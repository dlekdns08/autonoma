"use client";

/**
 * Pose Editor 3D scene — VRM with overlaid bone handles, two IK target
 * spheres for the wrists, an OrbitControls cam, and a single
 * TransformControls gizmo bound to whatever the operator has selected.
 *
 *   Selection model (one-of):
 *     - null              → no gizmo, just the VRM
 *     - { fk: bone }      → rotate gizmo on the bone (FK)
 *     - { ik: 'L'|'R' }   → translate gizmo on the wrist target sphere;
 *                           dragging runs solveTwoBoneIK on that arm
 *
 * IK target meshes use callback refs that store the THREE.Mesh into
 * parent state. React 19's compiler refuses to let consumers read
 * ``ref.current`` during render, so the meshes have to live in state to
 * be safely passed to ``ActiveGizmo``.
 */

import { Canvas, useFrame, useLoader } from "@react-three/fiber";
import { OrbitControls, TransformControls } from "@react-three/drei";
import {
  VRM,
  VRMLoaderPlugin,
  VRMUtils,
} from "@pixiv/three-vrm";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { Suspense, useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import {
  adjustVrmRestToArmsDown,
  collectMocapBones,
  type MocapBoneMap,
} from "@/lib/mocap/vrmShared";
import type { MocapBone } from "@/lib/mocap/clipFormat";
import { solveTwoBoneIK } from "@/lib/poseEditor/ik";
import BoneHandles from "./BoneHandles";

export type Selection =
  | null
  | { kind: "fk"; bone: MocapBone }
  | { kind: "ik"; side: "L" | "R" };

interface SceneProps {
  vrmFile: string;
  selectableBones: readonly MocapBone[];
  selection: Selection;
  onSelectionChange: (s: Selection) => void;
  /** Fired whenever the resolved bone map changes (VRM load / change).
   *  Parent uses it for save/reset against the same bone references the
   *  gizmo / IK mutate. */
  onBonesReady: (m: MocapBoneMap) => void;
  /** Bumped by the parent's "IK 타깃 동기화" button to request the IK
   *  spheres jump back to the current wrist world positions. */
  ikSyncToken: number;
}

function VrmInner({
  url,
  onBonesReady,
  ikSyncToken,
  leftMesh,
  rightMesh,
}: {
  url: string;
  onBonesReady: (m: MocapBoneMap, vrm: VRM) => void;
  ikSyncToken: number;
  leftMesh: THREE.Mesh | null;
  rightMesh: THREE.Mesh | null;
}) {
  const gltf = useLoader(GLTFLoader, url, (loader) => {
    (loader as GLTFLoader).register((p) => new VRMLoaderPlugin(p));
  });
  const vrm = (gltf as unknown as GLTF & { userData: { vrm: VRM } }).userData.vrm;
  const [bones, setBones] = useState<MocapBoneMap>({});

  useEffect(() => {
    if (!vrm) return;
    VRMUtils.rotateVRM0(vrm);
    adjustVrmRestToArmsDown(vrm);
    const map = collectMocapBones(vrm);
    setBones(map);
    onBonesReady(map, vrm);
    vrm.scene.traverse((o) => {
      o.frustumCulled = false;
    });
    vrm.update(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vrm]);

  // Snap IK target meshes to the wrist world positions. Re-runs on:
  //   - bones resolve (initial placement)
  //   - mesh mount (covers the case where IKTarget commits after VRM)
  //   - operator clicks "IK 타깃 동기화" (ikSyncToken bump)
  useEffect(() => {
    if (!vrm) return;
    const left = bones.leftHand;
    const right = bones.rightHand;
    if (left && leftMesh) left.getWorldPosition(leftMesh.position);
    if (right && rightMesh) right.getWorldPosition(rightMesh.position);
  }, [vrm, bones, leftMesh, rightMesh, ikSyncToken]);

  // VRM frame update — needed for spring bones / look-at, even though
  // we're not driving the humanoid from a sample.
  useFrame((_, delta) => {
    if (vrm) vrm.update(delta);
  });

  return <primitive object={vrm.scene} />;
}

function IKTarget({
  meshRef,
  side,
  selected,
  onSelect,
}: {
  meshRef: (m: THREE.Mesh | null) => void;
  side: "L" | "R";
  selected: boolean;
  onSelect: () => void;
}) {
  const color = side === "L" ? "#f97316" : "#a855f7";
  return (
    <mesh
      ref={meshRef}
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      onPointerOver={() => {
        document.body.style.cursor = "grab";
      }}
      onPointerOut={() => {
        document.body.style.cursor = "auto";
      }}
      renderOrder={1000}
    >
      <boxGeometry args={[0.05, 0.05, 0.05]} />
      <meshBasicMaterial
        color={color}
        depthTest={false}
        transparent
        opacity={selected ? 0.95 : 0.6}
      />
    </mesh>
  );
}

function ActiveGizmo({
  selection,
  bones,
  leftMesh,
  rightMesh,
}: {
  selection: Selection;
  bones: MocapBoneMap;
  leftMesh: THREE.Mesh | null;
  rightMesh: THREE.Mesh | null;
}) {
  if (!selection) return null;

  if (selection.kind === "fk") {
    const bone = bones[selection.bone];
    if (!bone) return null;
    return (
      <TransformControls
        object={bone}
        mode="rotate"
        size={0.6}
        space="local"
      />
    );
  }

  const targetMesh = selection.side === "L" ? leftMesh : rightMesh;
  if (!targetMesh) return null;

  const root =
    selection.side === "L" ? bones.leftUpperArm : bones.rightUpperArm;
  const mid =
    selection.side === "L" ? bones.leftLowerArm : bones.rightLowerArm;
  const end = selection.side === "L" ? bones.leftHand : bones.rightHand;

  const handleChange = () => {
    if (!root || !mid || !end) return;
    solveTwoBoneIK({
      root,
      mid,
      end,
      targetWorld: targetMesh.position,
    });
  };

  return (
    <TransformControls
      object={targetMesh}
      mode="translate"
      size={0.7}
      onObjectChange={handleChange}
    />
  );
}

export default function PoseEditorScene({
  vrmFile,
  selectableBones,
  selection,
  onSelectionChange,
  onBonesReady,
  ikSyncToken,
}: SceneProps) {
  const url = useMemo(() => `/vrm/${vrmFile}`, [vrmFile]);
  const [leftMesh, setLeftMesh] = useState<THREE.Mesh | null>(null);
  const [rightMesh, setRightMesh] = useState<THREE.Mesh | null>(null);
  const [bones, setBones] = useState<MocapBoneMap>({});

  const handleBones = (m: MocapBoneMap) => {
    setBones(m);
    onBonesReady(m);
  };

  return (
    <div className="aspect-[3/4] overflow-hidden rounded-lg border border-white/10 bg-slate-950/60">
      <Canvas
        dpr={[1, 1.75]}
        flat
        camera={{ fov: 28, position: [0, 1.25, 2.4], near: 0.1, far: 10 }}
        gl={{ alpha: true, premultipliedAlpha: false }}
        onCreated={({ gl }) => gl.setClearColor(0x000000, 0)}
        onPointerMissed={() => onSelectionChange(null)}
      >
        <ambientLight intensity={0.7} />
        <directionalLight position={[1, 2, 1.5]} intensity={1.2} />
        <directionalLight position={[-1.5, 1.2, -1]} intensity={0.35} />
        <Suspense fallback={null}>
          <VrmInner
            url={url}
            onBonesReady={handleBones}
            ikSyncToken={ikSyncToken}
            leftMesh={leftMesh}
            rightMesh={rightMesh}
          />
          <BoneHandles
            bones={bones}
            selectableBones={selectableBones}
            selected={selection?.kind === "fk" ? selection.bone : null}
            onSelect={(b) => onSelectionChange({ kind: "fk", bone: b })}
          />
          <IKTarget
            meshRef={setLeftMesh}
            side="L"
            selected={selection?.kind === "ik" && selection.side === "L"}
            onSelect={() => onSelectionChange({ kind: "ik", side: "L" })}
          />
          <IKTarget
            meshRef={setRightMesh}
            side="R"
            selected={selection?.kind === "ik" && selection.side === "R"}
            onSelect={() => onSelectionChange({ kind: "ik", side: "R" })}
          />
          <ActiveGizmo
            selection={selection}
            bones={bones}
            leftMesh={leftMesh}
            rightMesh={rightMesh}
          />
        </Suspense>
        <OrbitControls
          target={[0, 1.25, 0]}
          enablePan={false}
          enableDamping
          dampingFactor={0.12}
          minDistance={1.2}
          maxDistance={4}
          minPolarAngle={Math.PI * 0.2}
          maxPolarAngle={Math.PI * 0.82}
          makeDefault
        />
      </Canvas>
    </div>
  );
}

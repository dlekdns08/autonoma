"use client";

/**
 * Live VRM preview driven by a ``ClipSample`` ref (either the recorder's
 * output or a ``ClipRuntime`` sampling a saved clip). No procedural
 * idle / gestures — this component is strictly a passthrough so the
 * operator can see exactly what was captured.
 *
 * The caller owns the sample buffer; we read it every frame inside
 * ``useFrame``. Because the buffer mutates in place, we never pass it
 * through React state — doing so would either force a re-render per
 * frame or miss updates entirely.
 *
 * Finger-axis diagnostic: when ``testFingerAxis`` is set, ``useFrame``
 * writes a fixed 60° rotation around that axis to every finger proximal
 * — bypassing the solver entirely. The operator eyeballs which axis
 * actually bends the fingers inward; that's the curl axis the solver
 * should use. The override disables itself after ``testFingerUntil``
 * milliseconds (set by the caller), so the test is time-boxed.
 */

import { Canvas, useFrame, useLoader } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { VRM, VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { Suspense, useEffect, useMemo, useRef } from "react";
import type { RefObject } from "react";
import * as THREE from "three";
import {
  collectMocapBones,
  countResolvedBones,
  applyBoneSampleAll,
  applyExpressionSample,
  type MocapBoneMap,
} from "@/lib/mocap/vrmShared";
import type { ClipSample } from "@/lib/mocap/clipPlayer";
import { FINGER_BONE_SET, type MocapBone } from "@/lib/mocap/clipFormat";

/** Finger-axis test — axis the caller wants to force. Null = no test. */
export type FingerTestAxis = "x" | "y" | "z" | null;

interface Props {
  vrmFile: string;
  sampleRef: RefObject<ClipSample>;
  /** If set, force a 60° rotation on all finger proximals around this
   *  axis (ignoring the sample) until ``testFingerUntil`` passes. */
  testFingerAxis?: FingerTestAxis;
  testFingerUntil?: number;
  /** VRM filename the sample was recorded against. When this differs
   *  from ``vrmFile``, finger-bone tracks are suppressed (cross-rig
   *  finger curl axes don't line up). Omit for live-capture previews
   *  where the sample is always native by construction. */
  sampleSourceVrm?: string;
}

const FINGER_PROXIMALS: readonly MocapBone[] = [
  "leftThumbProximal",
  "leftIndexProximal",
  "leftMiddleProximal",
  "leftRingProximal",
  "leftLittleProximal",
  "rightThumbProximal",
  "rightIndexProximal",
  "rightMiddleProximal",
  "rightRingProximal",
  "rightLittleProximal",
];

const TEST_ANGLE_RAD = -Math.PI / 3; // -60°; negative = "inward" guess

// Scratch objects reused by the finger-axis test harness. Kept
// module-scoped so the per-frame override stays allocation-free.
const _testEuler = new THREE.Euler();
const _testQ = new THREE.Quaternion();

function PreviewVRM({
  url,
  sampleRef,
  testFingerAxis,
  testFingerUntil,
  skipFingers,
}: {
  url: string;
  sampleRef: RefObject<ClipSample>;
  testFingerAxis?: FingerTestAxis;
  testFingerUntil?: number;
  /** When true, finger bones aren't written from the sample — used for
   *  cross-rig clip playback where finger curl axes may mismatch. */
  skipFingers: boolean;
}) {
  const gltf = useLoader(GLTFLoader, url, (loader) => {
    (loader as GLTFLoader).register((p) => new VRMLoaderPlugin(p));
  });
  const vrm = (gltf as unknown as GLTF & { userData: { vrm: VRM } }).userData.vrm;
  const bonesRef = useRef<MocapBoneMap>({});

  useEffect(() => {
    if (!vrm) return;
    VRMUtils.rotateVRM0(vrm);
    bonesRef.current = collectMocapBones(vrm);
    // One-time diagnostic: log which MOCAP_BONES this rig actually
    // exposes. Finger capture silently no-ops when the rig omits
    // finger humanoid bones (some minimalist VRMs do); this log makes
    // that failure mode discoverable in the browser console.
    const stats = countResolvedBones(bonesRef.current);
    console.info(
      `[mocap] VRM resolved ${stats.resolved}/${stats.total} bones`
        + (stats.fallbacks.length
          ? ` (${stats.fallbacks.length} via scene fallback)`
          : "")
        + (stats.missing.length ? ` — missing:` : ""),
      stats.missing.length ? stats.missing : "",
    );
    vrm.scene.traverse((o) => {
      o.frustumCulled = false;
    });
  }, [vrm]);

  useFrame((_, delta) => {
    if (!vrm) return;
    const sample = sampleRef.current;
    if (sample) {
      applyBoneSampleAll(
        bonesRef.current,
        sample,
        skipFingers ? FINGER_BONE_SET : undefined,
      );
      applyExpressionSample(vrm, sample);
    }
    // Finger-axis diagnostic: override the sample's finger rotations
    // with a fixed rotation around the tested axis so the operator can
    // see which axis actually bends the finger inward on THIS rig.
    if (
      testFingerAxis &&
      testFingerUntil &&
      performance.now() < testFingerUntil
    ) {
      _testEuler.set(
        testFingerAxis === "x" ? TEST_ANGLE_RAD : 0,
        testFingerAxis === "y" ? TEST_ANGLE_RAD : 0,
        testFingerAxis === "z" ? TEST_ANGLE_RAD : 0,
        "XYZ",
      );
      _testQ.setFromEuler(_testEuler);
      for (const name of FINGER_PROXIMALS) {
        const bone = bonesRef.current[name];
        if (!bone) continue;
        const rest = bone.userData?.mocapRest as THREE.Quaternion | undefined;
        if (rest) {
          bone.quaternion.copy(rest).multiply(_testQ);
        } else {
          bone.quaternion.copy(_testQ);
        }
      }
    }
    vrm.update(delta);
  });

  return <primitive object={vrm.scene} />;
}

export default function MocapPreview({
  vrmFile,
  sampleRef,
  testFingerAxis,
  testFingerUntil,
  sampleSourceVrm,
}: Props) {
  const url = useMemo(() => `/vrm/${vrmFile}`, [vrmFile]);
  // Suppress finger tracks when the sample came from a different rig —
  // finger local axes are the main cross-rig failure mode. When the
  // caller omits ``sampleSourceVrm`` (live-capture previews), this is
  // always false.
  const skipFingers =
    !!sampleSourceVrm && sampleSourceVrm !== "" && sampleSourceVrm !== vrmFile;
  return (
    <div className="aspect-[3/4] overflow-hidden rounded-lg border border-white/10 bg-slate-950/60">
      <Canvas
        dpr={[1, 1.75]}
        flat
        camera={{ fov: 28, position: [0, 1.25, 2.4], near: 0.1, far: 10 }}
        gl={{ alpha: true, premultipliedAlpha: false }}
        onCreated={({ gl }) => gl.setClearColor(0x000000, 0)}
      >
        <ambientLight intensity={0.7} />
        <directionalLight position={[1, 2, 1.5]} intensity={1.2} />
        <directionalLight position={[-1.5, 1.2, -1]} intensity={0.35} />
        <Suspense fallback={null}>
          <PreviewVRM
            url={url}
            sampleRef={sampleRef}
            testFingerAxis={testFingerAxis}
            testFingerUntil={testFingerUntil}
            skipFingers={skipFingers}
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

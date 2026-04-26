"use client";

/**
 * Clickable joint markers overlaid on the VRM. Each handle is a small
 * always-on-top sphere whose world position tracks its bone every frame
 * — clicking selects the bone for the FK rotation gizmo.
 *
 * depthTest=false + renderOrder=999 keeps handles visible through hair
 * and clothing meshes; otherwise long-haired VRMs hide the head/neck
 * markers entirely.
 */

import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import * as THREE from "three";
import type { MocapBone } from "@/lib/mocap/clipFormat";
import type { MocapBoneMap } from "@/lib/mocap/vrmShared";

const _wp = new THREE.Vector3();

interface Props {
  bones: MocapBoneMap;
  selectableBones: readonly MocapBone[];
  selected: MocapBone | null;
  onSelect: (b: MocapBone) => void;
}

export default function BoneHandles({
  bones,
  selectableBones,
  selected,
  onSelect,
}: Props) {
  const refs = useRef<Map<MocapBone, THREE.Mesh>>(new Map());

  useFrame(() => {
    for (const name of selectableBones) {
      const bone = bones[name];
      const mesh = refs.current.get(name);
      if (!bone || !mesh) continue;
      bone.getWorldPosition(_wp);
      mesh.position.copy(_wp);
    }
  });

  return (
    <group>
      {selectableBones.map((name) => {
        if (!bones[name]) return null;
        const isSel = selected === name;
        return (
          <mesh
            key={name}
            ref={(m) => {
              if (m) refs.current.set(name, m);
              else refs.current.delete(name);
            }}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(name);
            }}
            onPointerOver={() => {
              document.body.style.cursor = "pointer";
            }}
            onPointerOut={() => {
              document.body.style.cursor = "auto";
            }}
            renderOrder={999}
          >
            <sphereGeometry args={[isSel ? 0.028 : 0.018, 12, 12]} />
            <meshBasicMaterial
              color={isSel ? "#fde047" : "#22d3ee"}
              depthTest={false}
              transparent
              opacity={0.9}
            />
          </mesh>
        );
      })}
    </group>
  );
}

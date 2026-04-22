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
 */

import { Canvas, useFrame, useLoader } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { VRM, VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { Suspense, useEffect, useMemo, useRef } from "react";
import type { RefObject } from "react";
import {
  collectMocapBones,
  countResolvedBones,
  applyBoneSampleAll,
  applyExpressionSample,
  type MocapBoneMap,
} from "@/lib/mocap/vrmShared";
import type { ClipSample } from "@/lib/mocap/clipPlayer";

interface Props {
  vrmFile: string;
  sampleRef: RefObject<ClipSample>;
}

function PreviewVRM({ url, sampleRef }: { url: string; sampleRef: RefObject<ClipSample> }) {
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
      applyBoneSampleAll(bonesRef.current, sample);
      applyExpressionSample(vrm, sample);
    }
    vrm.update(delta);
  });

  return <primitive object={vrm.scene} />;
}

export default function MocapPreview({ vrmFile, sampleRef }: Props) {
  const url = useMemo(() => `/vrm/${vrmFile}`, [vrmFile]);
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
          <PreviewVRM url={url} sampleRef={sampleRef} />
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

"use client";

/**
 * Three.js VRM character renderer.
 *
 *   <VRMCharacter agent={…} getMouthAmplitude={…} spotlight />
 *
 * Each agent is mapped deterministically to one of the .vrm assets in
 * `public/vrm/` via `vrmFileForAgent`, so an agent named "Architect"
 * always resolves to the same model across sessions and across viewers.
 *
 * Lip-sync:
 *   - The caller passes `getMouthAmplitude(name)` — the same 0..1 envelope
 *     the procedural SVG face used. Inside the r3f render loop we pull it
 *     every frame and push it into the "aa" expression, which is the VRM
 *     standard mouth-open blendshape. We deliberately don't try to
 *     distinguish vowels (ih / ou / ee / oh): the audio is arbitrary TTS
 *     output and running a formant analyzer per-agent would cost more
 *     than the added realism is worth.
 *
 * Blink + mood:
 *   - Blink is a tiny state machine driven from the render loop, with a
 *     per-agent phase offset (derived from the agent name) so a group of
 *     agents doesn't blink in lockstep.
 *   - `agent.mood` maps to one of the VRM standard emote expressions
 *     (happy/angry/sad/relaxed/surprised). We ease current → target each
 *     frame rather than snapping, so mood transitions read naturally.
 *
 * Camera:
 *   - `spotlight` prop switches between a close head-and-shoulders frame
 *     and a wider full-body frame for the gallery thumbnails. The camera
 *     looks at the VRM's head bone (which is in humanoid meters, so the
 *     target Y is consistent across models of different heights).
 */

import { Canvas, useFrame, useLoader, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import {
  VRM,
  VRMLoaderPlugin,
  VRMUtils,
} from "@pixiv/three-vrm";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { Suspense, useEffect, useMemo, useRef, type ComponentRef } from "react";
import * as THREE from "three";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { AgentData } from "@/lib/types";
import { vrmFileForAgent } from "./vrmCredits";

interface Props {
  agent: AgentData;
  /** Same amplitude feed the SVG face used — 0..1, sampled per frame. */
  getMouthAmplitude?: (name: string) => number;
  /** Spotlight = full-body with orbit controls; otherwise = static thumb. */
  spotlight?: boolean;
  /** Outer-div click handler so the whole tile is interactive. */
  onClick?: () => void;
  /** Bump this integer to snap the camera back to the default framing.
   *  Used by the parent "reset view" button so the user can recover
   *  after an accidental orbit. */
  cameraResetNonce?: number;
}

// ── Mood → VRM standard emote ────────────────────────────────────────
//
// VRM 1.0 defines five standard emotion expressions: happy, angry, sad,
// relaxed, surprised. We map agent mood strings to these with a target
// intensity; the render loop eases toward it rather than snapping.

interface MoodTarget {
  happy?: number;
  angry?: number;
  sad?: number;
  relaxed?: number;
  surprised?: number;
}

const MOOD_MAP: Record<string, MoodTarget> = {
  happy: { happy: 0.75 },
  excited: { happy: 0.9, surprised: 0.3 },
  proud: { happy: 0.5, relaxed: 0.2 },
  frustrated: { angry: 0.7 },
  worried: { sad: 0.6, surprised: 0.15 },
  relaxed: { relaxed: 0.6 },
  determined: { angry: 0.3 },
  focused: { relaxed: 0.2 },
};

const EMOTE_KEYS: (keyof MoodTarget)[] = [
  "happy",
  "angry",
  "sad",
  "relaxed",
  "surprised",
];

// Deterministic blink phase from agent name — keeps the whole cast from
// blinking together. djb2, same family as `vrmFileForAgent` so debugging
// in the devtools matches.
function blinkOffsetForName(name: string): number {
  let h = 5381;
  for (let i = 0; i < name.length; i++) {
    h = ((h << 5) + h + name.charCodeAt(i)) >>> 0;
  }
  // 0..4 seconds offset.
  return (h % 4000) / 1000;
}

// ── Inner component: loaded VRM bound to the animation loop ──────────

interface ModelProps {
  url: string;
  agentName: string;
  mood: string;
  getMouthAmplitude?: (name: string) => number;
  spotlight: boolean;
  cameraResetNonce?: number;
}

function VRMModel({
  url,
  agentName,
  mood,
  getMouthAmplitude,
  spotlight,
  cameraResetNonce,
}: ModelProps) {
  const { camera } = useThree();
  const controlsRef = useRef<ComponentRef<typeof OrbitControls>>(null);
  const gltf = useLoader(GLTFLoader, url, (loader) => {
    // r3f's cache keys on URL alone, so this factory is invoked once per
    // distinct VRM file — we register the VRM parser plugin here.
    const l = loader as GLTFLoader;
    l.register((parser) => new VRMLoaderPlugin(parser));
  });

  // The plugin attaches the parsed VRM onto gltf.userData.vrm — the
  // GLTF type doesn't know about that extension, so go through unknown.
  const vrm = (gltf as unknown as GLTF & { userData: { vrm: VRM } })
    .userData.vrm;

  // One-time scene prep: older (VRM 0.x) models point -Z, modern (1.0)
  // models point +Z. `rotateVRM0` is a no-op on 1.0 so calling it
  // unconditionally is safe.
  useEffect(() => {
    if (!vrm) return;
    VRMUtils.rotateVRM0(vrm);
    // Drop the default T-pose arms a little so the idle frame doesn't
    // look like a crucifixion. We nudge upper arms inward via humanoid
    // bones when available.
    const leftUpper = vrm.humanoid?.getNormalizedBoneNode("leftUpperArm");
    const rightUpper = vrm.humanoid?.getNormalizedBoneNode("rightUpperArm");
    if (leftUpper) leftUpper.rotation.z = 1.15;
    if (rightUpper) rightUpper.rotation.z = -1.15;
    // Disable any leftover frustum culling weirdness on morphed meshes.
    vrm.scene.traverse((o) => {
      o.frustumCulled = false;
    });
    return () => {
      // r3f caches the loaded gltf; we don't fully dispose here because
      // the same VRM may be reused by another mount. VRMUtils.deepDispose
      // would run only on full app teardown.
    };
  }, [vrm]);

  // Blink + mood + mouth state lives in refs so we don't re-render each
  // frame — the changes are all inside the WebGL scene.
  const stateRef = useRef({
    blinkNextAt: performance.now() / 1000 + blinkOffsetForName(agentName),
    blinking: false,
    blinkT: 0,
    smoothMood: { happy: 0, angry: 0, sad: 0, relaxed: 0, surprised: 0 },
    lookBias: Math.random() * Math.PI * 2,
  });

  useFrame((_, delta) => {
    if (!vrm) return;

    const em = vrm.expressionManager;

    // ── Mouth (aa) ───────────────────────────────────────────────────
    if (em && getMouthAmplitude) {
      const raw = getMouthAmplitude(agentName);
      // Clamp to 0..1; the audio analyzer already smooths so we don't
      // double-smooth here.
      em.setValue("aa", Math.min(1, raw * 1.2));
    }

    // ── Mood emotes ──────────────────────────────────────────────────
    if (em) {
      const target = MOOD_MAP[mood] ?? {};
      const s = stateRef.current.smoothMood;
      // Ease at ~4/sec so a mood flip takes ~250ms to settle.
      const k = 1 - Math.exp(-delta * 4);
      for (const key of EMOTE_KEYS) {
        const t = target[key] ?? 0;
        s[key] += (t - s[key]) * k;
        em.setValue(key, s[key]);
      }
    }

    // ── Blink ────────────────────────────────────────────────────────
    const now = performance.now() / 1000;
    const st = stateRef.current;
    if (!st.blinking && now >= st.blinkNextAt) {
      st.blinking = true;
      st.blinkT = 0;
    }
    if (st.blinking && em) {
      // Closed fraction is a triangle wave over 0..2 (close then open).
      st.blinkT += delta * 10;
      const t = st.blinkT;
      const v = t < 1 ? t : t < 2 ? 2 - t : 0;
      em.setValue("blink", Math.max(0, Math.min(1, v)));
      if (t >= 2) {
        st.blinking = false;
        // Next blink 2.5–5.5s out.
        st.blinkNextAt = now + 2.5 + Math.random() * 3;
      }
    }

    // ── Subtle head sway so the pose isn't frozen ────────────────────
    const head = vrm.humanoid?.getNormalizedBoneNode("head");
    if (head) {
      head.rotation.y = Math.sin(now * 0.6 + st.lookBias) * 0.06;
      head.rotation.x = Math.sin(now * 0.4 + st.lookBias) * 0.03;
    }

    vrm.update(delta);
  });

  // Initial / reset framing. We compute a body center from the head and
  // hips bones *once* — not every frame — so OrbitControls can take over
  // without a per-frame setter stealing the camera back from the user.
  // The effect re-runs when `cameraResetNonce` bumps, giving the parent
  // a way to snap back to defaults after the viewer has orbited.
  useEffect(() => {
    if (!vrm) return;
    const humanoid = vrm.humanoid;
    if (!humanoid) return;
    const head = humanoid.getNormalizedBoneNode("head");
    const hips = humanoid.getNormalizedBoneNode("hips");
    if (!head || !hips) return;
    const headPos = new THREE.Vector3();
    const hipsPos = new THREE.Vector3();
    head.getWorldPosition(headPos);
    hips.getWorldPosition(hipsPos);

    const centerX = (headPos.x + hipsPos.x) / 2;
    const centerY = hipsPos.y + (headPos.y - hipsPos.y) * 0.55;
    const centerZ = (headPos.z + hipsPos.z) / 2;

    // Distance: tighter in spotlight (you're "looking at" the character),
    // wider for gallery tiles so everyone fits.
    const distance = spotlight ? 2.4 : 2.8;
    camera.position.set(centerX, centerY + 0.05, centerZ + distance);
    camera.lookAt(centerX, centerY - 0.05, centerZ);

    // Tell OrbitControls where to pivot. Without this the controls would
    // orbit around the origin, which for VRoid models puts the pivot at
    // the character's feet and makes rotation feel wildly off.
    if (controlsRef.current) {
      controlsRef.current.target.set(centerX, centerY - 0.05, centerZ);
      controlsRef.current.update();
    }
  }, [vrm, spotlight, cameraResetNonce, camera]);

  return (
    <>
      <primitive object={vrm.scene} />
      {/* Orbit controls only in the spotlight view. Gallery thumbs are
          too small to be usefully interactive and would just eat touch
          events meant for the tile's onClick. */}
      {spotlight && (
        <OrbitControls
          ref={controlsRef}
          enablePan={false}
          enableDamping
          dampingFactor={0.12}
          rotateSpeed={0.8}
          zoomSpeed={0.8}
          // Prevent the viewer from zooming inside the mesh or so far
          // out that the character becomes a dot.
          minDistance={1.1}
          maxDistance={5.5}
          // Keep the camera roughly level — disallow looking at the
          // character from directly overhead or underneath, both of
          // which expose ugly rigging seams.
          minPolarAngle={Math.PI * 0.2}
          maxPolarAngle={Math.PI * 0.82}
          makeDefault
        />
      )}
    </>
  );
}

// ── Outer component: Canvas + lighting + fallback ─────────────────────

export default function VRMCharacter({
  agent,
  getMouthAmplitude,
  spotlight = false,
  onClick,
}: Props) {
  const file = useMemo(() => vrmFileForAgent(agent.name), [agent.name]);
  const url = `/vrm/${file}`;

  return (
    <div
      className="relative h-full w-full overflow-hidden"
      onClick={onClick}
      style={{ cursor: onClick ? "pointer" : "default" }}
    >
      <Canvas
        // `dpr` bounded so a Retina display doesn't hammer the GPU on 10
        // simultaneous Canvases.
        dpr={[1, spotlight ? 2 : 1.25]}
        // `flat` keeps colors close to the VRM's texture intent instead
        // of applying tone mapping.
        flat
        camera={{ fov: spotlight ? 30 : 32, near: 0.1, far: 10 }}
      >
        {/* Soft three-point: key, fill, rim. Matches the anime-ish flat
            look most VRoid models are authored for. */}
        <ambientLight intensity={0.75} />
        <directionalLight position={[1.2, 2, 1.5]} intensity={1.2} />
        <directionalLight position={[-1.5, 1.2, -1]} intensity={0.4} />
        <Suspense fallback={null}>
          <VRMModel
            url={url}
            agentName={agent.name}
            mood={agent.mood}
            getMouthAmplitude={getMouthAmplitude}
            spotlight={spotlight}
          />
        </Suspense>
      </Canvas>
    </div>
  );
}

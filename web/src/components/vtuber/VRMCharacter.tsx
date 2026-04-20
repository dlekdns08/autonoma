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

import { Canvas, useFrame, useLoader } from "@react-three/fiber";
import {
  VRM,
  VRMLoaderPlugin,
  VRMUtils,
} from "@pixiv/three-vrm";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { Suspense, useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { AgentData } from "@/lib/types";
import { vrmFileForAgent } from "./vrmCredits";

interface Props {
  agent: AgentData;
  /** Same amplitude feed the SVG face used — 0..1, sampled per frame. */
  getMouthAmplitude?: (name: string) => number;
  /** Spotlight = close-up head+shoulders; otherwise = wider full-body. */
  spotlight?: boolean;
  /** Outer-div click handler so the whole tile is interactive. */
  onClick?: () => void;
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
}

function VRMModel({
  url,
  agentName,
  mood,
  getMouthAmplitude,
  spotlight,
}: ModelProps) {
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

  // Full-body framing. We compute a body center from the head and hips
  // bones each render — model heights differ so we can't hard-code Y.
  // The spotlight frame is slightly tighter on the upper body; the
  // gallery frame is a touch wider to fit everyone in a small tile.
  useFrame(({ camera }) => {
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

    // Approximate full-body center. We bias slightly upward from the
    // hips — this keeps the chest/face area in the optical center where
    // the viewer's eye lands first, rather than landing on the stomach.
    const centerX = (headPos.x + hipsPos.x) / 2;
    const centerY = hipsPos.y + (headPos.y - hipsPos.y) * 0.55;
    const centerZ = (headPos.z + hipsPos.z) / 2;

    if (spotlight) {
      // Close enough to see facial expressions, far enough to catch hands.
      camera.position.set(centerX, centerY + 0.05, centerZ + 2.4);
      camera.lookAt(centerX, centerY - 0.05, centerZ);
    } else {
      // Gallery tile: step back a hair, tilt down slightly.
      camera.position.set(centerX, centerY + 0.1, centerZ + 2.8);
      camera.lookAt(centerX, centerY - 0.1, centerZ);
    }
  });

  return <primitive object={vrm.scene} />;
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

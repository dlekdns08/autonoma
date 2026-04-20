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
  /** Agent behavioral state: "idle" | "working" | "talking" | "thinking" | "celebrating" etc. */
  state?: string;
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

// Agent state → expression overlay, blended on top of mood blendshapes.
const STATE_EXPRESSION_BOOST: Record<string, Partial<MoodTarget>> = {
  celebrating: { happy: 0.25, surprised: 0.2 },
  working:     { relaxed: 0.1 },
  talking:     { happy: 0.1 },
  thinking:    { relaxed: 0.15 },
};

const EMOTE_KEYS: (keyof MoodTarget)[] = [
  "happy",
  "angry",
  "sad",
  "relaxed",
  "surprised",
];

// ── Procedural gesture system ────────────────────────────────────────
//
// Each gesture is a duration + a function that ADDS rotations on top of
// the idle pose. Because the idle loop rewrites every tracked bone each
// frame, gestures only need to be additive — when the clip ends we just
// drop the reference and the next frame's idle pose takes over cleanly,
// no "return to rest" interpolation required.
//
// `env` is a 0 → 1 → 0 envelope the caller feeds in, so gestures start
// and end gently instead of snapping. All rotation magnitudes are tuned
// against the VRoid-style rigs in `public/vrm/`; different rigs may
// need a second pass.

type GestureName = "wave" | "hype" | "think" | "bow";

interface Bones {
  head: THREE.Object3D | null;
  hips: THREE.Object3D | null;
  chest: THREE.Object3D | null;
  leftUpperArm: THREE.Object3D | null;
  rightUpperArm: THREE.Object3D | null;
  leftLowerArm: THREE.Object3D | null;
  rightLowerArm: THREE.Object3D | null;
}

const GESTURES: Record<
  GestureName,
  { duration: number; apply: (t: number, b: Bones, env: number) => void }
> = {
  wave: {
    duration: 1.4,
    apply: (t, b, env) => {
      // Right arm raises; forearm oscillates — classic greeting.
      // rightUpperArm base is -1.15 (arm at side), so adding positive
      // z brings the hand up and away from the torso.
      if (b.rightUpperArm) {
        b.rightUpperArm.rotation.z += env * 1.5;
        b.rightUpperArm.rotation.x -= env * 0.35;
      }
      if (b.rightLowerArm) {
        b.rightLowerArm.rotation.y +=
          env * Math.sin(t * Math.PI * 5) * 0.45;
      }
    },
  },
  hype: {
    duration: 0.95,
    apply: (_t, b, env) => {
      // Both arms up briefly — the "YES!!" moment.
      if (b.leftUpperArm) {
        b.leftUpperArm.rotation.z -= env * 1.55;
        b.leftUpperArm.rotation.x -= env * 0.2;
      }
      if (b.rightUpperArm) {
        b.rightUpperArm.rotation.z += env * 1.55;
        b.rightUpperArm.rotation.x -= env * 0.2;
      }
    },
  },
  think: {
    duration: 1.8,
    apply: (_t, b, env) => {
      // Right hand to chin with a small head tilt — reads as "hmm".
      if (b.rightUpperArm) {
        b.rightUpperArm.rotation.z += env * 0.85;
        b.rightUpperArm.rotation.x -= env * 0.55;
      }
      if (b.rightLowerArm) {
        b.rightLowerArm.rotation.z += env * 1.0;
      }
      if (b.head) {
        b.head.rotation.z -= env * 0.08;
      }
    },
  },
  bow: {
    duration: 1.4,
    apply: (_t, b, env) => {
      // Forward spine flex with matching head dip.
      if (b.chest) b.chest.rotation.x += env * 0.28;
      if (b.head) b.head.rotation.x += env * 0.12;
    },
  },
};

// Mood → gesture options. Multiple options = random pick so the same
// mood doesn't always play the exact same clip.
const MOOD_GESTURE_OPTIONS: Partial<Record<string, GestureName[]>> = {
  excited:     ["hype", "wave"],
  proud:       ["wave", "bow"],
  worried:     ["think"],
  happy:       ["wave", "bow"],
  determined:  ["hype"],
  focused:     ["think"],
  celebrating: ["hype", "wave"],
};

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
    const h = vrm.humanoid;
    // Drop the default T-pose arms a little so the idle frame doesn't
    // look like a crucifixion. The idle loop adds a small oscillation
    // around these base angles.
    const leftUpper = h?.getNormalizedBoneNode("leftUpperArm") ?? null;
    const rightUpper = h?.getNormalizedBoneNode("rightUpperArm") ?? null;
    if (leftUpper) leftUpper.rotation.z = 1.15;
    if (rightUpper) rightUpper.rotation.z = -1.15;
    // Cache every bone the render loop touches. `chest` isn't guaranteed
    // on every VRM (some rigs only define upperChest or spine), so fall
    // through those in priority order rather than skipping breathing.
    // Lower arms drive gesture forearm flex (wave, think) — optional
    // since some minimalist rigs omit them.
    bonesRef.current = {
      head: h?.getNormalizedBoneNode("head") ?? null,
      hips: h?.getNormalizedBoneNode("hips") ?? null,
      chest:
        h?.getNormalizedBoneNode("chest") ??
        h?.getNormalizedBoneNode("upperChest") ??
        h?.getNormalizedBoneNode("spine") ??
        null,
      leftUpperArm: leftUpper,
      rightUpperArm: rightUpper,
      leftLowerArm: h?.getNormalizedBoneNode("leftLowerArm") ?? null,
      rightLowerArm: h?.getNormalizedBoneNode("rightLowerArm") ?? null,
    };
    // Gaze targeting: wire the VRM's lookAt system to track the camera
    // so eyes follow the viewer as they orbit. three-vrm reads the
    // target's world position each `vrm.update(delta)` call, so there's
    // nothing to do in the render loop.
    if (vrm.lookAt) {
      vrm.lookAt.target = camera;
    }
    // Disable any leftover frustum culling weirdness on morphed meshes.
    vrm.scene.traverse((o) => {
      o.frustumCulled = false;
    });
    return () => {
      // r3f caches the loaded gltf; we don't fully dispose here because
      // the same VRM may be reused by another mount. VRMUtils.deepDispose
      // would run only on full app teardown.
    };
  }, [vrm, camera]);

  // Mood-triggered gestures. When `mood` changes we schedule the
  // matching gesture (wave, hype, think, bow) after a small random
  // delay so a whole cast flipping to the same mood doesn't gesture in
  // lockstep. The cleanup cancels a pending fire if the component
  // unmounts mid-delay.
  useEffect(() => {
    const next = MOOD_GESTURES[mood];
    if (!next) return;
    const delay = Math.random() * 400;
    const timer = window.setTimeout(() => {
      stateRef.current.gesture = next;
      stateRef.current.gestureStart = performance.now() / 1000;
    }, delay);
    return () => window.clearTimeout(timer);
  }, [mood]);

  // Blink + mood + mouth state lives in refs so we don't re-render each
  // frame — the changes are all inside the WebGL scene.
  const stateRef = useRef({
    blinkNextAt: performance.now() / 1000 + blinkOffsetForName(agentName),
    blinking: false,
    blinkT: 0,
    smoothMood: { happy: 0, angry: 0, sad: 0, relaxed: 0, surprised: 0 },
    lookBias: Math.random() * Math.PI * 2,
    // Rising-edge detector on the mouth amplitude feeds the speech nod —
    // nodStart < 0 means inactive, otherwise it's the seconds-timestamp
    // the nod began at so the render loop can compute a decay curve.
    prevAmp: 0,
    nodStart: -1,
    // Currently-playing gesture. `null` when idle; otherwise the render
    // loop applies the gesture's additive transform until its duration
    // elapses.
    gesture: null as GestureName | null,
    gestureStart: 0,
  });

  // Bone lookups are cheap but non-zero — humanoid.getNormalizedBoneNode
  // walks a map on every call. Cache the handful we touch each frame so
  // the idle loop stays allocation-free.
  const bonesRef = useRef<Bones>({
    head: null,
    hips: null,
    chest: null,
    leftUpperArm: null,
    rightUpperArm: null,
    leftLowerArm: null,
    rightLowerArm: null,
  });

  useFrame((_, delta) => {
    if (!vrm) return;

    const em = vrm.expressionManager;
    const now = performance.now() / 1000;
    const st = stateRef.current;
    const bones = bonesRef.current;
    const phase = st.lookBias;

    // Amplitude drives both the mouth and the speech-nod trigger, so
    // sample it once per frame.
    const amp = getMouthAmplitude ? getMouthAmplitude(agentName) : 0;

    // ── Mouth (aa) ───────────────────────────────────────────────────
    if (em && getMouthAmplitude) {
      // Clamp to 0..1; the audio analyzer already smooths so we don't
      // double-smooth here.
      em.setValue("aa", Math.min(1, amp * 1.2));
    }

    // ── Mood emotes ──────────────────────────────────────────────────
    if (em) {
      const target = MOOD_MAP[mood] ?? {};
      const s = st.smoothMood;
      // Ease at ~4/sec so a mood flip takes ~250ms to settle.
      const k = 1 - Math.exp(-delta * 4);
      for (const key of EMOTE_KEYS) {
        const t = target[key] ?? 0;
        s[key] += (t - s[key]) * k;
        em.setValue(key, s[key]);
      }
    }

    // ── Blink ────────────────────────────────────────────────────────
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

    // ── Procedural idle — breathing, weight shift, arm sway ──────────
    // Driven by sin waves keyed on `phase` (per-agent) so the whole
    // cast doesn't inhale in unison. No external clip assets required
    // — keeps bundle size flat and avoids Mixamo/VRMA retarget edge
    // cases across rigs.
    if (bones.chest) {
      // ~14 BPM breathing as a gentle forward-back chest tilt.
      bones.chest.rotation.x = Math.sin(now * 1.5 + phase) * 0.015;
    }
    if (bones.hips) {
      // Slow lateral weight shift — reads like a standing idle.
      bones.hips.rotation.z = Math.sin(now * 0.3 + phase) * 0.022;
      bones.hips.rotation.y = Math.sin(now * 0.23 + phase * 1.3) * 0.015;
    }
    if (bones.leftUpperArm) {
      // Additive around the 1.15 base set at mount — arms drift rather
      // than snap, which is what makes the pose feel "alive".
      bones.leftUpperArm.rotation.z =
        1.15 + Math.sin(now * 0.8 + phase) * 0.03;
    }
    if (bones.rightUpperArm) {
      bones.rightUpperArm.rotation.z =
        -1.15 + Math.sin(now * 0.8 + phase + Math.PI) * 0.03;
    }

    // ── Speech-triggered head nod ────────────────────────────────────
    // When the mouth opens sharply, fire a small damped bow so the
    // character "punctuates" its sentence. Rising-edge detect keeps us
    // from retriggering every frame of a sustained loud phrase.
    if (amp > 0.18 && st.prevAmp <= 0.18 && st.nodStart < 0) {
      st.nodStart = now;
    }
    st.prevAmp = amp;
    let nodX = 0;
    if (st.nodStart >= 0) {
      const nt = now - st.nodStart;
      if (nt > 0.6) {
        st.nodStart = -1;
      } else {
        // Damped sine: peaks ~0.17s in, settles by ~0.6s. Negative
        // because forward-chin (downward) is the natural bow axis.
        nodX = -Math.sin(nt * Math.PI * 2) * Math.exp(-nt * 3.5) * 0.12;
      }
    }

    // ── Head sway + nod (combined) ───────────────────────────────────
    if (bones.head) {
      bones.head.rotation.y = Math.sin(now * 0.6 + phase) * 0.06;
      bones.head.rotation.x =
        Math.sin(now * 0.4 + phase) * 0.03 + nodX;
    }

    // ── Gesture playback ─────────────────────────────────────────────
    // Additive on top of the idle pose: breathing/sway/nod stay active,
    // the gesture just layers the arm-raise (wave / hype), hand-to-chin
    // (think), or spine flex (bow) for the gesture's duration.
    if (st.gesture) {
      const g = GESTURES[st.gesture];
      const nt = now - st.gestureStart;
      if (nt >= g.duration) {
        st.gesture = null;
      } else {
        const t = nt / g.duration;
        // Soft 0 → 1 → 0 envelope so the clip ramps in and out rather
        // than snapping a bone straight to its target.
        const env = Math.sin(t * Math.PI);
        g.apply(t, bones, env);
      }
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
  cameraResetNonce,
}: Props) {
  const file = useMemo(() => vrmFileForAgent(agent.name), [agent.name]);
  const url = `/vrm/${file}`;

  return (
    <div
      className="relative h-full w-full overflow-hidden"
      // Only the gallery tile should fire onClick on the whole area —
      // in spotlight the OrbitControls need the drag gestures and the
      // parent attaches click handlers to separate overlay elements.
      onClick={spotlight ? undefined : onClick}
      style={{
        cursor: spotlight ? "grab" : onClick ? "pointer" : "default",
      }}
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
            cameraResetNonce={cameraResetNonce}
          />
        </Suspense>
      </Canvas>
    </div>
  );
}

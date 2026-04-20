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
import type { AgentData, AgentEmote } from "@/lib/types";
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
  /** Current reaction emote from the pixel stage — triggers a matching gesture. */
  emote?: AgentEmote | null;
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
  idle:       { relaxed: 0.15 },
  happy:      { happy: 0.8 },
  excited:    { happy: 0.95, surprised: 0.4 },
  proud:      { happy: 0.55, relaxed: 0.25 },
  frustrated: { angry: 0.75 },
  worried:    { sad: 0.5, surprised: 0.3 },
  relaxed:    { relaxed: 0.7 },
  determined: { angry: 0.45, relaxed: 0.1 },
  focused:    { relaxed: 0.3 },
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
  leftHand: THREE.Object3D | null;
  rightHand: THREE.Object3D | null;
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

// Emote icon → gesture. Icons come from the backend `agent.emote` events
// whose icon field is set based on the agent's mood at speech time.
// Unrecognised icons fall through to `wave` as a safe default.
const EMOTE_GESTURE_MAP: Record<string, GestureName> = {
  "🎉": "hype", "🥳": "hype", "💪": "hype", "⭐": "hype", "🌟": "hype",
  "🤔": "think", "💭": "think", "😤": "think", "🧐": "think",
  "👋": "wave", "😊": "wave", "❤️": "wave", "💕": "wave", "🙌": "wave",
  "🙇": "bow", "🙏": "bow",
};

function gestureForEmote(icon: string): GestureName {
  return EMOTE_GESTURE_MAP[icon] ?? "wave";
}

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
  state: string;
  getMouthAmplitude?: (name: string) => number;
  spotlight: boolean;
  cameraResetNonce?: number;
  emote?: AgentEmote | null;
}

function VRMModel({
  url,
  agentName,
  mood,
  state,
  getMouthAmplitude,
  spotlight,
  cameraResetNonce,
  emote,
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
      leftHand: h?.getNormalizedBoneNode("leftHand") ?? null,
      rightHand: h?.getNormalizedBoneNode("rightHand") ?? null,
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

  // Mood-triggered gestures — picks randomly from MOOD_GESTURE_OPTIONS
  // so the same mood doesn't always play the exact same clip.
  useEffect(() => {
    const options = MOOD_GESTURE_OPTIONS[mood];
    if (!options || options.length === 0) return;
    const next = options[Math.floor(Math.random() * options.length)];
    const delay = 200 + Math.random() * 600;
    const timer = window.setTimeout(() => {
      stateRef.current.gesture = next;
      stateRef.current.gestureStart = performance.now() / 1000;
    }, delay);
    return () => window.clearTimeout(timer);
  }, [mood]);

  // State-triggered gestures. Celebrating always fires hype; talking
  // occasionally fires a wave to punctuate dialogue.
  useEffect(() => {
    if (state === "celebrating") {
      stateRef.current.gesture = "hype";
      stateRef.current.gestureStart = performance.now() / 1000;
    } else if (state === "talking") {
      const timer = window.setTimeout(() => {
        if (Math.random() > 0.5) {
          stateRef.current.gesture = "wave";
          stateRef.current.gestureStart = performance.now() / 1000;
        }
      }, 400 + Math.random() * 800);
      return () => window.clearTimeout(timer);
    }
  }, [state]);

  // Blink + mood + mouth state lives in refs so we don't re-render each
  // frame — the changes are all inside the WebGL scene.
  const stateRef = useRef({
    blinkNextAt: performance.now() / 1000 + blinkOffsetForName(agentName),
    blinking: false,
    blinkT: 0,
    // Whether this blink is a double-blink (two rapid closes).
    doubleBlink: false,
    smoothMood: { happy: 0, angry: 0, sad: 0, relaxed: 0, surprised: 0 },
    lookBias: Math.random() * Math.PI * 2,
    // Rising-edge detector on the mouth amplitude feeds the speech nod.
    prevAmp: 0,
    nodStart: -1,
    // Prevents nods from firing too rapidly (1 s cooldown after each nod).
    nodCooldownUntil: 0,
    // Smoothed arm velocity for pendulum-feel secondary motion.
    leftArmVel: 0,
    rightArmVel: 0,
    // Currently-playing gesture.
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
    leftHand: null,
    rightHand: null,
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

    // ── Mood emotes + state boost ────────────────────────────────────
    if (em) {
      const base = MOOD_MAP[mood] ?? {};
      const boost = STATE_EXPRESSION_BOOST[state] ?? {};
      const target: MoodTarget = {};
      for (const key of EMOTE_KEYS) {
        target[key] = Math.min(1, (base[key] ?? 0) + (boost[key] ?? 0));
      }
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
    // Three blink varieties:
    //   normal      — 15 BPM-ish, single close-open (speed 10).
    //   double      — two rapid closes in quick succession (15% chance).
    //   contemplative — slow, heavy blink when thinking (speed 4).
    const isThinking = state === "thinking";
    if (!st.blinking && now >= st.blinkNextAt) {
      st.blinking = true;
      st.blinkT = 0;
      st.doubleBlink = !isThinking && Math.random() < 0.15;
    }
    if (st.blinking && em) {
      // Contemplative blink is slow and heavy; normal is crisp.
      const blinkSpeed = isThinking ? 4 : 10;
      st.blinkT += delta * blinkSpeed;
      const t = st.blinkT;
      let v: number;
      if (st.doubleBlink) {
        // Double blink: close→open→close→open over 0..4.
        if (t < 1) v = t;
        else if (t < 2) v = 2 - t;
        else if (t < 2.4) v = 0;
        else if (t < 3) v = t - 2.4;
        else if (t < 4) v = 4 - t;
        else v = 0;
      } else {
        v = t < 1 ? t : t < 2 ? 2 - t : 0;
      }
      em.setValue("blink", Math.max(0, Math.min(1, v)));
      const endT = st.doubleBlink ? 4 : 2;
      if (t >= endT) {
        st.blinking = false;
        // Thinking: blinks less often (slow + heavy).
        st.blinkNextAt = now + (isThinking ? 4 + Math.random() * 4 : 2.5 + Math.random() * 3);
      }
    }

    // ── Procedural idle — breathing, weight shift, arm sway ──────────
    // Multiple overlapping sine waves per bone (Perlin-like layering)
    // give the "alive but not robotic" quality. Each agent gets a unique
    // phase offset so no two characters move in lockstep.
    const breathe = Math.sin(now * 1.4 + phase);          // ~13 BPM
    const breatheSlow = Math.sin(now * 0.7 + phase * 0.6); // half speed breath

    if (bones.chest) {
      // Chest breathes forward-back; tiny side sway adds life.
      bones.chest.rotation.x = breathe * 0.012 + breatheSlow * 0.004;
      bones.chest.rotation.z = Math.sin(now * 0.28 + phase) * 0.006;
    }
    if (bones.hips) {
      // Counter-balance to chest — hips shift opposite to weight foot.
      bones.hips.rotation.z = Math.sin(now * 0.28 + phase + Math.PI * 0.4) * 0.014;
      bones.hips.rotation.y = Math.sin(now * 0.19 + phase * 1.1) * 0.009;
    }

    // ── Natural arm movement — pendulum + breathing coupling ──────────
    // Arm hangs from shoulder; gravity pulls it down. The "elbow" (lower
    // arm) lags behind the upper arm with its own slower oscillation,
    // simulating the weight-and-drag of a real forearm.
    //
    // Two additive waves per arm:
    //   primary   — slow, large  (shoulder rock from breathing/weight shift)
    //   secondary — faster, tiny (residual micro-tremor from muscle tension)
    // Left and right use deliberately different frequencies so they never
    // sync up — that's what makes idle hands look robotic when they do.

    const lPrimary   = Math.sin(now * 0.52 + phase) * 0.045
                     + Math.sin(now * 0.19 + phase * 1.3) * 0.018;
    const lSecondary = Math.sin(now * 1.3  + phase * 0.8) * 0.008;
    const rPrimary   = Math.sin(now * 0.67 + phase + 2.1) * 0.040
                     + Math.sin(now * 0.24 + phase * 0.7) * 0.015;
    const rSecondary = Math.sin(now * 1.1  + phase * 1.2) * 0.007;

    // Upper arms breathe with the chest — they hang from it.
    const chestCoupling = breathe * 0.008;

    if (bones.leftUpperArm) {
      bones.leftUpperArm.rotation.z = 1.15 + lPrimary + lSecondary + chestCoupling;
      // Forward/back swing — small but makes the arm look less pinned.
      bones.leftUpperArm.rotation.x = Math.sin(now * 0.43 + phase + 0.9) * 0.012;
    }
    if (bones.rightUpperArm) {
      bones.rightUpperArm.rotation.z = -1.15 + rPrimary + rSecondary - chestCoupling;
      bones.rightUpperArm.rotation.x = Math.sin(now * 0.38 + phase + 2.5) * 0.011;
    }

    // Forearms lag ~120ms behind the upper arm (physics approximation).
    // The lag is modelled as a phase-shifted and attenuated copy of the
    // primary wave, plus their own micro-tremor. Elbow bend (rotation.z
    // for forearms in VRM space) should ALWAYS be ≥ 0 (extended = 0).
    if (bones.leftLowerArm) {
      const lLag = Math.sin(now * 0.52 + phase - 0.6) * 0.025; // lagged primary
      bones.leftLowerArm.rotation.z = Math.max(0, lLag + lSecondary * 0.6);
      bones.leftLowerArm.rotation.y = Math.sin(now * 0.31 + phase + 1.0) * 0.018;
    }
    if (bones.rightLowerArm) {
      const rLag = Math.sin(now * 0.67 + phase + 2.1 - 0.6) * 0.022;
      bones.rightLowerArm.rotation.z = -Math.max(0, rLag + rSecondary * 0.6);
      bones.rightLowerArm.rotation.y = -Math.sin(now * 0.36 + phase + 3.2) * 0.016;
    }

    // Wrist subtle rotation — tiny roll so hands don't look locked flat.
    if (bones.leftHand) {
      bones.leftHand.rotation.z = Math.sin(now * 0.41 + phase + 0.7) * 0.025;
      bones.leftHand.rotation.x = 0.05 + Math.sin(now * 0.29 + phase) * 0.012;
    }
    if (bones.rightHand) {
      bones.rightHand.rotation.z = Math.sin(now * 0.38 + phase + 2.3) * 0.025;
      bones.rightHand.rotation.x = 0.05 + Math.sin(now * 0.33 + phase + 1.4) * 0.012;
    }

    // Speaking lean — subtle chest push when actively talking (amplitude > 0.1).
    if (amp > 0.1 && bones.chest) {
      bones.chest.rotation.x += amp * 0.025;
    }

    // ── Speech-triggered head nod ────────────────────────────────────
    // Rising-edge on amplitude fires a single small bow. The cooldown
    // (1 s after nod finishes) prevents rapid chained nods that look
    // like the character is headbanging.
    if (amp > 0.22 && st.prevAmp <= 0.22 && st.nodStart < 0 && now > st.nodCooldownUntil) {
      st.nodStart = now;
    }
    st.prevAmp = amp;
    let nodX = 0;
    if (st.nodStart >= 0) {
      const nt = now - st.nodStart;
      if (nt > 0.55) {
        st.nodStart = -1;
        st.nodCooldownUntil = now + 1.2; // 1.2 s before next nod can fire
      } else {
        // Damped sine — gentle forward dip, 5° max.
        nodX = -Math.sin(nt * Math.PI * 2) * Math.exp(-nt * 4.0) * 0.055;
      }
    }

    // ── Head sway + nod (combined) ───────────────────────────────────
    // IMPORTANT: ALL three axes must be SET (=) here so that gesture and
    // state overlay additions later in the frame are pure deltas and do
    // NOT accumulate across frames. Any axis left un-set drifts without
    // bound whenever something does "+= value" to it each frame.
    if (bones.head) {
      bones.head.rotation.y = Math.sin(now * 0.45 + phase) * 0.028        // gentle look-left/right
                             + Math.sin(now * 0.17 + phase * 0.6) * 0.008; // slow drift
      bones.head.rotation.x = Math.sin(now * 0.32 + phase + 0.5) * 0.012 + nodX; // subtle nod + speech
      bones.head.rotation.z = 0; // reset every frame — gestures/state add on top safely
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

    // ── State-driven pose overlays ────────────────────────────────────
    // These add DELTAS on top of bones already SET in the idle section.
    // head.rotation.z is safe to use here because idle now resets it to 0.
    // Applied after gestures so state never fights an active gesture clip.
    if (!st.gesture) {
      switch (state) {
        case "working":
          // Slight forward lean; arms pulled in — like focusing on a task.
          if (bones.chest) bones.chest.rotation.x += 0.03;
          if (bones.leftUpperArm) bones.leftUpperArm.rotation.z -= 0.06;
          if (bones.rightUpperArm) bones.rightUpperArm.rotation.z += 0.06;
          break;
        case "talking": {
          // Arms open slightly — welcoming, expressive posture.
          const talkSway = Math.sin(now * 0.9 + phase) * 0.02;
          if (bones.leftUpperArm) bones.leftUpperArm.rotation.z += 0.04 + talkSway;
          if (bones.rightUpperArm) bones.rightUpperArm.rotation.z -= 0.04 - talkSway;
          break;
        }
        case "thinking":
          // Right forearm drifts upward toward chin — classic "hmm" pose.
          if (bones.rightUpperArm) {
            bones.rightUpperArm.rotation.z += 0.18;
            bones.rightUpperArm.rotation.x -= 0.12;
          }
          if (bones.rightLowerArm) bones.rightLowerArm.rotation.z += 0.25;
          // Slight head tilt — safe because idle set rotation.z = 0 above.
          if (bones.head) bones.head.rotation.z = -0.04;
          break;
        case "celebrating": {
          // Victory arms with subtle bounce oscillation.
          const celebOsc = Math.sin(now * 2.2 + phase) * 0.08;
          if (bones.leftUpperArm) bones.leftUpperArm.rotation.z -= 0.85 + celebOsc;
          if (bones.rightUpperArm) bones.rightUpperArm.rotation.z += 0.85 - celebOsc;
          if (bones.leftLowerArm) bones.leftLowerArm.rotation.z = Math.max(0, -0.25);
          if (bones.rightLowerArm) bones.rightLowerArm.rotation.z = Math.min(0, 0.25);
          break;
        }
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
    // Slight downward camera position + upward look target creates a
    // subtle "hero angle" — the viewer looks slightly up at the character,
    // which reads as more impressive than a flat eye-level frame.
    camera.position.set(centerX, centerY - 0.08, centerZ + distance);
    camera.lookAt(centerX, centerY + 0.05, centerZ);

    // Tell OrbitControls where to pivot. Without this the controls would
    // orbit around the origin, which for VRoid models puts the pivot at
    // the character's feet and makes rotation feel wildly off.
    if (controlsRef.current) {
      controlsRef.current.target.set(centerX, centerY + 0.05, centerZ);
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
  state,
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
        {/* Four-point lighting: ambient + key + fill + backlight rim.
            The backlight (-z, slightly above) catches the hair and
            shoulder silhouette, adding depth without needing PBR. */}
        <ambientLight intensity={0.65} />
        <directionalLight position={[1.2, 2, 1.5]} intensity={1.25} />
        <directionalLight position={[-1.5, 1.2, -1]} intensity={0.35} />
        <directionalLight position={[0, 1.8, -2.2]} intensity={0.55} color="#c4b5fd" />
        <Suspense fallback={null}>
          <VRMModel
            url={url}
            agentName={agent.name}
            mood={agent.mood}
            state={state ?? agent.state ?? "idle"}
            getMouthAmplitude={getMouthAmplitude}
            spotlight={spotlight}
            cameraResetNonce={cameraResetNonce}
          />
        </Suspense>
      </Canvas>
    </div>
  );
}

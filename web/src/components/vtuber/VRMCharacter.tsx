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
 *     every frame, apply a concave curve so quiet speech reads as more
 *     motion, and distribute the curved amplitude across the five VRM
 *     vowel blendshapes (aa/ih/ou/ee/oh) with a slowly-drifting bias and
 *     a small bleed into the neighbour vowel. We're not doing real
 *     formant analysis — just enough vowel variety to keep the mouth
 *     from looking like a single open-close shutter.
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
import {
  Component,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentRef,
  type ReactNode,
} from "react";
import * as THREE from "three";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { AgentData, AgentEmote } from "@/lib/types";
import { vrmFileForAgent } from "./vrmCredits";
import {
  ClipRuntime,
  clipCache,
  createSampleBuffer,
  type ClipSample,
} from "@/lib/mocap/clipPlayer";
import {
  collectMocapBones,
  type MocapBoneMap,
} from "@/lib/mocap/vrmShared";
import type { MocapBone, MocapExpression } from "@/lib/mocap/clipFormat";

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
  /** Mocap clip to play instead of procedural gestures on bones the clip
   *  covers. ``null`` / ``undefined`` disables mocap playback and the
   *  character returns to the idle + gesture pipeline. The clip is
   *  fetched lazily via ``clipCache``; while it loads the character
   *  keeps using its procedural pose. */
  mocapClipId?: string | null;
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
  idle:        { relaxed: 0.3 },
  happy:       { happy: 0.8 },
  excited:     { happy: 0.95, surprised: 0.4 },
  proud:       { happy: 0.6, relaxed: 0.3 },
  frustrated:  { angry: 0.8 },
  worried:     { sad: 0.55, surprised: 0.35 },
  relaxed:     { relaxed: 0.7 },
  determined:  { angry: 0.5, relaxed: 0.15 },
  focused:     { relaxed: 0.4 },
  curious:     { surprised: 0.6, happy: 0.25 },
  tired:       { sad: 0.3, relaxed: 0.4 },
  nostalgic:   { sad: 0.35, relaxed: 0.35 },
  inspired:    { surprised: 0.4, happy: 0.55 },
  mischievous: { happy: 0.6, angry: 0.1 },
  friendly:    { happy: 0.6, relaxed: 0.2 },
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

type GestureName = "wave" | "greet" | "hype" | "think" | "bow" | "beat" | "nod";

// Fraction of each gesture's duration that should feel like the attack
// (rise from 0 → 1). The rest is a slower settle back to 0. Tuned by eye
// on the wave/hype clips — 0.3 gives a crisp punch without shortening
// the hold so much that the pose stops reading.
const GESTURE_PEAK = 0.3;
// Derived remap exponent. Math.pow(t, K) at t=GESTURE_PEAK equals 0.5,
// so sin(·π) hits its maximum at real-time GESTURE_PEAK. Computed once.
const GESTURE_PEAK_SHAPE_K = Math.log(0.5) / Math.log(GESTURE_PEAK);

// Priority band for requestGesture(). Four separate schedulers (ambient
// idle, mood, state, emote) can fire on the same frame — raw last-write-
// wins loses gestures silently. A higher priority preempts a lower one;
// equal priority drops the incoming request so the current clip plays
// out. Anything preempted goes into a one-deep pending slot.
const GESTURE_PRIORITY = {
  ambient: 1, // idle scheduler's nod/beat/wave fillers
  mood: 2,    // mood-driven wave/hype/etc while mood is held
  talking: 3, // utterance-driven talking beats and initial wave
  emote: 4,   // backend-emitted emote icon reaction
  state: 5,   // celebrating/hype, initial greet — headline moments
} as const;
type GesturePriority = (typeof GESTURE_PRIORITY)[keyof typeof GESTURE_PRIORITY];

// How long a pending gesture is held onto before it's discarded. A clip
// that was queued two full seconds ago is no longer in sync with what
// triggered it; better to drop than to play late.
const PENDING_GESTURE_TTL_MS = 2000;

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

// Gesture rotation deltas are composed via quaternion multiplication
// rather than component-wise Euler addition. Three.js defaults each bone
// to "XYZ" order, which hits a gimbal-lock pole when the base pose
// combines with a large gesture delta (e.g. wave's rightUpperArm.z ≈ 1.55
// on top of the idle rotation.z = -1.4). Composing as a single quaternion
// with "YXZ" ordering — twist last, on top of flex/abduct — matches
// shoulder biomechanics and avoids the decomposition singularity.
const _gestureEuler = new THREE.Euler(0, 0, 0, "YXZ");
const _gestureQuat = new THREE.Quaternion();
function addBoneRotation(
  bone: THREE.Object3D,
  x: number,
  y: number,
  z: number,
): void {
  if (x === 0 && y === 0 && z === 0) return;
  _gestureEuler.set(x, y, z, "YXZ");
  _gestureQuat.setFromEuler(_gestureEuler);
  bone.quaternion.multiply(_gestureQuat);
}

const GESTURES: Record<
  GestureName,
  { duration: number; apply: (t: number, b: Bones, env: number) => void }
> = {
  wave: {
    duration: 1.9,
    apply: (t, b, env) => {
      // Raise the upper arm up-and-forward so the forearm can flex into
      // a natural "hand by the head" wave pose. The side-to-side swing
      // is driven by the UPPER arm's twist axis (rotation.y) — with the
      // arm raised, twisting around its long axis sweeps the bent
      // forearm and hand through the air in a clean horizontal arc.
      const osc = Math.sin(t * Math.PI * 4); // ~2 full wave cycles
      if (b.rightUpperArm) {
        // Negative x flexes the shoulder forward; negative z on the
        // forearm closes the elbow. Positive values hyperextend backward.
        addBoneRotation(b.rightUpperArm, -env * 0.8, -env * osc * 0.5, env * 1.55);
      }
      if (b.rightLowerArm) {
        addBoneRotation(b.rightLowerArm, 0, 0, -env * 0.95);
      }
      if (b.rightHand) {
        addBoneRotation(b.rightHand, 0, 0, -env * osc * 0.4);
      }
    },
  },
  // Bigger, longer greeting — a deliberate "hi there!" wave used on
  // first appearance and after spotlight switches. Same motion as `wave`
  // but higher arm, longer duration, and more oscillation cycles so the
  // viewer can't miss it.
  greet: {
    duration: 2.6,
    apply: (t, b, env) => {
      // Longer, more pronounced version of `wave` — arm raised high,
      // bigger shoulder-twist sweep, plus a head tilt and chest lean.
      const osc = Math.sin(t * Math.PI * 5); // ~2.5 clear wave cycles
      if (b.rightUpperArm) {
        addBoneRotation(b.rightUpperArm, -env * 0.95, -env * osc * 0.65, env * 1.75);
      }
      if (b.rightLowerArm) {
        addBoneRotation(b.rightLowerArm, 0, 0, -env * 1.1);
      }
      if (b.rightHand) {
        addBoneRotation(b.rightHand, 0, 0, -env * osc * 0.5);
      }
      if (b.head) b.head.rotation.z -= env * 0.06;
      if (b.chest) b.chest.rotation.x -= env * 0.04;
    },
  },
  hype: {
    duration: 0.95,
    apply: (_t, b, env) => {
      // Both arms up briefly — the "YES!!" moment.
      if (b.leftUpperArm) {
        addBoneRotation(b.leftUpperArm, -env * 0.2, 0, -env * 1.55);
      }
      if (b.rightUpperArm) {
        addBoneRotation(b.rightUpperArm, -env * 0.2, 0, env * 1.55);
      }
    },
  },
  think: {
    duration: 1.8,
    apply: (_t, b, env) => {
      // Right hand to chin with a small head tilt — reads as "hmm".
      if (b.rightUpperArm) {
        addBoneRotation(b.rightUpperArm, -env * 0.55, 0, env * 0.85);
      }
      if (b.rightLowerArm) {
        addBoneRotation(b.rightLowerArm, 0, 0, -env * 1.0);
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
  // Short, low-magnitude hand flick — used as a conversational beat
  // gesture during continuous talking so the character isn't static
  // between bigger waves. Roughly 40% the amplitude of `wave`.
  beat: {
    duration: 0.55,
    apply: (t, b, env) => {
      if (b.rightUpperArm) {
        addBoneRotation(b.rightUpperArm, -env * 0.14, 0, env * 0.6);
      }
      if (b.rightLowerArm) {
        addBoneRotation(
          b.rightLowerArm,
          0,
          env * Math.sin(t * Math.PI * 3) * 0.22,
          0,
        );
      }
    },
  },
  // Short forward head dip — conversational acknowledgement nod.
  nod: {
    duration: 0.45,
    apply: (_t, b, env) => {
      if (b.head) b.head.rotation.x += env * 0.10;
      if (b.chest) b.chest.rotation.x += env * 0.02;
    },
  },
};

// Mood → weighted gesture options. Tuples are [gesture, weight]. Uniform
// picks made every mood's first option dominate by luck alone; weights
// let the signature gesture for a mood (e.g. `hype` on `excited`) play
// more often than its softer variant without excluding it entirely.
type WeightedGesture = readonly [GestureName, number];
const MOOD_GESTURE_OPTIONS: Partial<Record<string, readonly WeightedGesture[]>> = {
  excited:     [["hype", 3], ["wave", 1]],
  proud:       [["wave", 2], ["bow", 1]],
  worried:     [["think", 1]],
  happy:       [["wave", 2], ["bow", 1]],
  determined:  [["hype", 1]],
  focused:     [["think", 1]],
  celebrating: [["hype", 2], ["wave", 1]],
  curious:     [["think", 2], ["wave", 1]],
  tired:       [["think", 2], ["bow", 1]],
  nostalgic:   [["bow", 2], ["think", 1]],
  inspired:    [["hype", 2], ["wave", 1]],
  mischievous: [["wave", 2], ["hype", 1]],
  friendly:    [["wave", 2], ["bow", 1]],
  frustrated:  [["hype", 2], ["wave", 1]],
  relaxed:     [["bow", 2], ["wave", 1]],
};

function pickWeighted<T>(opts: ReadonlyArray<readonly [T, number]>): T {
  let total = 0;
  for (const [, w] of opts) total += w;
  if (total <= 0) return opts[0][0];
  let r = Math.random() * total;
  for (const [val, w] of opts) {
    r -= w;
    if (r <= 0) return val;
  }
  return opts[opts.length - 1][0];
}

// Emote icon → gesture (or gesture list). Icons come from the backend
// `agent.emote` events whose icon field is set based on the agent's mood
// at speech time (see MOOD_EMOTE in src/autonoma/agents/base.py). Unknown
// icons fall through to `wave` as a safe default.
const EMOTE_GESTURE_MAP: Record<string, GestureName> = {
  // Hype/wave mix — excited, proud, determined, inspired, happy
  "✦": "hype",   // excited
  "★": "hype",   // proud
  "‼": "hype",   // determined
  "💡": "hype",  // inspired
  "♪": "wave",   // happy
  // Think — curious, focused, worried, tired
  "?": "think",
  "•": "think",
  "💧": "think", // worried
  "💤": "think", // tired
  // Brisk wave-type — frustrated, mischievous
  "💢": "wave",  // frustrated
  "✧": "wave",  // mischievous
  // Soft/bow — relaxed, nostalgic
  "～": "bow",    // relaxed
  "✿": "bow",    // nostalgic
};

// Alternative gesture picks used by gestureForEmote() so re-emotes don't
// always play the identical clip. Deterministic per-agent variation is
// driven by a hash of the agent name combined with the emote sequence.
const EMOTE_GESTURE_ALTERNATIVES: Record<string, GestureName[]> = {
  "✦": ["hype", "greet", "wave"],
  "★": ["greet", "hype", "wave", "bow"],
  "‼": ["hype", "wave"],
  "💡": ["hype", "greet", "wave"],
  "♪": ["greet", "wave", "hype"],
  "?": ["think"],
  "•": ["think"],
  "💧": ["think", "bow"],
  "💤": ["think", "bow"],
  "💢": ["wave", "hype"],
  "✧": ["wave", "hype"],
  "～": ["bow", "wave"],
  "✿": ["bow", "wave"],
};

function hashString(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  }
  return h;
}

function gestureForEmote(icon: string, agentName: string = "", seq: number = 0): GestureName {
  const alts = EMOTE_GESTURE_ALTERNATIVES[icon];
  if (alts && alts.length > 0) {
    const idx = (hashString(agentName) + seq) % alts.length;
    return alts[idx];
  }
  return EMOTE_GESTURE_MAP[icon] ?? "wave";
}

// ── State pose overlays ──────────────────────────────────────────────
//
// Each state contributes a small pose layered on top of the idle loop.
// All magnitudes are multiplied by `w` (0..1) so the caller can
// cross-fade between two states during transitions instead of snapping.
function applyStateOverlay(
  state: string,
  w: number,
  bones: Bones,
  now: number,
  phase: number,
) {
  if (w <= 0) return;
  switch (state) {
    case "working":
      // Slight forward lean; arms pulled in — like focusing on a task.
      if (bones.chest) bones.chest.rotation.x += 0.03 * w;
      if (bones.leftUpperArm) bones.leftUpperArm.rotation.z -= 0.06 * w;
      if (bones.rightUpperArm) bones.rightUpperArm.rotation.z += 0.06 * w;
      break;
    case "talking": {
      // Arms open slightly — welcoming, expressive posture.
      const talkSway = Math.sin(now * 0.9 + phase) * 0.02;
      if (bones.leftUpperArm) bones.leftUpperArm.rotation.z += (0.04 + talkSway) * w;
      if (bones.rightUpperArm) bones.rightUpperArm.rotation.z -= (0.04 - talkSway) * w;
      break;
    }
    case "thinking":
      // Right forearm drifts upward toward chin — classic "hmm" pose.
      if (bones.rightUpperArm) {
        bones.rightUpperArm.rotation.z += 0.18 * w;
        bones.rightUpperArm.rotation.x -= 0.12 * w;
      }
      // Right elbow flex is negative-z (consistent with wave/greet/think
      // gesture clips and the idle right forearm baseline). Using +=
      // here hyperextended the elbow backward — the same grotesque bend
      // this file was fixing in the think gesture above.
      if (bones.rightLowerArm) bones.rightLowerArm.rotation.z -= 0.25 * w;
      // Head tilt — additive during cross-fade. Idle set rotation.z = 0.
      if (bones.head) bones.head.rotation.z += -0.04 * w;
      break;
    case "celebrating": {
      // Victory arms with subtle bounce oscillation.
      const celebOsc = Math.sin(now * 2.2 + phase) * 0.08;
      if (bones.leftUpperArm) bones.leftUpperArm.rotation.z -= (0.85 + celebOsc) * w;
      if (bones.rightUpperArm) bones.rightUpperArm.rotation.z += (0.85 - celebOsc) * w;
      // Extend the forearms as an additive delta so the idle arm sway
      // keeps showing through — previously we lerped them to a fixed 0,
      // which locked the elbows at w=1 and killed the micro-motion for
      // the whole celebrating state. The idle bend sits at +0.15 on
      // the left and −0.15 on the right (mirror signs), so straightening
      // uses OPPOSITE sign deltas. Using the same sign on both bent the
      // right forearm further instead of extending it — the source of
      // the asymmetric grotesque arm during celebrating.
      if (bones.leftLowerArm) bones.leftLowerArm.rotation.z -= 0.15 * w;
      if (bones.rightLowerArm) bones.rightLowerArm.rotation.z += 0.15 * w;
      break;
    }
    case "spawning": {
      // "Emerging" — arms held slightly away from the body, chest lifted,
      // head tilted up as if just opening its eyes.
      if (bones.leftUpperArm) bones.leftUpperArm.rotation.z += 0.18 * w;
      if (bones.rightUpperArm) bones.rightUpperArm.rotation.z -= 0.18 * w;
      if (bones.chest) bones.chest.rotation.x -= 0.04 * w;
      if (bones.head) bones.head.rotation.x -= 0.08 * w;
      break;
    }
    case "error": {
      // Sad droop — head down, shoulders drop.
      if (bones.head) bones.head.rotation.x += 0.10 * w;
      if (bones.chest) bones.chest.rotation.x += 0.04 * w;
      if (bones.leftUpperArm) bones.leftUpperArm.rotation.z -= 0.10 * w;
      if (bones.rightUpperArm) bones.rightUpperArm.rotation.z += 0.10 * w;
      break;
    }
  }
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
  mocapClipId?: string | null;
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
  mocapClipId,
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
    // Drop the T-pose arms so they hang down the sides. Going to
    // ±1.40 rad (~80° from horizontal) leaves only a ~10° splay at the
    // shoulder, which reads as "arms hanging naturally" — the ㄴ-shaped
    // silhouette. The previous 1.15 rad (~66°) left the upper arm still
    // jutting outward ~24° above vertical, which combined with the
    // forearm flex read as a broken ㄱ shape (shoulder held up,
    // forearm dangling).
    const leftUpper = h?.getNormalizedBoneNode("leftUpperArm") ?? null;
    const rightUpper = h?.getNormalizedBoneNode("rightUpperArm") ?? null;
    if (leftUpper) leftUpper.rotation.z = 1.4;
    if (rightUpper) rightUpper.rotation.z = -1.4;
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

  // Ambient idle gesture scheduler — fires gentle gestures (nod, beat,
  // waves, occasionally think) on a 6-12s cadence regardless of state/mood
  // so the character doesn't just jitter in place when the backend sends
  // nothing. Fires an initial `greet` shortly after mount so first
  // impressions always include a visible hello wave. Skipped while a
  // gesture is already running.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    // Weighted: hand waves are the headline so we tip the pool toward
    // them — wave 4x, beat 2x, nod 2x, think 1x.
    const pool: GestureName[] = [
      "wave", "wave", "wave", "wave",
      "beat", "beat",
      "nod", "nod",
      "think",
    ];
    // Initial hello wave — fires once ~0.6-1.2s after mount, unconditionally.
    const hello = window.setTimeout(() => {
      if (cancelled) return;
      // Initial greet is a headline moment — same tier as state events.
      requestGestureRef.current("greet", GESTURE_PRIORITY.state);
    }, 600 + Math.random() * 600);
    const scheduleNext = () => {
      const delay = 6000 + Math.random() * 6000; // 6–12s
      timer = window.setTimeout(() => {
        if (cancelled) return;
        const pick = pool[Math.floor(Math.random() * pool.length)];
        requestGestureRef.current(pick, GESTURE_PRIORITY.ambient);
        scheduleNext();
      }, delay);
    };
    // Queue the first ambient pick after the initial greet has room to play.
    timer = window.setTimeout(scheduleNext, 4000 + Math.random() * 2000);
    return () => {
      cancelled = true;
      window.clearTimeout(hello);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, []);

  // Mood-triggered gestures — picks randomly from MOOD_GESTURE_OPTIONS
  // so the same mood doesn't always play the exact same clip. Re-fires
  // every 9-18s (randomized per cycle) while the mood is held, so that
  // a long-held `excited` mood keeps producing occasional `hype`/`wave`
  // clips instead of playing exactly once.
  useEffect(() => {
    const options = MOOD_GESTURE_OPTIONS[mood];
    if (!options || options.length === 0) return;
    let cancelled = false;
    let timer: number | undefined;
    const playOne = () => {
      if (cancelled) return;
      const next = pickWeighted(options);
      requestGestureRef.current(next, GESTURE_PRIORITY.mood);
    };
    const scheduleNext = (initial: boolean) => {
      const delay = initial
        ? 200 + Math.random() * 600
        : 9000 + Math.random() * 9000; // 9-18s while mood is held
      timer = window.setTimeout(() => {
        playOne();
        scheduleNext(false);
      }, delay);
    };
    scheduleNext(true);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [mood]);

  // Emote-triggered gestures — fires whenever a new emote arrives (seq bumps).
  // This mirrors what the pixel character shows above their head so the VTuber
  // body reacts in sync with the floating icon.
  useEffect(() => {
    if (!emote) return;
    const gesture = gestureForEmote(emote.icon, agentName, emote.seq ?? 0);
    const delay = 80 + Math.random() * 120; // slight human lag
    const timer = window.setTimeout(() => {
      requestGestureRef.current(gesture, GESTURE_PRIORITY.emote);
    }, delay);
    return () => window.clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [emote?.seq]);

  // State-triggered gestures. Celebrating always fires hype; talking
  // fires an initial punctuation plus occasional beat gestures on a
  // 3-5s cadence for as long as the state remains `talking` (25% chance
  // per cycle — most cycles produce nothing, keeping it non-spammy).
  useEffect(() => {
    if (state === "celebrating") {
      requestGestureRef.current("hype", GESTURE_PRIORITY.state);
      return;
    }
    if (state === "talking") {
      let cancelled = false;
      let timer: number | undefined;
      const initial = window.setTimeout(() => {
        if (cancelled) return;
        if (Math.random() > 0.5) {
          requestGestureRef.current("wave", GESTURE_PRIORITY.talking);
        }
      }, 400 + Math.random() * 800);
      // First beat fires early (1.2–2.2s) so medium-length utterances get
      // at least one gesture besides the initial wave. Subsequent beats
      // settle into the 3–5s cadence — longer intervals keep idle arm
      // motion visible between clips.
      let firstBeat = true;
      const scheduleBeat = () => {
        const delay = firstBeat
          ? 1200 + Math.random() * 1000
          : 3000 + Math.random() * 2000;
        firstBeat = false;
        timer = window.setTimeout(() => {
          if (cancelled) return;
          if (Math.random() < 0.25) {
            // Lightweight subset — gentle nod or short hand-flick.
            const opts: GestureName[] = ["nod", "beat"];
            const pick = opts[Math.floor(Math.random() * opts.length)];
            // Priority-arbitrated — if a bigger gesture is running the
            // helper drops the request (talking beats don't squat a
            // state-tier clip).
            requestGestureRef.current(pick, GESTURE_PRIORITY.talking);
          }
          scheduleBeat();
        }, delay);
      };
      scheduleBeat();
      return () => {
        cancelled = true;
        window.clearTimeout(initial);
        if (timer !== undefined) window.clearTimeout(timer);
      };
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
    // Priority of the in-flight gesture (higher wins). Arbitrates the
    // "same-frame clover" case where ambient + mood + state timers
    // all fire between two render frames and stomp each other.
    gesturePriority: 0,
    // One-deep queue for requests that arrived while a gesture was
    // already playing. Only held briefly — if `pendingExpires` has
    // passed by the time the current gesture ends we drop it, because
    // a gesture the user waited 2 seconds to see is less relevant
    // than keeping the idle pose stable.
    pendingGesture: null as GestureName | null,
    pendingPriority: 0,
    pendingExpires: 0,
    // State cross-fade — when `state` changes we record the previous
    // value and ease stateBlend 0→1 over ~350ms, blending the old
    // overlay out and the new one in instead of snapping poses.
    prevState: null as string | null,
    stateBlend: 1,
    // Speech-beat nod tracker — fires small secondary nods during
    // sustained speech (distinct from the rising-edge bow above).
    speechOnsetStart: -1,
    beatNodStart: -1,
    beatNodNextAt: 0,
    // Chest-pump beat (talking-state amplitude rising edge).
    chestPumpStart: -1,
    chestPumpCooldownUntil: 0,
    prevAmpForPump: 0,
  });

  // Single arbitration path for every gesture trigger. Replaces the four
  // schedulers' direct writes to stateRef.current.gesture — last-write-
  // wins was dropping clips whenever two timers landed in the same frame
  // (e.g. ambient nod vs. mood hype vs. talking beat). Priority wins;
  // same-priority yields to whatever's already running; higher-priority
  // requests that arrive mid-clip sit in a one-deep pending slot and
  // play when the current gesture finishes.
  const requestGestureRef = useRef<(name: GestureName, priority: GesturePriority) => void>(
    () => {},
  );
  requestGestureRef.current = (name: GestureName, priority: GesturePriority) => {
    const s = stateRef.current;
    const nowSec = performance.now() / 1000;
    if (s.gesture === null) {
      s.gesture = name;
      s.gestureStart = nowSec;
      s.gesturePriority = priority;
      return;
    }
    if (priority > s.gesturePriority) {
      // Preempt outright — a headline moment (celebrate, initial greet)
      // beating an ambient filler.
      s.gesture = name;
      s.gestureStart = nowSec;
      s.gesturePriority = priority;
      return;
    }
    // Incoming can't preempt. Hold it as pending only if its priority is
    // strictly higher than whatever's already pending — lower-priority
    // requests shouldn't squat the slot.
    if (priority > s.pendingPriority) {
      s.pendingGesture = name;
      s.pendingPriority = priority;
      s.pendingExpires = performance.now() + PENDING_GESTURE_TTL_MS;
    }
  };

  // State cross-fade trigger. Whenever `state` changes, remember the
  // previous value and reset stateBlend so the overlay smoothly eases
  // from (1 - blend) * old  →  blend * new over ~350ms.
  const prevStateValueRef = useRef(state);
  useEffect(() => {
    if (prevStateValueRef.current === state) return;
    stateRef.current.prevState = prevStateValueRef.current;
    stateRef.current.stateBlend = 0;
    prevStateValueRef.current = state;
  }, [state]);

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

  // Mocap playback — superset of the procedural bone cache (``Bones``).
  // We collect every bone the clip format can address so a full-body
  // recording can override the procedural pose. The runtime is recreated
  // whenever ``mocapClipId`` flips; the clipCache keeps the underlying
  // payload hot across remounts.
  const mocapBonesRef = useRef<MocapBoneMap>({});
  const mocapRuntimeRef = useRef<ClipRuntime | null>(null);
  const mocapSampleRef = useRef<ClipSample>(createSampleBuffer());
  // Reused when composing a sample rotation on top of a raw scene
  // bone's non-identity rest quaternion (finger fallback path).
  const _mocapScratchQ = useRef(new THREE.Quaternion()).current;

  useEffect(() => {
    if (!vrm) return;
    mocapBonesRef.current = collectMocapBones(vrm);
  }, [vrm]);

  useEffect(() => {
    if (!mocapClipId) {
      mocapRuntimeRef.current = null;
      return;
    }
    const id = mocapClipId;
    let cancelled = false;
    clipCache.retain(id);
    clipCache
      .ensure(id)
      .then((clip) => {
        if (cancelled) return;
        mocapRuntimeRef.current = new ClipRuntime(
          clip,
          performance.now() / 1000,
          { loop: true },
        );
      })
      .catch(() => {
        // Swallow — the character keeps its procedural pose if the
        // fetch fails. The /mocap page surfaces upload/fetch errors.
      });
    return () => {
      cancelled = true;
      clipCache.release(id);
      mocapRuntimeRef.current = null;
    };
  }, [mocapClipId]);

  useFrame((_, rawDelta) => {
    if (!vrm) return;

    // Clamp delta to 100ms max so a frame drop (tab hidden, GC pause, etc.)
    // doesn't cause animations to snap or skip. 0.1s is roughly 10fps —
    // below that threshold we accept some slowdown rather than a visual pop.
    const delta = Math.min(rawDelta, 0.1);

    const em = vrm.expressionManager;
    const now = performance.now() / 1000;
    const st = stateRef.current;
    const bones = bonesRef.current;
    const phase = st.lookBias;

    // Amplitude drives both the mouth and the speech-nod trigger, so
    // sample it once per frame.
    const amp = getMouthAmplitude ? getMouthAmplitude(agentName) : 0;

    // ── Mouth (aa / ih / ou / ee / oh) ───────────────────────────────
    // Distribute amplitude across the five VRM vowel blendshapes with a
    // slowly-drifting bias, so the mouth shapes change while speaking
    // instead of only opening-and-closing on "aa". A concave curve
    // (pow 0.7) lifts quiet→medium amplitudes so the motion reads more
    // naturally on soft speech.
    if (em && getMouthAmplitude) {
      const curved = Math.pow(Math.min(1, amp * 1.4), 0.7);
      // Vowel bias changes every ~180ms during speech. Math.floor(now * 5.5)
      // gives ~5.5 steps/sec; phase offsets per-agent so two characters
      // speaking simultaneously don't share the same mouth shape.
      const vowels: ("aa" | "ih" | "ou" | "ee" | "oh")[] = ["aa", "ih", "ou", "ee", "oh"];
      const vowelIdx = Math.floor(now * 5.5 + phase) % vowels.length;
      const nbrIdx = (vowelIdx + 1) % vowels.length;
      for (let i = 0; i < vowels.length; i++) {
        em.setValue(vowels[i], 0);
      }
      if (curved >= 0.04) {
        em.setValue(vowels[vowelIdx], curved);
        em.setValue(vowels[nbrIdx], curved * 0.3);
      }
    }

    // ── Mood emotes + state boost ────────────────────────────────────
    if (em) {
      const base = MOOD_MAP[mood] ?? {};
      const boost = STATE_EXPRESSION_BOOST[state] ?? {};
      const target: MoodTarget = {};
      let rawSum = 0;
      for (const key of EMOTE_KEYS) {
        const v = (base[key] ?? 0) + (boost[key] ?? 0);
        target[key] = v;
        rawSum += v;
      }
      // Per-key clamping dropped secondary components (e.g. "surprised"
      // beside a saturated "happy") whenever mood + state boost summed
      // past 1.0 on one key. Normalize the whole expression vector when
      // the combined magnitude would read as over-saturated, preserving
      // the mix — then clamp each slot to [0, 1] for rig safety.
      const scale = rawSum > 1.4 ? 1.4 / rawSum : 1;
      let targetSum = 0;
      for (const key of EMOTE_KEYS) {
        const scaled = Math.min(1, (target[key] ?? 0) * scale);
        target[key] = scaled;
        targetSum += scaled;
      }
      const s = st.smoothMood;
      // Ease at ~4/sec so a mood flip takes ~250ms to settle.
      const k = 1 - Math.exp(-delta * 4);
      for (const key of EMOTE_KEYS) {
        const t = target[key] ?? 0;
        s[key] += (t - s[key]) * k;
        em.setValue(key, s[key]);
      }
      // Neutral fallback — if all mood blendshape targets combined fall
      // below a threshold (unknown mood, or a very subtle one), fill in
      // `neutral` so the face still reads as alive instead of blank.
      // Wrapped in a null-guard because some rigs don't ship `neutral`.
      if (em.getExpression?.("neutral")) {
        const neutralTarget = targetSum < 0.15 ? 0.25 : 0;
        em.setValue("neutral", neutralTarget);
      }
      // ── Subtle eye-glance micro-motion ─────────────────────────────
      // vrm.lookAt.target still points at the camera, but these
      // blendshapes offset the baseline so the eyes drift naturally on
      // a slow cycle. Amplitudes kept modest (0.15-0.3) to read as gaze
      // rather than spasm. `thinking` biases upward ("looking away to
      // think"), `talking` biases slightly down-toward-camera.
      const glanceX = Math.sin(now * (2 * Math.PI) / 5.3 + phase) * 0.25;       // period ~5.3s
      const glanceY = Math.sin(now * (2 * Math.PI) / 7.1 + phase * 1.3) * 0.2;  // period ~7.1s
      let lookL = 0, lookR = 0, lookU = 0, lookD = 0;
      if (glanceX >= 0) lookR = glanceX; else lookL = -glanceX;
      if (glanceY >= 0) lookU = glanceY; else lookD = -glanceY;
      if (state === "thinking") {
        lookU = Math.min(1, lookU + 0.35);
        lookD = Math.max(0, lookD - 0.2);
      } else if (state === "talking") {
        lookD = Math.min(1, lookD + 0.1);
        lookU = Math.max(0, lookU - 0.1);
      }
      // Null-guard each one — minimalist rigs may not define look* slots.
      if (em.getExpression?.("lookLeft")) em.setValue("lookLeft", lookL);
      if (em.getExpression?.("lookRight")) em.setValue("lookRight", lookR);
      if (em.getExpression?.("lookUp")) em.setValue("lookUp", lookU);
      if (em.getExpression?.("lookDown")) em.setValue("lookDown", lookD);
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
      // Use the real delta so blink duration is frame-rate-independent.
      // Cap at 100 ms so a one-off stall (tab backgrounded, GC pause)
      // can't fast-forward through the whole blink in a single frame —
      // but 30fps frames (33 ms) no longer get artificially slowed to
      // the old 16 ms ceiling.
      st.blinkT += Math.min(delta, 0.1) * blinkSpeed;
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
    //
    // Magnitudes scaled up from the first pass because the character
    // renders in a narrow 356px-wide panel — at that size subtle motion
    // becomes invisible. Primary waves roughly doubled; secondary
    // "muscle tremor" waves kept small on purpose.
    const breathe = Math.sin(now * 1.4 + phase);          // ~13 BPM
    const breatheSlow = Math.sin(now * 0.7 + phase * 0.6); // half speed breath

    // ── Weight shift / contrapposto ──────────────────────────────────
    // Real standing poses shift weight between feet on a ~4-6s cycle.
    // `weightShift` ranges roughly in [-1, 1]. Hips tilt one way;
    // chest counter-tilts (contrapposto) the other way so the torso
    // reads as shifted without any actual translation (humanoid bones
    // are normalized rotations only).
    const weightShift = Math.sin(now * 0.35 + phase) * 1;

    if (bones.chest) {
      // Chest breathes forward-back; tiny side sway adds life.
      bones.chest.rotation.x = breathe * 0.022 + breatheSlow * 0.007;
      // Contrapposto — shoulders tilt OPPOSITE the hips.
      bones.chest.rotation.z = Math.sin(now * 0.28 + phase) * 0.006
                              - weightShift * 0.035;
    }
    if (bones.hips) {
      // Counter-balance + weight-shift drives a ~4-6s tilt cycle that
      // makes the character actually look like it's standing on legs.
      bones.hips.rotation.z = Math.sin(now * 0.28 + phase + Math.PI * 0.4) * 0.022
                             + weightShift * 0.05;
      bones.hips.rotation.y = Math.sin(now * 0.19 + phase * 1.1) * 0.016;
    }

    // ── Natural arm movement — pendulum + breathing coupling ──────────
    // Arm hangs from shoulder; gravity pulls it down. The "elbow" (lower
    // arm) lags behind the upper arm with its own slower oscillation,
    // simulating the weight-and-drag of a real forearm.
    //
    // Three additive waves per arm:
    //   primary   — slow, large  (shoulder rock from breathing/weight shift)
    //   sway      — medium, arm opens/closes against the torso
    //   secondary — faster, tiny (residual micro-tremor from muscle tension)
    // Left and right use deliberately different frequencies so they never
    // sync up — that's what makes idle hands look robotic when they do.

    const lPrimary   = Math.sin(now * 0.52 + phase) * 0.10
                     + Math.sin(now * 0.19 + phase * 1.3) * 0.038;
    const lSway      = Math.sin(now * 0.37 + phase + 0.8) * 0.05;
    const lSecondary = Math.sin(now * 1.3  + phase * 0.8) * 0.012;
    const rPrimary   = Math.sin(now * 0.67 + phase + 2.1) * 0.10
                     + Math.sin(now * 0.24 + phase * 0.7) * 0.034;
    const rSway      = Math.sin(now * 0.41 + phase + 3.4) * 0.045;
    const rSecondary = Math.sin(now * 1.1  + phase * 1.2) * 0.011;

    // Upper arms breathe with the chest — they hang from it.
    const chestCoupling = breathe * 0.018;

    if (bones.leftUpperArm) {
      bones.leftUpperArm.rotation.z = 1.4 + lPrimary + lSway + lSecondary + chestCoupling;
      // Forward/back swing — visible pendulum motion.
      bones.leftUpperArm.rotation.x = Math.sin(now * 0.43 + phase + 0.9) * 0.045
                                    + Math.sin(now * 0.23 + phase) * 0.02;
      // Shoulder rotation around the body axis (twist) — subtle roll.
      bones.leftUpperArm.rotation.y = Math.sin(now * 0.29 + phase + 1.7) * 0.028;
    }
    if (bones.rightUpperArm) {
      bones.rightUpperArm.rotation.z = -1.4 - rPrimary - rSway + rSecondary - chestCoupling;
      bones.rightUpperArm.rotation.x = Math.sin(now * 0.38 + phase + 2.5) * 0.042
                                     + Math.sin(now * 0.21 + phase + 1.1) * 0.02;
      bones.rightUpperArm.rotation.y = Math.sin(now * 0.33 + phase + 2.9) * 0.028;
    }

    // Forearms lag ~120ms behind the upper arm (physics approximation).
    // Baseline elbow bend of ~0.15 rad keeps arms slightly bent instead
    // of locked straight at the T-pose zero, which reads as stiff.
    // The lag is modelled as a phase-shifted and attenuated copy of the
    // primary wave, plus their own micro-tremor. Elbow bend (rotation.z
    // for forearms in VRM space) should always be ≥ 0 on the left (extend
    // + flex) and ≤ 0 on the right (mirror).
    if (bones.leftLowerArm) {
      const lLag = Math.sin(now * 0.52 + phase - 0.6) * 0.04;
      const lFlex = Math.sin(now * 0.27 + phase + 1.4) * 0.03;
      bones.leftLowerArm.rotation.z = Math.max(0, 0.12 + lLag + lFlex + lSecondary * 0.6);
      bones.leftLowerArm.rotation.y = Math.sin(now * 0.31 + phase + 1.0) * 0.03;
      bones.leftLowerArm.rotation.x = Math.sin(now * 0.22 + phase + 0.4) * 0.018;
    }
    if (bones.rightLowerArm) {
      const rLag = Math.sin(now * 0.67 + phase + 2.1 - 0.6) * 0.04;
      const rFlex = Math.sin(now * 0.31 + phase + 2.9) * 0.03;
      bones.rightLowerArm.rotation.z = -Math.max(0, 0.12 + rLag + rFlex + rSecondary * 0.6);
      bones.rightLowerArm.rotation.y = -Math.sin(now * 0.36 + phase + 3.2) * 0.03;
      bones.rightLowerArm.rotation.x = Math.sin(now * 0.25 + phase + 2.4) * 0.018;
    }

    // Wrist subtle rotation — hand roll and small flexion so they don't
    // look locked flat to the forearm.
    if (bones.leftHand) {
      bones.leftHand.rotation.z = Math.sin(now * 0.41 + phase + 0.7) * 0.07;
      bones.leftHand.rotation.x = 0.05 + Math.sin(now * 0.29 + phase) * 0.045;
      bones.leftHand.rotation.y = Math.sin(now * 0.36 + phase + 1.2) * 0.04;
    }
    if (bones.rightHand) {
      bones.rightHand.rotation.z = Math.sin(now * 0.38 + phase + 2.3) * 0.07;
      bones.rightHand.rotation.x = 0.05 + Math.sin(now * 0.33 + phase + 1.4) * 0.045;
      bones.rightHand.rotation.y = Math.sin(now * 0.31 + phase + 2.7) * 0.04;
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
        // Clamp nt to avoid frame-drop spikes making the decay jump.
        const ntClamped = Math.min(nt, 0.55);
        nodX = -Math.sin(ntClamped * Math.PI * 2) * Math.exp(-ntClamped * 4.0) * 0.055;
      }
    }

    // ── Sustained-speech beat nods ───────────────────────────────────
    // During continuous speech (amplitude > 0.18 sustained > 1 s) fire
    // small beat-nods on a ~700-1100ms cadence with jitter. These are
    // deliberately smaller than the rising-edge nod above so they read
    // as rhythmic acknowledgement rather than another big "hello bow".
    if (amp > 0.18) {
      if (st.speechOnsetStart < 0) st.speechOnsetStart = now;
    } else {
      st.speechOnsetStart = -1;
      st.beatNodNextAt = 0;
    }
    const sustained =
      st.speechOnsetStart >= 0 && now - st.speechOnsetStart > 1.0;
    if (sustained) {
      if (st.beatNodNextAt === 0) {
        st.beatNodNextAt = now + 0.7 + Math.random() * 0.4;
      } else if (now >= st.beatNodNextAt && st.beatNodStart < 0 && st.nodStart < 0) {
        st.beatNodStart = now;
        st.beatNodNextAt = now + 0.7 + Math.random() * 0.4;
      }
    }
    let beatNodX = 0;
    if (st.beatNodStart >= 0) {
      const bt = now - st.beatNodStart;
      if (bt > 0.4) {
        st.beatNodStart = -1;
      } else {
        // Half the peak of the rising-edge nod.
        beatNodX = -Math.sin(bt * Math.PI * 2.5) * Math.exp(-bt * 5.0) * 0.028;
      }
    }

    // ── Head sway + nod (combined) ───────────────────────────────────
    // IMPORTANT: ALL three axes must be SET (=) here so that gesture and
    // state overlay additions later in the frame are pure deltas and do
    // NOT accumulate across frames. Any axis left un-set drifts without
    // bound whenever something does "+= value" to it each frame.
    if (bones.head) {
      bones.head.rotation.y = Math.sin(now * 0.45 + phase) * 0.055        // gentle look-left/right
                             + Math.sin(now * 0.17 + phase * 0.6) * 0.016; // slow drift
      bones.head.rotation.x = Math.sin(now * 0.32 + phase + 0.5) * 0.025
                             + nodX + beatNodX; // subtle idle nod + speech nods
      bones.head.rotation.z = 0; // reset every frame — gestures/state add on top safely
    }

    // ── Chest-pump beat during talking ───────────────────────────────
    // A very small forward lean pulse on amplitude rising edges above
    // 0.3 while actively talking, with ~0.9s cooldown. Reads as the
    // little "lean-in" speakers do on stressed syllables.
    if (
      state === "talking" &&
      amp > 0.3 &&
      st.prevAmpForPump <= 0.3 &&
      st.chestPumpStart < 0 &&
      now > st.chestPumpCooldownUntil
    ) {
      st.chestPumpStart = now;
    }
    st.prevAmpForPump = amp;
    let chestPumpX = 0;
    if (st.chestPumpStart >= 0) {
      const ct = now - st.chestPumpStart;
      if (ct > 0.45) {
        st.chestPumpStart = -1;
        st.chestPumpCooldownUntil = now + 0.9;
      } else {
        // Pulse envelope — up fast, down fast, max ~0.06 rad.
        chestPumpX = Math.sin(ct * Math.PI * 2.2) * Math.exp(-ct * 4.0) * 0.06;
      }
    }
    if (chestPumpX !== 0 && bones.chest) {
      bones.chest.rotation.x += chestPumpX;
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
        st.gesturePriority = 0;
        // Fresh pending? Play it. Stale? Drop — a clip the user waited
        // two seconds to see is no longer in sync with the signal that
        // requested it.
        if (st.pendingGesture && performance.now() <= st.pendingExpires) {
          st.gesture = st.pendingGesture;
          st.gestureStart = now;
          st.gesturePriority = st.pendingPriority;
        }
        st.pendingGesture = null;
        st.pendingPriority = 0;
        st.pendingExpires = 0;
      } else {
        const t = nt / g.duration;
        // Asymmetric 0 → 1 → 0 envelope: fast anticipation into the peak,
        // slower settle out. Human gesture recordings rarely spend equal
        // time on the attack and decay — a symmetric sin(πt) felt robotic
        // on the wave/hype clips in particular, because the hand hung at
        // peak for the same duration it rose, then dropped in equal time.
        //
        // We reshape the time axis with t^k so the envelope's symmetric
        // peak lands at real-time t = GESTURE_PEAK instead of 0.5. Picking
        // k = log(0.5) / log(peak) guarantees the remap hits exactly 0.5
        // at t = peak, so the sine curve keeps its start/end zeros and
        // its maximum of 1. With peak = 0.3, roughly 30% of the clip is
        // the attack and 70% is the trailing settle.
        const env = Math.sin(Math.pow(t, GESTURE_PEAK_SHAPE_K) * Math.PI);
        g.apply(t, bones, env);
      }
    }

    // ── State-driven pose overlays (with cross-fade) ─────────────────
    // These add DELTAS on top of bones already SET in the idle section.
    // head.rotation.z is safe to use here because idle now resets it to 0.
    // Applied after gestures so state never fights an active gesture clip.
    //
    // State transitions cross-fade over ~350ms to eliminate pose snaps:
    //   previous overlay scaled by (1 - stateBlend)
    //   new      overlay scaled by      stateBlend
    if (!st.gesture) {
      // Ease stateBlend 0 → 1 over ~350ms (k = 1 - exp(-delta/tau)).
      if (st.stateBlend < 1) {
        const tau = 0.35;
        st.stateBlend = Math.min(1, st.stateBlend + delta / tau);
        // Snap below a tiny epsilon to 1 so float drift can't leave the
        // previous overlay mixed in at sub-perceptual weight forever.
        if (st.stateBlend >= 0.999) {
          st.stateBlend = 1;
          st.prevState = null;
        }
      }
      const newW = st.stateBlend;
      const oldW = 1 - newW;
      applyStateOverlay(state, newW, bones, now, phase);
      if (st.prevState && oldW > 0) {
        applyStateOverlay(st.prevState, oldW, bones, now, phase);
      }
    }

    // ── Mocap clip override (covered bones + expressions) ────────────
    // When a clip is active for the current (agent, mood/emote/state),
    // sample it and OVERWRITE the procedural pose on bones it drives.
    // Uncovered bones keep whatever the idle loop wrote — this is how
    // a face-only recording layers cleanly against the body idle.
    // Expressions are written after mood/mouth so the recorded mouth
    // shapes win when the clip covers vowels.
    const mocapRt = mocapRuntimeRef.current;
    if (mocapRt) {
      mocapRt.sampleInto(performance.now() / 1000, mocapSampleRef.current);
      const sample = mocapSampleRef.current;
      const mocapBones = mocapBonesRef.current;
      for (const name of Object.keys(sample.bones) as MocapBone[]) {
        const q = sample.bones[name]!;
        const node = mocapBones[name];
        if (!node) continue;
        // Raw scene bones (finger fallback) have a non-identity rest
        // pose stashed in userData — compose to keep the rig's original
        // finger splay instead of snapping to identity on each frame.
        const rest = node.userData?.mocapRest as THREE.Quaternion | undefined;
        if (rest) {
          _mocapScratchQ.set(q[0], q[1], q[2], q[3]);
          node.quaternion.copy(rest).multiply(_mocapScratchQ);
        } else {
          node.quaternion.set(q[0], q[1], q[2], q[3]);
        }
      }
      if (em) {
        for (const [name, v] of Object.entries(sample.expressions) as [
          MocapExpression,
          number,
        ][]) {
          if (em.getExpression?.(name)) em.setValue(name, v);
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

    // Prefer an actual chest bone for the framing center. VRM models
    // vary wildly in torso proportion — taller VRoid base models have
    // long necks, stylised chibi rigs have a head that covers 40% of
    // the silhouette. A hardcoded 0.55 * (head - hips) over-framed the
    // chibi faces and under-framed the tall rigs. Reading the actual
    // upperChest (or chest) world-Y gives a proportionally correct
    // center regardless of rig; we fall back to 0.55 only when the
    // model genuinely lacks both bones (legacy/minimal rigs).
    const chest =
      humanoid.getNormalizedBoneNode("upperChest") ??
      humanoid.getNormalizedBoneNode("chest");
    const centerX = (headPos.x + hipsPos.x) / 2;
    let centerY: number;
    if (chest) {
      const chestPos = new THREE.Vector3();
      chest.getWorldPosition(chestPos);
      centerY = chestPos.y;
    } else {
      centerY = hipsPos.y + (headPos.y - hipsPos.y) * 0.55;
    }
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

// ── Error boundary for VRM load failures ──────────────────────────────
//
// ``useLoader`` throws when a .vrm 404s, is corrupt, or the parser plugin
// rejects it. Without this boundary the whole Canvas would freeze on a
// stale frame (Suspense doesn't catch thrown errors — only thrown
// Promises), which in OBS mode is indistinguishable from a dead stream.
// The boundary swaps in a neutral placeholder capsule so the spotlight
// stays alive, and surfaces the failure to the parent DOM so a toast or
// banner can be shown.

class VRMLoadErrorBoundary extends Component<
  { children: ReactNode; onError: (message: string) => void },
  { hasError: boolean }
> {
  state = { hasError: false };

  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }

  componentDidCatch(error: Error): void {
    this.props.onError(error.message || "VRM model failed to load");
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return <VRMPlaceholderMesh />;
    }
    return this.props.children;
  }
}

function VRMPlaceholderMesh() {
  // Low-poly capsule roughly character-sized so the spotlight still
  // reads as "someone's here" rather than a blank backdrop.
  return (
    <group position={[0, 1.0, 0]}>
      <mesh>
        <capsuleGeometry args={[0.22, 0.7, 4, 12]} />
        <meshStandardMaterial
          color="#94a3b8"
          roughness={0.8}
          transparent
          opacity={0.55}
        />
      </mesh>
    </group>
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
  emote,
  mocapClipId,
}: Props) {
  const file = useMemo(() => vrmFileForAgent(agent.name), [agent.name]);
  const url = `/vrm/${file}`;
  const [loadError, setLoadError] = useState<string | null>(null);

  // Reset the error state when the underlying file changes — a different
  // agent might have a working asset even if the previous one failed.
  useEffect(() => {
    setLoadError(null);
  }, [url]);

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
        // ``alpha: true`` + transparent clear color lets OBS chromakey
        // (and any non-OBS gradient behind the stage) show through the
        // canvas's empty pixels. Without these the renderer paints the
        // whole tile opaque black and streams lose the character cut-out.
        gl={{ alpha: true, premultipliedAlpha: false }}
        onCreated={({ gl }) => {
          gl.setClearColor(0x000000, 0);
        }}
      >
        {/* Four-point lighting: ambient + key + fill + backlight rim.
            The backlight (-z, slightly above) catches the hair and
            shoulder silhouette, adding depth without needing PBR. */}
        <ambientLight intensity={0.65} />
        <directionalLight position={[1.2, 2, 1.5]} intensity={1.25} />
        <directionalLight position={[-1.5, 1.2, -1]} intensity={0.35} />
        <directionalLight position={[0, 1.8, -2.2]} intensity={0.55} color="#c4b5fd" />
        <VRMLoadErrorBoundary onError={setLoadError}>
          <Suspense fallback={null}>
            <VRMModel
              url={url}
              agentName={agent.name}
              mood={agent.mood}
              state={state ?? agent.state ?? "idle"}
              getMouthAmplitude={getMouthAmplitude}
              spotlight={spotlight}
              cameraResetNonce={cameraResetNonce}
              emote={emote}
              mocapClipId={mocapClipId}
            />
          </Suspense>
        </VRMLoadErrorBoundary>
      </Canvas>
      {loadError && spotlight && (
        <div
          role="status"
          aria-live="polite"
          className="pointer-events-none absolute inset-x-0 bottom-0 bg-rose-900/70 px-3 py-1.5 text-center text-[11px] font-medium text-rose-50 backdrop-blur"
        >
          캐릭터 모델을 불러올 수 없습니다 — {agent.name}
        </div>
      )}
    </div>
  );
}

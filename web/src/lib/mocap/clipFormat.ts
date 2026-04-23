/**
 * Mocap clip — wire format shared between the ``/mocap`` recorder and
 * the VRMCharacter playback path.
 *
 *   version 1
 *   ─────────
 *   bones       : Partial<Record<MocapBone, QuatTrack>>
 *   expressions : Partial<Record<MocapExpression, FloatTrack>>
 *
 * Tracks are uniform-sampled at ``fps`` so the track arrays can omit
 * time stamps — frame ``i`` is at ``i / fps`` seconds. QuatTrack data
 * is flattened ``[x0, y0, z0, w0, x1, …]`` (length = frameCount * 4);
 * FloatTrack data is a flat scalar array (length = frameCount).
 *
 * Absent tracks (i.e. ``bones[name]`` is undefined) mean "don't touch
 * this bone during playback" — the idle pipeline keeps driving it.
 * This is how we express layered clips (e.g. face-only recordings)
 * without a separate masking mechanism.
 */

export const MOCAP_CLIP_VERSION = 1 as const;

/** Humanoid bone names we record, mirroring ``ALLOWED_BONES`` on the
 *  server and the keys of ``VRM.humanoid.getNormalizedBoneNode``. Legs
 *  are intentionally omitted in v1 — webcam mocap quality is poor on
 *  lower-body joints and the noise reads worse than a clean idle.
 *
 *  Finger proximal bones are populated only when the recorder's
 *  HandLandmarker is enabled; otherwise their tracks are absent and the
 *  playback path keeps whatever the idle/gesture loop writes. */
export const MOCAP_BONES = [
  "hips",
  "spine",
  "chest",
  "upperChest",
  "neck",
  "head",
  "leftShoulder",
  "rightShoulder",
  "leftUpperArm",
  "rightUpperArm",
  "leftLowerArm",
  "rightLowerArm",
  "leftHand",
  "rightHand",
  // Hand fingers — full articulation (proximal / intermediate / distal
  // per finger). See ``solveHands`` in ``solver.ts`` for the
  // per-joint relative-angle solver and calibration table. Thumb has
  // no Intermediate joint (VRM 1.0 humanoid spec); the Metacarpal
  // joint is driven from the palm-forward reference to capture CMC
  // opposition (thumb moving across the palm).
  "leftThumbProximal",
  "leftThumbDistal",
  "leftThumbMetacarpal",
  "leftIndexProximal",
  "leftIndexIntermediate",
  "leftIndexDistal",
  "leftMiddleProximal",
  "leftMiddleIntermediate",
  "leftMiddleDistal",
  "leftRingProximal",
  "leftRingIntermediate",
  "leftRingDistal",
  "leftLittleProximal",
  "leftLittleIntermediate",
  "leftLittleDistal",
  "rightThumbProximal",
  "rightThumbDistal",
  "rightThumbMetacarpal",
  "rightIndexProximal",
  "rightIndexIntermediate",
  "rightIndexDistal",
  "rightMiddleProximal",
  "rightMiddleIntermediate",
  "rightMiddleDistal",
  "rightRingProximal",
  "rightRingIntermediate",
  "rightRingDistal",
  "rightLittleProximal",
  "rightLittleIntermediate",
  "rightLittleDistal",
] as const;

export type MocapBone = (typeof MOCAP_BONES)[number];
export const MOCAP_BONE_SET: ReadonlySet<MocapBone> = new Set(MOCAP_BONES);

/** VRM expression slots we record. VRM 1.0 standard five emotions +
 *  procedural vowels (used by the existing lip-sync) + blinks. A clip
 *  that only contains vowels can still layer cleanly against the
 *  idle loop's amplitude-driven lip-sync — our playback path skips
 *  amplitude lip-sync only when the clip provides at least one vowel
 *  track. */
export const MOCAP_EXPRESSIONS = [
  "happy",
  "angry",
  "sad",
  "relaxed",
  "surprised",
  "aa",
  "ih",
  "ou",
  "ee",
  "oh",
  "blink",
  "blinkLeft",
  "blinkRight",
] as const;

export type MocapExpression = (typeof MOCAP_EXPRESSIONS)[number];
export const MOCAP_EXPRESSION_SET: ReadonlySet<MocapExpression> = new Set(
  MOCAP_EXPRESSIONS,
);

export const MOCAP_VOWELS: ReadonlyArray<MocapExpression> = [
  "aa",
  "ih",
  "ou",
  "ee",
  "oh",
];

export interface QuatTrack {
  /** length = frameCount * 4, laid out ``[x,y,z,w, x,y,z,w, …]``. */
  data: number[];
}

export interface FloatTrack {
  /** length = frameCount. */
  data: number[];
}

export interface MocapClip {
  version: 1;
  /** server-assigned uuid (empty pre-upload). */
  id: string;
  name: string;
  /** vrm filename (e.g. ``"konomi.vrm"``) at record time. */
  sourceVrm: string;
  durationS: number;
  fps: number;
  frameCount: number;
  bones: Partial<Record<MocapBone, QuatTrack>>;
  expressions: Partial<Record<MocapExpression, FloatTrack>>;
  meta?: {
    createdAt?: string;
    recordedBy?: string;
  };
}

/** Server-sent summary (no payload). */
export interface ClipSummary {
  id: string;
  owner_user_id: string;
  name: string;
  source_vrm: string;
  duration_s: number;
  fps: number;
  frame_count: number;
  size_bytes: number;
  created_at: string;
  updated_at: string;
}

/** Server-sent binding row. */
export interface BindingRow {
  vrm_file: string;
  trigger_kind: "mood" | "emote" | "state" | "manual";
  trigger_value: string;
  clip_id: string;
  updated_by: string | null;
  updated_at: string;
}

/** Compute the expected frameCount for a ``(durationS, fps)`` pair,
 *  matching the server's tolerance check. */
export function expectedFrameCount(durationS: number, fps: number): number {
  return Math.round(durationS * fps) + 1;
}

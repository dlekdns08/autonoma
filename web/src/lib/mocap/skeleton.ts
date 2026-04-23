/**
 * MediaPipe Pose + Hand landmark connection tables + helpers for the
 * real-time skeleton overlay. Lives separately from ``solver.ts``
 * because the solver is coordinate-agnostic (rotations only) while the
 * overlay is image-plane pixel math — mixing them would pull Three.js
 * into the overlay path and add allocations per frame.
 *
 * Pose indices reference:
 *   https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker
 * Hand indices reference:
 *   https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker
 */

import type { NormalizedLandmark } from "@mediapipe/tasks-vision";

/** Subset of MediaPipe's 33 pose landmarks we actually render. Face
 *  mesh points inside the head are dropped — they crowd the overlay. */
export const POSE_IDX = {
  NOSE: 0,
  LEFT_SHOULDER: 11,
  RIGHT_SHOULDER: 12,
  LEFT_ELBOW: 13,
  RIGHT_ELBOW: 14,
  LEFT_WRIST: 15,
  RIGHT_WRIST: 16,
  LEFT_HIP: 23,
  RIGHT_HIP: 24,
  LEFT_KNEE: 25,
  RIGHT_KNEE: 26,
  LEFT_ANKLE: 27,
  RIGHT_ANKLE: 28,
} as const;

/** Anatomical joint→joint edges. Renderer draws a line segment for
 *  each pair. Excludes duplicate "shortcut" edges that would double-
 *  draw on top of the shoulder/hip/torso triangle. */
export const POSE_CONNECTIONS: ReadonlyArray<readonly [number, number]> = [
  // Head → shoulders (Y-shape approximating neck)
  [POSE_IDX.NOSE, POSE_IDX.LEFT_SHOULDER],
  [POSE_IDX.NOSE, POSE_IDX.RIGHT_SHOULDER],
  // Torso quad
  [POSE_IDX.LEFT_SHOULDER, POSE_IDX.RIGHT_SHOULDER],
  [POSE_IDX.LEFT_SHOULDER, POSE_IDX.LEFT_HIP],
  [POSE_IDX.RIGHT_SHOULDER, POSE_IDX.RIGHT_HIP],
  [POSE_IDX.LEFT_HIP, POSE_IDX.RIGHT_HIP],
  // Arms
  [POSE_IDX.LEFT_SHOULDER, POSE_IDX.LEFT_ELBOW],
  [POSE_IDX.LEFT_ELBOW, POSE_IDX.LEFT_WRIST],
  [POSE_IDX.RIGHT_SHOULDER, POSE_IDX.RIGHT_ELBOW],
  [POSE_IDX.RIGHT_ELBOW, POSE_IDX.RIGHT_WRIST],
  // Legs
  [POSE_IDX.LEFT_HIP, POSE_IDX.LEFT_KNEE],
  [POSE_IDX.LEFT_KNEE, POSE_IDX.LEFT_ANKLE],
  [POSE_IDX.RIGHT_HIP, POSE_IDX.RIGHT_KNEE],
  [POSE_IDX.RIGHT_KNEE, POSE_IDX.RIGHT_ANKLE],
];

/** Distinct joint indices used by ``POSE_CONNECTIONS`` (for dot render). */
export const POSE_JOINTS: ReadonlyArray<number> = Array.from(
  new Set(POSE_CONNECTIONS.flat()),
);

/** MediaPipe hand landmarker returns 21 points per hand — all 21 get
 *  rendered since the hand overlay is small + already meaningful. */
export const HAND_CONNECTIONS: ReadonlyArray<readonly [number, number]> = [
  // Thumb (CMC → MCP → IP → TIP)
  [0, 1], [1, 2], [2, 3], [3, 4],
  // Index
  [0, 5], [5, 6], [6, 7], [7, 8],
  // Middle
  [5, 9], [9, 10], [10, 11], [11, 12],
  // Ring
  [9, 13], [13, 14], [14, 15], [15, 16],
  // Little
  [13, 17], [17, 18], [18, 19], [19, 20],
  // Palm closure (wrist → little-MCP — gives the palm a bottom edge)
  [0, 17],
];

export const HAND_JOINTS: ReadonlyArray<number> = Array.from(
  { length: 21 },
  (_, i) => i,
);

/** Squared Euclidean distance between two landmarks in normalized
 *  image coords. Returns ``Infinity`` if either is missing so the
 *  overlay's diff-colour check falls through to "off" safely. Squared
 *  (not sqrt) to skip a sqrt per frame per joint — the caller
 *  compares against a pre-squared threshold. */
export function landmarkDist2(
  a: NormalizedLandmark | undefined,
  b: NormalizedLandmark | undefined,
): number {
  if (!a || !b) return Infinity;
  const dx = a.x - b.x;
  const dy = a.y - b.y;
  return dx * dx + dy * dy;
}

/** Min visibility (0..1) for a landmark to be drawn. MediaPipe emits
 *  low-confidence landmarks with ghostly positions when the body is
 *  partially out of frame — drawing those produces a flickering mess. */
export const MIN_VISIBILITY = 0.3;

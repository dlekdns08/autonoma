/**
 * Alignment score: how well does the VRM's solved pose match the raw
 * MediaPipe pose landmarks it was driven from? A scalar percentage
 * (0..1) that surfaces as a badge on the mocap page — useful operator
 * feedback for IK tuning.
 *
 * The comparison is done in 2D image space:
 *   - Landmarks: NormalizedLandmark.x/.y (0..1, image-left = 0, image-
 *     top = 0). We work in the raw MediaPipe frame, pre-CSS-mirror.
 *   - VRM bones: world-space 3D positions (meters). We project onto XY
 *     by dropping Z, then align axes with image space:
 *       * Image Y grows DOWN, VRM Y grows UP → negate Y.
 *       * For a mirrored (selfie) render the scene is flipped on X so
 *         user-left shows on screen-left; the raw landmark's user-left
 *         shoulder (landmark[11]) sits at a HIGHER x in the MediaPipe
 *         frame (camera sees user reversed), and the VRM's leftShoulder
 *         is on the VRM's +X side. To compare directions we negate VRM
 *         dx when mirrored.
 *
 * For each bone pair we compare normalised 2D direction vectors via
 * cosine similarity. Cosine ∈ [-1, 1] is remapped to [0, 1] (perfect
 * opposite → 0, identical direction → 1) and averaged across all bone
 * pairs with both endpoints present. Magnitude is intentionally
 * ignored — we already know the user and VRM have different limb
 * scales; orientation is the useful signal here.
 */

import type { NormalizedLandmark } from "@mediapipe/tasks-vision";

/** World-space positions for the VRM bones we overlay / compare. All
 *  fields are optional because a given rig may omit any of them (the
 *  consumer tolerates missing data by skipping those pairs). */
export interface VrmBoneWorldPositions {
  nose?: [number, number, number];
  leftShoulder?: [number, number, number];
  rightShoulder?: [number, number, number];
  leftElbow?: [number, number, number];
  rightElbow?: [number, number, number];
  leftWrist?: [number, number, number];
  rightWrist?: [number, number, number];
  leftHip?: [number, number, number];
  rightHip?: [number, number, number];
  leftKnee?: [number, number, number];
  rightKnee?: [number, number, number];
  leftAnkle?: [number, number, number];
  rightAnkle?: [number, number, number];
}

/** Build the list of bone pairs to compare. When the render is
 *  mirrored (selfie preview), the raw landmark at index 11 (user's
 *  anatomical LEFT shoulder) corresponds to the VRM's RIGHT bone after
 *  mirroring, and vice versa — so we swap the VRM-side keys. */
function bonePairs(
  mirror: boolean,
): Array<[number, number, keyof VrmBoneWorldPositions, keyof VrmBoneWorldPositions]> {
  return mirror
    ? [
        [11, 13, "rightShoulder", "rightElbow"],
        [13, 15, "rightElbow", "rightWrist"],
        [12, 14, "leftShoulder", "leftElbow"],
        [14, 16, "leftElbow", "leftWrist"],
        [23, 25, "rightHip", "rightKnee"],
        [25, 27, "rightKnee", "rightAnkle"],
        [24, 26, "leftHip", "leftKnee"],
        [26, 28, "leftKnee", "leftAnkle"],
      ]
    : [
        [11, 13, "leftShoulder", "leftElbow"],
        [13, 15, "leftElbow", "leftWrist"],
        [12, 14, "rightShoulder", "rightElbow"],
        [14, 16, "rightElbow", "rightWrist"],
        [23, 25, "leftHip", "leftKnee"],
        [25, 27, "leftKnee", "leftAnkle"],
        [24, 26, "rightHip", "rightKnee"],
        [26, 28, "rightKnee", "rightAnkle"],
      ];
}

/** Return a [0..1] alignment score, or ``null`` if there isn't enough
 *  data to score a single bone pair. */
export function computeAlignment(
  landmarks: NormalizedLandmark[] | null,
  vrmPositions: VrmBoneWorldPositions | null,
  mirror: boolean,
): number | null {
  if (!landmarks || landmarks.length === 0 || !vrmPositions) return null;

  const pairs = bonePairs(mirror);
  let total = 0;
  let count = 0;

  for (const [lmA, lmB, vrmA, vrmB] of pairs) {
    const la = landmarks[lmA];
    const lb = landmarks[lmB];
    const va = vrmPositions[vrmA];
    const vb = vrmPositions[vrmB];
    if (!la || !lb || !va || !vb) continue;

    const ldx = lb.x - la.x;
    const ldy = lb.y - la.y;
    const lmag = Math.hypot(ldx, ldy);
    if (lmag < 1e-6) continue;

    const vdxRaw = vb[0] - va[0];
    const vdx = mirror ? -vdxRaw : vdxRaw;
    // VRM Y-up → image Y-down.
    const vdy = -(vb[1] - va[1]);
    const vmag = Math.hypot(vdx, vdy);
    if (vmag < 1e-6) continue;

    const cos = (ldx * vdx + ldy * vdy) / (lmag * vmag);
    // Remap cosine from [-1, 1] to [0, 1]. Clamp for floating-point
    // slop pushing values slightly outside the range.
    const score = Math.max(0, Math.min(1, (cos + 1) * 0.5));
    total += score;
    count++;
  }

  if (count === 0) return null;
  return total / count;
}

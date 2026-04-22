/**
 * MediaPipe Tasks Vision → VRM humanoid adapter.
 *
 * Input  — one ``FaceLandmarkerResult`` + ``PoseLandmarkerResult`` pair
 *          captured at the same video timestamp.
 * Output — a ``ClipSample`` (bone quaternions in humanoid-normalized
 *          space + expression scalars) ready to apply to a VRM or append
 *          to a recording buffer.
 *
 * Why bypass Kalidokit for face:
 *   Tasks Vision already produces 52 ARKit blendshapes and a
 *   face-transformation matrix. That matrix is a rigid head pose in a
 *   known coordinate system, so we can derive neck/head rotation
 *   directly. For eyes + mouth the blendshapes are more accurate than
 *   Kalidokit's heuristic-based face solve.
 *
 * Why use Kalidokit for body:
 *   Kalidokit.Pose.solve does the limb-frame math (shoulder-rooted
 *   rotations computed from neighbouring keypoints, elbow flex axis,
 *   etc.). Doing it by hand is possible but Kalidokit has already tuned
 *   the edge cases — crossing arms, shoulder roll — against real
 *   webcam footage. We feed it the Tasks Vision output in the shape
 *   the older Holistic API produced and swap L↔R on the way out so the
 *   VRM mirrors the user (webcam is a mirror by convention).
 *
 * Smoothing:
 *   One-Euro filter per bone/expression. Jitter at 30fps is the single
 *   biggest source of "that looks AI-generated" energy; the adaptive
 *   cutoff handles the tradeoff between responsiveness and calm.
 */

import * as Kalidokit from "kalidokit";
import type {
  FaceLandmarkerResult,
  HandLandmarkerResult,
  PoseLandmarkerResult,
} from "@mediapipe/tasks-vision";
import * as THREE from "three";
import type { MocapBone, MocapExpression } from "./clipFormat";
import type { ClipSample } from "./clipPlayer";
import { OneEuroQuat, OneEuroScalar, type OneEuroConfig } from "./oneEuro";

/** Which Tasks Vision blendshapes map to which VRM 1.0 expressions. The
 *  ARKit shapes are a superset — we blend the relevant ones per VRM slot
 *  so stronger ARKit motion reads as stronger VRM expression without
 *  losing subtler shapes. */
const BLENDSHAPE_TO_VRM: Record<string, [MocapExpression, number][]> = {
  // Eyes → blinks. Tasks Vision reports left/right separately.
  eyeBlinkLeft: [["blinkLeft", 1], ["blink", 0.5]],
  eyeBlinkRight: [["blinkRight", 1], ["blink", 0.5]],
  // Smile + cheek puff → happy.
  mouthSmileLeft: [["happy", 0.6]],
  mouthSmileRight: [["happy", 0.6]],
  cheekSquintLeft: [["happy", 0.2]],
  cheekSquintRight: [["happy", 0.2]],
  // Frown + brow down → angry.
  browDownLeft: [["angry", 0.5]],
  browDownRight: [["angry", 0.5]],
  mouthFrownLeft: [["angry", 0.4], ["sad", 0.3]],
  mouthFrownRight: [["angry", 0.4], ["sad", 0.3]],
  // Inner brow raise + mouth down → sad.
  browInnerUp: [["sad", 0.6]],
  // Jaw open + relaxed brows → relaxed (drifts toward neutral smile).
  mouthShrugUpper: [["relaxed", 0.3]],
  // Wide eyes + raised brows → surprised.
  eyeWideLeft: [["surprised", 0.5]],
  eyeWideRight: [["surprised", 0.5]],
  browOuterUpLeft: [["surprised", 0.3]],
  browOuterUpRight: [["surprised", 0.3]],
  // Mouth shapes for vowels. These are coarse — ARKit doesn't separate
  // vowels, so we derive each vowel's weight from the jaw-open / lip
  // shape combinations that read as that vowel.
  jawOpen: [["aa", 1]],
  mouthFunnel: [["ou", 1]],
  mouthPucker: [["ou", 0.6], ["oh", 0.4]],
  mouthClose: [["ih", 0.5]],
  mouthStretchLeft: [["ee", 0.5]],
  mouthStretchRight: [["ee", 0.5]],
};

/** Sum blendshapes into VRM expression slots, clamped 0..1. */
function mapBlendshapes(
  categories: { categoryName: string; score: number }[],
  out: Partial<Record<MocapExpression, number>>,
): void {
  for (const c of categories) {
    const entries = BLENDSHAPE_TO_VRM[c.categoryName];
    if (!entries) continue;
    for (const [slot, weight] of entries) {
      out[slot] = (out[slot] ?? 0) + c.score * weight;
    }
  }
  for (const k of Object.keys(out) as MocapExpression[]) {
    out[k] = Math.max(0, Math.min(1, out[k] ?? 0));
  }
}

/** Convert a 4x4 column-major matrix (MediaPipe ships these) into a
 *  three.js Matrix4, then extract a quaternion.  */
function quatFromMatrix(
  m: Float32Array | number[],
  out: [number, number, number, number],
): void {
  const mat = _scratchMat.fromArray(m as number[]);
  mat.decompose(_scratchVec, _scratchQuat, _scratchVec2);
  out[0] = _scratchQuat.x;
  out[1] = _scratchQuat.y;
  out[2] = _scratchQuat.z;
  out[3] = _scratchQuat.w;
}

const _scratchMat = new THREE.Matrix4();
const _scratchVec = new THREE.Vector3();
const _scratchVec2 = new THREE.Vector3();
const _scratchQuat = new THREE.Quaternion();

interface LM2D {
  x: number;
  y: number;
  z: number;
  visibility?: number;
}

/** Adapt Tasks Vision pose output to Kalidokit's expected shape. The
 *  older Holistic API returned arrays of ``{x, y, z, visibility}``;
 *  Tasks Vision is the same shape but with additional ``presence`` and
 *  per-landmark ``visibility`` fields we ignore. */
function toKalidokitPose(
  landmarks: LM2D[],
  worldLandmarks: LM2D[],
): {
  pose2d: { x: number; y: number; z: number; visibility?: number }[];
  pose3d: { x: number; y: number; z: number; visibility?: number }[];
} {
  const pose2d = landmarks.map((l) => ({
    x: l.x,
    y: l.y,
    z: l.z ?? 0,
    visibility: l.visibility,
  }));
  const pose3d = worldLandmarks.map((l) => ({
    x: l.x,
    y: l.y,
    z: l.z ?? 0,
    visibility: l.visibility,
  }));
  return { pose2d, pose3d };
}

/** Kalidokit outputs Euler angles per bone; convert to quaternion. */
function eulerToQuat(
  e: { x: number; y: number; z: number } | undefined,
  order: THREE.EulerOrder,
  out: [number, number, number, number],
): void {
  if (!e) {
    out[0] = 0;
    out[1] = 0;
    out[2] = 0;
    out[3] = 1;
    return;
  }
  _scratchEuler.set(e.x, e.y, e.z, order);
  _scratchQuat.setFromEuler(_scratchEuler);
  out[0] = _scratchQuat.x;
  out[1] = _scratchQuat.y;
  out[2] = _scratchQuat.z;
  out[3] = _scratchQuat.w;
}

const _scratchEuler = new THREE.Euler();

/** Per-bone Euler order — matches the one VRMCharacter's gesture code
 *  uses so the recorded rotations play back cleanly. Finger proximals
 *  don't appear here because ``solveHands`` writes their quaternions
 *  directly (no Euler intermediate). */
const BONE_EULER_ORDER: Partial<Record<MocapBone, THREE.EulerOrder>> = {
  hips: "XYZ",
  spine: "XYZ",
  chest: "YXZ",
  upperChest: "YXZ",
  neck: "YXZ",
  head: "YXZ",
  leftShoulder: "YXZ",
  rightShoulder: "YXZ",
  leftUpperArm: "YXZ",
  rightUpperArm: "YXZ",
  leftLowerArm: "YXZ",
  rightLowerArm: "YXZ",
  leftHand: "YXZ",
  rightHand: "YXZ",
};

// MediaPipe hand landmark indices — 21 per hand.
// Reference: https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker
//   0: WRIST
//   1-4: THUMB       (CMC, MCP, IP, TIP)
//   5-8: INDEX       (MCP, PIP, DIP, TIP)
//   9-12: MIDDLE     (MCP, PIP, DIP, TIP)
//   13-16: RING      (MCP, PIP, DIP, TIP)
//   17-20: LITTLE    (MCP, PIP, DIP, TIP)
const WRIST_LM = 0;
const MIDDLE_MCP_LM = 9;

/** Joint type — selects calibration constants (rest/fist/out-range)
 *  and the VRM-local rotation axis. Different joints have different
 *  anatomical ranges AND different local-axis conventions on the rig:
 *  non-thumb fingers flex around +Z while the thumb (anatomically
 *  rotated ~90° from the other fingers) flexes around -X. The
 *  axis-finding was validated with the finger-axis test harness in
 *  ``MocapPreview``. */
type JointType =
  | "proximal"        // MCP joint of index/middle/ring/little
  | "intermediate"    // PIP joint
  | "distal"          // DIP joint
  | "thumbProximal"   // MCP of thumb (measured relative to metacarpal)
  | "thumbDistal";    // IP of thumb (measured relative to proximal)

type RotationAxis = "x" | "y" | "z";

interface JointCalibration {
  restRad: number;
  fistRad: number;
  outRangeRad: number;
  /** VRM-local rotation axis where "flexion" lives for this joint. */
  axis: RotationAxis;
  /** Flip the sign of the applied rotation. True when the axis's
   *  positive direction goes AWAY from flexion (e.g. the thumb's local
   *  +X points toward the tip and flexion requires -X). */
  flipSign?: boolean;
}

const CALIBRATION: Record<JointType, JointCalibration> = {
  // Validated empirically with Midori rig. Any mis-tuning shows as
  // "joint feels stuck at rest" (rest too high) or "joint over-curls
  // before user closes" (rest too low).
  proximal: {
    restRad: (50 * Math.PI) / 180,
    fistRad: (90 * Math.PI) / 180,
    outRangeRad: (90 * Math.PI) / 180,
    axis: "z",
  },
  intermediate: {
    restRad: (10 * Math.PI) / 180,
    fistRad: (90 * Math.PI) / 180,
    outRangeRad: (90 * Math.PI) / 180,
    axis: "z",
  },
  distal: {
    restRad: (10 * Math.PI) / 180,
    fistRad: (70 * Math.PI) / 180,
    outRangeRad: (70 * Math.PI) / 180,
    axis: "z",
  },
  // Thumb joints: +Z produces an invisible twist on this rig family
  // (bone's local +Z is along the thumb's own axis). Local -X is the
  // anatomical flexion direction — see axis-test report where pressing
  // X caused the thumb proximal to bend toward the palm.
  //
  // The thumb MCP (``thumbProximal``) range is much narrower than the
  // IP (``thumbDistal``): when the whole thumb moves in opposition,
  // the CMC→MCP and MCP→IP landmark segments rotate together, so the
  // measured RELATIVE angle only varies ~5°-30° even at a full fist.
  // We tune the rest/fist bounds tight around that observed range so
  // the ~25° of signal maps to ~60° of visible bone rotation.
  thumbProximal: {
    restRad: (5 * Math.PI) / 180,
    fistRad: (30 * Math.PI) / 180,
    outRangeRad: (85 * Math.PI) / 180,
    axis: "x",
    flipSign: true,
  },
  thumbDistal: {
    restRad: (10 * Math.PI) / 180,
    fistRad: (60 * Math.PI) / 180,
    outRangeRad: (50 * Math.PI) / 180,
    axis: "x",
    flipSign: true,
  },
};

/** Parent direction reference for a joint's curl metric.
 *  - ``"palm"`` — use palm-forward (wrist→middle-MCP). Used by
 *    non-thumb proximals because their rest-pose direction aligns with
 *    the palm's long axis.
 *  - ``[from, to]`` — use a specific landmark segment as the parent
 *    direction. Used for every joint below proximal (intermediate /
 *    distal) and for the thumb (whose rest-pose is orthogonal to the
 *    palm, so the parent-segment reference is more accurate). */
type ParentRef = "palm" | readonly [number, number];

/** [bone, parent-dir, child-start, child-end, joint-type]. Each entry
 *  computes ``curl = acos(dot(parent_dir, child_dir))`` and applies
 *  the calibrated rotation to ``bone`` around local Z. */
type FingerJoint = readonly [
  bone: MocapBone,
  parent: ParentRef,
  childFrom: number,
  childTo: number,
  joint: JointType,
];

const LEFT_JOINTS: readonly FingerJoint[] = [
  // Thumb (2 of 3 segments — thumbMetacarpal skipped; see module doc)
  ["leftThumbProximal",      [1, 2], 2, 3, "thumbProximal"],
  ["leftThumbDistal",        [2, 3], 3, 4, "thumbDistal"],
  // Index
  ["leftIndexProximal",      "palm", 5, 6, "proximal"],
  ["leftIndexIntermediate",  [5, 6], 6, 7, "intermediate"],
  ["leftIndexDistal",        [6, 7], 7, 8, "distal"],
  // Middle
  ["leftMiddleProximal",     "palm", 9, 10, "proximal"],
  ["leftMiddleIntermediate", [9, 10], 10, 11, "intermediate"],
  ["leftMiddleDistal",       [10, 11], 11, 12, "distal"],
  // Ring
  ["leftRingProximal",       "palm", 13, 14, "proximal"],
  ["leftRingIntermediate",   [13, 14], 14, 15, "intermediate"],
  ["leftRingDistal",         [14, 15], 15, 16, "distal"],
  // Little
  ["leftLittleProximal",     "palm", 17, 18, "proximal"],
  ["leftLittleIntermediate", [17, 18], 18, 19, "intermediate"],
  ["leftLittleDistal",       [18, 19], 19, 20, "distal"],
];
const RIGHT_JOINTS: readonly FingerJoint[] = [
  ["rightThumbProximal",      [1, 2], 2, 3, "thumbProximal"],
  ["rightThumbDistal",        [2, 3], 3, 4, "thumbDistal"],
  ["rightIndexProximal",      "palm", 5, 6, "proximal"],
  ["rightIndexIntermediate",  [5, 6], 6, 7, "intermediate"],
  ["rightIndexDistal",        [6, 7], 7, 8, "distal"],
  ["rightMiddleProximal",     "palm", 9, 10, "proximal"],
  ["rightMiddleIntermediate", [9, 10], 10, 11, "intermediate"],
  ["rightMiddleDistal",       [10, 11], 11, 12, "distal"],
  ["rightRingProximal",       "palm", 13, 14, "proximal"],
  ["rightRingIntermediate",   [13, 14], 14, 15, "intermediate"],
  ["rightRingDistal",         [14, 15], 15, 16, "distal"],
  ["rightLittleProximal",     "palm", 17, 18, "proximal"],
  ["rightLittleIntermediate", [17, 18], 18, 19, "intermediate"],
  ["rightLittleDistal",       [18, 19], 19, 20, "distal"],
];

// Pre-allocated scratch vectors / quaternions used by ``solveHands``.
// Keeping them module-scoped means the per-frame solve does zero
// allocations in steady state.
const _hWrist = new THREE.Vector3();
const _hMid = new THREE.Vector3();
const _hParentA = new THREE.Vector3();
const _hParentB = new THREE.Vector3();
const _hChildA = new THREE.Vector3();
const _hChildB = new THREE.Vector3();
const _parentDir = new THREE.Vector3();
const _childDir = new THREE.Vector3();
const _palmY = new THREE.Vector3();
const _curlEuler = new THREE.Euler();
const _handQ = new THREE.Quaternion();

export interface SolverOptions {
  /** Mirror the webcam on horizontal axis so the user's left hand drives
   *  the VRM's left hand (webcam is a mirror by convention). Default
   *  true — turn off only for non-selfie sources. */
  mirror?: boolean;
  /** Filter config applied to every output. Defaults are chosen to
   *  match natural webcam conditions (30fps, well-lit). */
  oneEuro?: OneEuroConfig;
}

/** Reusable per-source solver. Owns a bank of One-Euro filters so
 *  repeated calls share smoothing state. */
export class MocapSolver {
  private readonly mirror: boolean;
  private readonly cfg: OneEuroConfig | undefined;
  private readonly quatFilters: Partial<Record<MocapBone, OneEuroQuat>> = {};
  private readonly scalarFilters: Partial<Record<MocapExpression, OneEuroScalar>> = {};
  private readonly scratch: [number, number, number, number] = [0, 0, 0, 1];

  constructor(opts: SolverOptions = {}) {
    this.mirror = opts.mirror ?? true;
    this.cfg = opts.oneEuro;
  }

  reset(): void {
    for (const f of Object.values(this.quatFilters)) f?.reset();
    for (const f of Object.values(this.scalarFilters)) f?.reset();
  }

  private quatFilter(name: MocapBone): OneEuroQuat {
    let f = this.quatFilters[name];
    if (!f) {
      f = new OneEuroQuat(this.cfg);
      this.quatFilters[name] = f;
    }
    return f;
  }

  private scalarFilter(name: MocapExpression): OneEuroScalar {
    let f = this.scalarFilters[name];
    if (!f) {
      f = new OneEuroScalar(this.cfg);
      this.scalarFilters[name] = f;
    }
    return f;
  }

  /** Resolve one frame. Any input can be null — e.g. face-only
   *  recording omits pose + hands. Output buffer is reused across calls,
   *  so don't retain references to ``out.bones[name]`` between frames. */
  solveInto(
    face: FaceLandmarkerResult | null,
    pose: PoseLandmarkerResult | null,
    hands: HandLandmarkerResult | null,
    tsSec: number,
    out: ClipSample,
  ): void {
    for (const k of Object.keys(out.bones)) {
      delete out.bones[k as MocapBone];
    }
    for (const k of Object.keys(out.expressions)) {
      delete out.expressions[k as MocapExpression];
    }
    // Diagnostic state is per-frame; reset before per-hand math
    // accumulates into it.
    this.latestFingerMaxCurl = 0;
    if (face) this.solveFace(face, tsSec, out);
    if (pose) this.solvePose(pose, tsSec, out);
    if (hands) this.solveHands(hands, tsSec, out);
  }

  private solveFace(
    face: FaceLandmarkerResult,
    tsSec: number,
    out: ClipSample,
  ): void {
    // 1) Blendshapes → VRM expressions.
    const shapes = face.faceBlendshapes?.[0]?.categories;
    if (shapes) {
      const raw: Partial<Record<MocapExpression, number>> = {};
      mapBlendshapes(shapes, raw);
      for (const [name, v] of Object.entries(raw) as [MocapExpression, number][]) {
        const smoothed = this.scalarFilter(name).filter(v, tsSec);
        out.expressions[name] = smoothed;
      }
    }
    // 2) Head pose from the facial transformation matrix. Tasks Vision
    //    ships one 4x4 matrix per tracked face; we only use face #0.
    const mats = face.facialTransformationMatrixes;
    const mat = mats?.[0]?.data;
    if (mat) {
      quatFromMatrix(mat, this.scratch);
      // Mirror the rotation's Y/Z around the vertical axis so a head
      // tilt-left in the mirror drives a head tilt-left on the VRM.
      // For a rotation (x, y, z, w), mirroring across X → (x, -y, -z, w).
      if (this.mirror) {
        this.scratch[1] = -this.scratch[1];
        this.scratch[2] = -this.scratch[2];
      }
      // Split evenly between neck and head so the motion reads as a
      // natural spine chain rather than a bobblehead.
      const half: [number, number, number, number] = [
        this.scratch[0] * 0.5,
        this.scratch[1] * 0.5,
        this.scratch[2] * 0.5,
        // Half-rotation: w component = cos(θ/2) ≈ linear blend of w+1 halved.
        (this.scratch[3] + 1) * 0.5,
      ];
      const mag = Math.hypot(half[0], half[1], half[2], half[3]) || 1;
      half[0] /= mag;
      half[1] /= mag;
      half[2] /= mag;
      half[3] /= mag;
      this.writeBoneSmoothed("neck", half, tsSec, out);
      this.writeBoneSmoothed("head", half, tsSec, out);
    }
  }

  private solvePose(
    pose: PoseLandmarkerResult,
    tsSec: number,
    out: ClipSample,
  ): void {
    const landmarks = pose.landmarks?.[0];
    const world = pose.worldLandmarks?.[0];
    if (!landmarks || !world) return;
    const { pose2d, pose3d } = toKalidokitPose(landmarks, world);
    let rig;
    try {
      rig = Kalidokit.Pose.solve(pose3d, pose2d, {
        runtime: "mediapipe",
        enableLegs: false,
      });
    } catch {
      return;
    }
    if (!rig) return;
    // Kalidokit "left" / "right" are from the webcam's perspective. If
    // mirrored (default), swap so the VRM's left matches the user's
    // visual left in the preview.
    const left = this.mirror ? rig.RightUpperArm : rig.LeftUpperArm;
    const right = this.mirror ? rig.LeftUpperArm : rig.RightUpperArm;
    const leftLower = this.mirror ? rig.RightLowerArm : rig.LeftLowerArm;
    const rightLower = this.mirror ? rig.LeftLowerArm : rig.RightLowerArm;
    const leftHand = this.mirror ? rig.RightHand : rig.LeftHand;
    const rightHand = this.mirror ? rig.LeftHand : rig.RightHand;

    this.writeBoneFromEuler("leftUpperArm", left, tsSec, out);
    this.writeBoneFromEuler("rightUpperArm", right, tsSec, out);
    this.writeBoneFromEuler("leftLowerArm", leftLower, tsSec, out);
    this.writeBoneFromEuler("rightLowerArm", rightLower, tsSec, out);
    this.writeBoneFromEuler("leftHand", leftHand, tsSec, out);
    this.writeBoneFromEuler("rightHand", rightHand, tsSec, out);
    this.writeBoneFromEuler("hips", rig.Hips?.rotation, tsSec, out);
    this.writeBoneFromEuler("spine", rig.Spine, tsSec, out);
  }

  private writeBoneFromEuler(
    name: MocapBone,
    euler: { x: number; y: number; z: number } | undefined,
    tsSec: number,
    out: ClipSample,
  ): void {
    if (!euler) return;
    // Kalidokit produces Euler angles in radians under its own sign
    // convention. When mirroring, flip X+Y so mirrored limbs rotate
    // correctly around the VRM's local axes.
    const src = this.mirror
      ? { x: euler.x, y: -euler.y, z: -euler.z }
      : euler;
    const order = BONE_EULER_ORDER[name] ?? "XYZ";
    eulerToQuat(src, order, this.scratch);
    this.writeBoneSmoothed(name, this.scratch, tsSec, out);
  }

  private writeBoneSmoothed(
    name: MocapBone,
    q: [number, number, number, number],
    tsSec: number,
    out: ClipSample,
  ): void {
    const pool: [number, number, number, number] = out.bones[name] ?? [0, 0, 0, 1];
    this.quatFilter(name).filter(q[0], q[1], q[2], q[3], tsSec, pool);
    out.bones[name] = pool;
  }

  /** Drive each hand's finger-proximal bones from a HandLandmarker
   *  result. Only one hand of each handedness wins — if MediaPipe
   *  detects two "Left" hands (rare but possible with two people in
   *  frame) we take the first and ignore the rest. ``latestHandCount``
   *  / ``latestHandSides`` are refreshed so callers can surface a live
   *  diagnostic ("손 2개 감지").
   *
   *  Handedness in MediaPipe HandLandmarker: the model assumes a
   *  selfie-mirrored input, so when we feed it raw (unmirrored)
   *  camera frames its ``"Left"`` label corresponds to the user's
   *  anatomical RIGHT hand and vice-versa. We flip back to anatomical
   *  here, then apply the standard mirror-swap so the VRM mirrors the
   *  user visually (same side of screen moves together — matching the
   *  convention ``solvePose`` uses for the arms).
   */
  private solveHands(
    result: HandLandmarkerResult,
    tsSec: number,
    out: ClipSample,
  ): void {
    const hands = result.landmarks;
    const sides = result.handednesses;
    this.latestHandCount = 0;
    this.latestHandSides.left = false;
    this.latestHandSides.right = false;
    if (!hands || !sides) return;
    const seenLeft = { done: false };
    const seenRight = { done: false };
    for (let i = 0; i < hands.length; i++) {
      const lm = hands[i];
      const mpLabel = sides[i]?.[0]?.categoryName;
      if (!lm || lm.length < 21 || !mpLabel) continue;
      // MediaPipe selfie-mirror assumption → invert to get the user's
      // anatomical side.
      const userIsLeft = mpLabel === "Right";
      // Standard mirror swap — same convention as ``solvePose`` so the
      // arm and the hand attached to it end up on the same VRM side.
      const vrmIsLeft = this.mirror ? !userIsLeft : userIsLeft;
      if (vrmIsLeft && seenLeft.done) continue;
      if (!vrmIsLeft && seenRight.done) continue;
      if (vrmIsLeft) seenLeft.done = true;
      else seenRight.done = true;
      this.latestHandCount++;
      if (vrmIsLeft) this.latestHandSides.left = true;
      else this.latestHandSides.right = true;
      this.solveOneHand(lm, vrmIsLeft, tsSec, out);
    }
  }

  /** Full-articulation finger solver. For each of 14 finger joints per
   *  hand (proximal + intermediate + distal for index/middle/ring/
   *  little, plus proximal + distal for thumb) we compute:
   *
   *    curl = acos(dot(parent_direction, child_direction))
   *
   *  where ``parent_direction`` is either the palm-forward axis
   *  (wrist→middle-MCP, for non-thumb proximals) or the parent bone's
   *  landmark segment (for every other joint). Because VRM bone
   *  transforms are stored parent-relative, using the parent's current
   *  direction as the rest reference means our per-joint rotation
   *  composes naturally through the finger chain.
   *
   *  Per-joint-type calibration (see ``CALIBRATION``) normalises the
   *  raw curl range to a visible bone rotation — PIP/DIP rest near 10°
   *  while proximal rests near 50°, so a single global remap would
   *  leave intermediate/distal dead through half their range.
   *
   *  Axis + chirality: non-thumb finger joints rotate around the VRM
   *  bone's local Z (validated with the finger-axis test harness — X
   *  is an invisible twist, Y is abduction, Z is flexion). The thumb
   *  uses local -X instead because the thumb bones are anatomically
   *  rotated ~90° from the other fingers. Right hand uses the opposite
   *  sign overall — empirically the normalized humanoid doesn't mirror
   *  finger-bone local axes per-side. Both per-joint axis and per-hand
   *  sign are driven from the ``CALIBRATION`` table.
   *
   *  Trade-off: still curl-only — no finger spread (abduction) and no
   *  thumb opposition. Those need explicit 2nd-axis rotation work.
   */
  private solveOneHand(
    lm: { x: number; y: number; z: number }[],
    vrmIsLeft: boolean,
    tsSec: number,
    out: ClipSample,
  ): void {
    _hWrist.set(lm[WRIST_LM].x, lm[WRIST_LM].y, lm[WRIST_LM].z);
    _hMid.set(lm[MIDDLE_MCP_LM].x, lm[MIDDLE_MCP_LM].y, lm[MIDDLE_MCP_LM].z);

    // Palm-forward direction (wrist → middle-MCP). Cached so every
    // "palm"-referenced proximal can read it without recomputation.
    _palmY.copy(_hMid).sub(_hWrist);
    if (_palmY.lengthSq() < 1e-10) return;
    _palmY.normalize();

    const joints = vrmIsLeft ? LEFT_JOINTS : RIGHT_JOINTS;
    // Right hand's local Z is flipped relative to the left on VRoid-
    // family rigs — see docstring "chirality" paragraph.
    const curlSign = vrmIsLeft ? 1 : -1;

    for (const [boneName, parent, childFrom, childTo, jointType] of joints) {
      // --- Parent direction (rest reference for this joint) ---
      let parentVec: THREE.Vector3;
      if (parent === "palm") {
        parentVec = _palmY;
      } else {
        const [pFrom, pTo] = parent;
        _hParentA.set(lm[pFrom].x, lm[pFrom].y, lm[pFrom].z);
        _hParentB.set(lm[pTo].x, lm[pTo].y, lm[pTo].z);
        _parentDir.copy(_hParentB).sub(_hParentA);
        if (_parentDir.lengthSq() < 1e-10) continue;
        _parentDir.normalize();
        parentVec = _parentDir;
      }

      // --- Child direction (this bone's actual direction) ---
      _hChildA.set(lm[childFrom].x, lm[childFrom].y, lm[childFrom].z);
      _hChildB.set(lm[childTo].x, lm[childTo].y, lm[childTo].z);
      _childDir.copy(_hChildB).sub(_hChildA);
      if (_childDir.lengthSq() < 1e-10) continue;
      _childDir.normalize();

      // --- Curl angle, calibrated + signed ---
      const cos = Math.max(-1, Math.min(1, parentVec.dot(_childDir)));
      const curl = Math.acos(cos);
      // Track peak across ALL joints for the "is anything moving?"
      // diagnostic. Proximals dominate at open/close; intermediate/
      // distal reach similar magnitudes only at full fist.
      this.latestFingerMaxCurl = Math.max(this.latestFingerMaxCurl, curl);

      const cal = CALIBRATION[jointType];
      const span = cal.fistRad - cal.restRad;
      const norm =
        span > 1e-6 ? Math.max(0, Math.min(1, (curl - cal.restRad) / span)) : 0;
      const sign = (cal.flipSign ? -1 : 1) * curlSign;
      const boneRot = norm * cal.outRangeRad * sign;

      // Dispatch on ``cal.axis`` — each joint type has its own local
      // flexion axis (validated against the rig via the axis test
      // harness). Branches compile to a trivial jump table.
      switch (cal.axis) {
        case "x":
          _curlEuler.set(boneRot, 0, 0, "XYZ");
          break;
        case "y":
          _curlEuler.set(0, boneRot, 0, "XYZ");
          break;
        case "z":
          _curlEuler.set(0, 0, boneRot, "XYZ");
          break;
      }
      _handQ.setFromEuler(_curlEuler);
      this.writeBoneSmoothed(
        boneName,
        [_handQ.x, _handQ.y, _handQ.z, _handQ.w],
        tsSec,
        out,
      );
    }
  }

  // ── Diagnostics (read by the /mocap page for the "hands detected"
  // status strip). Updated inside ``solveHands`` each frame. ──────────
  latestHandCount = 0;
  readonly latestHandSides = { left: false, right: false };
  /** Peak curl angle (radians) observed across all fingers this frame.
   *  Reset on each ``solveInto`` via the frame-max pattern — handy for
   *  "is anything happening?" readouts. */
  latestFingerMaxCurl = 0;
}

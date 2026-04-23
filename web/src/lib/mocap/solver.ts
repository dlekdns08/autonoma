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
 *   any heuristic-based face solver.
 *
 * Why a hand-written landmark IK for body (v4):
 *   Kalidokit's Pose.solve produced Euler angles per bone but dropped
 *   half the available signal — spine curve, shoulder shrug, arm roll,
 *   hip yaw, entire lower body. With MediaPipe Pose's ``worldLandmarks``
 *   already in metres and the skeleton overlay confirming landmark
 *   quality is good, it's cheaper to IK directly: torso frame from hip
 *   + shoulder landmarks, spine chain as a cube-root slerp of the
 *   torso-minus-yaw rotation, arms/legs as ``setFromUnitVectors`` in
 *   the parent bone's local frame. L↔R is swapped when ``mirror`` is
 *   on so the VRM tracks the user's visual side (webcam convention).
 *
 * Smoothing:
 *   One-Euro filter per bone/expression. Jitter at 30fps is the single
 *   biggest source of "that looks AI-generated" energy; the adaptive
 *   cutoff handles the tradeoff between responsiveness and calm.
 */

import type {
  FaceLandmarkerResult,
  HandLandmarkerResult,
  PoseLandmarkerResult,
} from "@mediapipe/tasks-vision";
import * as THREE from "three";
import type { MocapBone, MocapExpression } from "./clipFormat";
import type { ClipSample } from "./clipPlayer";
import { ONE_EURO_BODY, ONE_EURO_DEFAULTS } from "./config";
import { OneEuroQuat, OneEuroScalar, type OneEuroConfig } from "./oneEuro";
import type { VrmMocapOverrides } from "./vrmCalibration";

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

// ── Body IK constants ──────────────────────────────────────────────
//
// MediaPipe Pose world-landmark indices we use. Everything between the
// shoulders and feet we need for the landmark-driven IK body solver.
const LM_LEFT_SHOULDER = 11;
const LM_RIGHT_SHOULDER = 12;
const LM_LEFT_ELBOW = 13;
const LM_RIGHT_ELBOW = 14;
const LM_LEFT_WRIST = 15;
const LM_RIGHT_WRIST = 16;
const LM_LEFT_HIP = 23;
const LM_RIGHT_HIP = 24;
const LM_LEFT_KNEE = 25;
const LM_RIGHT_KNEE = 26;
const LM_LEFT_ANKLE = 27;
const LM_RIGHT_ANKLE = 28;
const LM_LEFT_FOOT_INDEX = 31;
const LM_RIGHT_FOOT_INDEX = 32;

/** Minimum per-landmark visibility needed to trust a body-bone write.
 *  Low-visibility landmarks produce jittery garbage — we gate every
 *  bone-family computation on the visibilities of its source
 *  landmarks.
 *
 *  When visibility drops we DELETE bone entries (not skip). Deletion
 *  lets the playback layer fall through to idle/procedural animation
 *  rather than freezing on a stale IK value, which was the
 *  "body doesn't move when partially out of frame" bug symptom.
 *
 *  0.15 is lenient — MediaPipe Pose is conservative on limb visibility
 *  (legitimate mid-torso shots frequently have hip/shoulder visibility
 *  in the 0.2-0.4 range). 0.15 still rejects "totally off-frame" while
 *  admitting realistic captures; OneEuro handles any residual jitter. */
const VIS_GATE = 0.15;

/** Bones cleared when the torso visibility gate fails. */
const TORSO_BONES: readonly MocapBone[] = [
  "hips",
  "spine",
  "chest",
  "upperChest",
] as const;

// Scratch objects for the body IK solver. Module-scoped so the per-
// frame solve does zero allocations in steady state.
const _Y_AXIS = new THREE.Vector3(0, 1, 0);
// Constant unit vectors — never mutated, used for axis-angle
// construction (shoulder shrug uses +X, forearm roll uses +Y in local
// frames).
const _unitX = new THREE.Vector3(1, 0, 0);
const _unitY = new THREE.Vector3(0, 1, 0);

/** Convert a landmark-derived vector from MediaPipe worldLandmarks
 *  coordinates to three.js / VRM coordinates.
 *
 *  MediaPipe worldLandmarks use image-camera convention:
 *    +X = subject's LEFT, +Y = DOWN, +Z = AWAY from camera.
 *  three.js / VRM expect:
 *    +X same, +Y = UP, +Z = TOWARD camera.
 *
 *  So we negate Y and Z on every vector derived from a landmark
 *  difference. Without this the VRM renders upside-down and rotations
 *  around horizontal axes invert. */
function fixCoord(v: THREE.Vector3): void {
  v.y = -v.y;
  v.z = -v.z;
}

/** Branchless median-of-3: returns the middle value of ``a``, ``b``,
 *  ``c`` using two ``min``s and two ``max``s. Used by the pre-IK pose
 *  landmark smoothing filter to reject single-frame outliers without
 *  the motion-onset lag a mean would incur. */
function median3(a: number, b: number, c: number): number {
  return Math.max(Math.min(a, b), Math.min(Math.max(a, b), c));
}
const _bX = new THREE.Vector3();
const _bY = new THREE.Vector3();
const _bZ = new THREE.Vector3();
const _midHip = new THREE.Vector3();
const _midSh = new THREE.Vector3();
const _torsoMat = new THREE.Matrix4();
const _torsoQuat = new THREE.Quaternion();
const _yawQuat = new THREE.Quaternion();
const _remaining = new THREE.Quaternion();
const _tmpQuat = new THREE.Quaternion();
const _identity = new THREE.Quaternion();
const _upperChestWorld = new THREE.Quaternion();
const _upperArmWorld = new THREE.Quaternion();
const _lowerArmWorld = new THREE.Quaternion();
const _parentInv = new THREE.Quaternion();
const _armA = new THREE.Vector3();
const _armB = new THREE.Vector3();
const _legA = new THREE.Vector3();
const _legB = new THREE.Vector3();
const _legC = new THREE.Vector3();
const _restDir = new THREE.Vector3();
const _obsLocal = new THREE.Vector3();
const _bqA = new THREE.Quaternion();
const _bqB = new THREE.Quaternion();
const _bqC = new THREE.Quaternion();

// Phase B scratches — forearm roll from hand landmarks.
// ``_handWristW``/``_handMidW`` hold the wrist and middle-MCP in
// three.js/VRM world coords (after ``fixCoord`` + mirror X-flip). They
// are re-derived from ``this._latestHandResult`` each frame in
// ``solveArmChain``. ``_handDir`` is the world-space wrist→middle-MCP
// vector, and ``_handDirLocal`` is the same expressed in the lower
// arm's local frame (used to recover the twist angle around the bone
// axis). ``_twistQuat`` / ``_lowerArmWorldInv`` are composition
// helpers.
const _handWristW = new THREE.Vector3();
const _handMidW = new THREE.Vector3();
const _handDir = new THREE.Vector3();
const _handDirLocal = new THREE.Vector3();
const _twistQuat = new THREE.Quaternion();
const _lowerArmWorldInv = new THREE.Quaternion();

// Phase C scratches — shoulder shrug heuristic. ``_shoulderDir`` is
// the upperArm's current direction in upperChest-local frame (rest
// direction rotated by the computed upperArm quaternion). ``
// _shoulderQuat`` is the resulting rotation written to
// left/rightShoulder.
const _shoulderDir = new THREE.Vector3();
const _shoulderQuat = new THREE.Quaternion();

// Phase D scratches — weighted spine distribution.
const _spineQuat = new THREE.Quaternion();
const _chestQuat = new THREE.Quaternion();
const _upperChestQuat = new THREE.Quaternion();

// ── Phase B: forearm roll calibration ─────────────────────────────
/** Cap the applied roll at ±60° to guard against the hand-landmark
 *  detector producing spurious extremes (partial occlusion, one
 *  finger visible, etc). Per-VRM rest/gain/sign-flip live on the
 *  ``MocapSolver`` class fields and are driven via
 *  ``BodyIKOverrides.forearmRoll``. */
const FOREARM_ROLL_CLAMP_RAD = (60 * Math.PI) / 180;

// ── Phase C: shoulder shrug heuristic ─────────────────────────────
/** Max rotation applied to left/rightShoulder when the arm is fully
 *  raised overhead. 20° is at the low end of the anatomical scapular
 *  elevation range — enough to avoid the "stiff collarbone" look
 *  without overshooting into comical shrugs. Per-VRM scaling of the
 *  applied rotation lives on the ``MocapSolver`` class fields
 *  (``shoulderLiftGain``) and is driven via
 *  ``BodyIKOverrides.shoulderLiftGain``. */
const SHOULDER_LIFT_MAX_RAD = (20 * Math.PI) / 180;
/** Lift component (upperArm's current Y in parent-local) must exceed
 *  this before shoulder starts moving. 0 = arm at or above horizontal
 *  only; the scapula at rest doesn't meaningfully depress when the
 *  arm hangs, so we pin the lower end at rest. */
const SHOULDER_LIFT_THRESHOLD = 0;

// ── Phase D: spine chain weights ──────────────────────────────────
// Default spine distribution lives on the ``MocapSolver`` class
// fields (``spineWeightSpine`` / ``spineWeightChest`` /
// ``spineWeightUpperChest``) so it can be overridden per-VRM via
// ``BodyIKOverrides.spineWeights``. Default values (0.45/0.35/0.20)
// are lumbar-biased to match the spine's real bending distribution.

/** Bones that get the softer body-tuned OneEuro preset. Finger bones
 *  are intentionally excluded — their output amplifies raw landmark
 *  noise and needs the aggressive ``ONE_EURO_DEFAULTS`` preset. */
const BODY_BONE_NAMES: ReadonlySet<MocapBone> = new Set<MocapBone>([
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
  "leftUpperLeg",
  "rightUpperLeg",
  "leftLowerLeg",
  "rightLowerLeg",
  "leftFoot",
  "rightFoot",
]);

// MediaPipe hand landmark indices — 21 per hand.
// Reference: https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker
//   0: WRIST
//   1-4: THUMB       (CMC, MCP, IP, TIP)
//   5-8: INDEX       (MCP, PIP, DIP, TIP)
//   9-12: MIDDLE     (MCP, PIP, DIP, TIP)
//   13-16: RING      (MCP, PIP, DIP, TIP)
//   17-20: LITTLE    (MCP, PIP, DIP, TIP)
const WRIST_LM = 0;
const INDEX_MCP_LM = 5;
const MIDDLE_MCP_LM = 9;
const PINKY_MCP_LM = 17;

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
  | "thumbDistal"     // IP of thumb (measured relative to proximal)
  | "thumbMetacarpal"; // CMC of thumb (measured relative to palm-forward)

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
  /** If true, the raw curl is replaced by ``(π/2 − curl)`` before
   *  calibration. Useful when the measured angle DECREASES as the
   *  joint "flexes" — e.g. thumb opposition brings CMC→MCP closer to
   *  palm-forward, shrinking the angle. The diagnostic
   *  ``latestFingerMaxCurl`` still tracks the ORIGINAL curl so its
   *  "is any signal present?" semantics stay meaningful. */
  invertRaw?: boolean;
}

/** Per-finger rest splay (abduction angle at a relaxed hand) in the
 *  palm frame. Signed, radians — positive points toward the pinky side.
 *  Fingers naturally splay a little even at rest, so we subtract this
 *  before scaling into bone rotation. Only defined for the four
 *  non-thumb proximals: thumb abduction is handled separately (not in
 *  this pass) and intermediate/distal joints are single-axis hinges.
 *
 *  Values measured from a relaxed hand against the MediaPipe palm
 *  frame: index sits ~10° toward the thumb, ring ~6° toward pinky,
 *  little ~16° toward pinky; middle is the zero reference. */
const SPREAD_REST_RAD: Partial<Record<MocapBone, number>> = {
  leftIndexProximal:  -0.18,
  leftMiddleProximal:  0.00,
  leftRingProximal:    0.10,
  leftLittleProximal:  0.28,
  rightIndexProximal: -0.18,
  rightMiddleProximal: 0.00,
  rightRingProximal:   0.10,
  rightLittleProximal: 0.28,
};

/** Spread (abduction / adduction) config shared across all non-thumb
 *  proximals. Assumes a VRoid-family rig where the bone's local +Y is
 *  the abduction axis (perpendicular to the +X "along finger" and +Z
 *  "flexion" axes). If a rig ships with a different spread axis we'll
 *  add per-VRM overrides in a follow-up — for now this is hard-coded. */
const PROXIMAL_SPREAD = {
  /** Local bone axis around which spread rotates. */
  axis: "y" as RotationAxis,
  /** Visible bone rotation at the clamp edge. */
  outRangeRad: (30 * Math.PI) / 180,
  /** Flip sign per rig chirality. Left/right-hand sign flip is handled
   *  by ``curlSign`` in the solve loop; this is the raw axis override. */
  flipSign: false,
  /** Clamp the raw (splay − restSplay) delta to this magnitude before
   *  mapping to ``outRangeRad``. Prevents extreme finger-crossing poses
   *  (e.g. index crossing over middle) from producing garbage angles. */
  clampRad: (20 * Math.PI) / 180,
};

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
    // Like ``thumbProximal``, the observed IP-joint relative angle is
    // narrower than the anatomical 60° range suggested — tuned down
    // to match what MediaPipe actually reports and amplified on the
    // output side so the tip of the thumb visibly curls. Sits at a
    // smaller out-range than the proximal so the two joints together
    // read as "proximal bends more than distal" — the natural ratio
    // for human thumb flexion.
    restRad: (5 * Math.PI) / 180,
    fistRad: (30 * Math.PI) / 180,
    outRangeRad: (75 * Math.PI) / 180,
    axis: "x",
    flipSign: true,
  },
  // Thumb CMC (``thumbMetacarpal``) — opposition motion brings the
  // thumb ACROSS the palm (OK-sign, fist wrap, pinch). Parent
  // reference is palm-forward (wrist→middle-MCP) because the wrist
  // isn't a tracked segment we can use as a parent direction. Child
  // segment is CMC→MCP (landmarks 1→2).
  //
  // Raw geometry: at rest the thumb points out to the side, so the
  // angle between palm-forward and CMC→MCP is wide (~85°). As the
  // thumb opposes, that angle SHRINKS toward ~30-50°. Because the
  // signal decreases with flexion we set ``invertRaw: true`` so the
  // solver uses ``(π/2 − curl)`` — a fully-opposing thumb then maps
  // to a large "virtual curl" value and the standard rest/fist/out
  // calibration works with the usual sign. After inversion the
  // measured range is roughly rest ~5° → fist ~55°. OUT range is wide
  // because opposition is a large visible deflection on the VRM.
  //
  // Axis "y" is a GUESS — needs empirical validation via the
  // ``?debug=1`` axis test harness. If wrong, flip via the per-VRM
  // override or edit here.
  thumbMetacarpal: {
    restRad: (5 * Math.PI) / 180,
    fistRad: (55 * Math.PI) / 180,
    outRangeRad: (60 * Math.PI) / 180,
    axis: "y",
    flipSign: false,
    invertRaw: true,
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
  // Thumb — all three segments. Metacarpal uses palm-forward as
  // parent (the wrist isn't a tracked segment we can use anywhere
  // else), so its "curl" is really the angle between palm-forward and
  // CMC→MCP. That angle DECREASES as the thumb opposes, which is why
  // ``thumbMetacarpal`` sets ``invertRaw: true`` in CALIBRATION.
  ["leftThumbMetacarpal",    "palm", 1, 2, "thumbMetacarpal"],
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
  ["rightThumbMetacarpal",    "palm", 1, 2, "thumbMetacarpal"],
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
const _hIdxMcp = new THREE.Vector3();
const _hPkyMcp = new THREE.Vector3();
const _hParentA = new THREE.Vector3();
const _hParentB = new THREE.Vector3();
const _hChildA = new THREE.Vector3();
const _hChildB = new THREE.Vector3();
const _parentDir = new THREE.Vector3();
const _childDir = new THREE.Vector3();
const _palmY = new THREE.Vector3();
const _palmX = new THREE.Vector3();
const _palmZ = new THREE.Vector3();
const _curlEuler = new THREE.Euler();
const _spreadEuler = new THREE.Euler();
const _handQ = new THREE.Quaternion();
const _composedQ = new THREE.Quaternion();
/** True when the palm frame derived this frame is degenerate (palm
 *  edge-on to camera). Gate spread application on this — curl still
 *  uses the orthogonalised ``_palmY`` which comes from a separate
 *  length check in ``solveOneHand``. */
let _palmFrameValid = false;

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
  /** Body-IK tunables — defaults match the former module constants so
   *  rigs without ``body`` overrides behave identically to the baseline
   *  tuning. Mutated in ``setVrmOverrides``. */
  private shoulderLiftGain = 1.0;
  private forearmRollRestRad = 0;
  private forearmRollGain = 0.8;
  private forearmRollSignFlip = false;
  private spineWeightSpine = 0.45;
  private spineWeightChest = 0.35;
  private spineWeightUpperChest = 0.2;
  /** Live calibration table — defaults to ``CALIBRATION`` and can be
   *  swapped in per-VRM via ``setVrmOverrides``. Read each frame by
   *  ``solveOneHand`` so override changes take effect on the next
   *  solve without needing a solver reinit. */
  private effectiveCalibration: Record<JointType, JointCalibration>;
  /** Latest HandLandmarker result stashed by ``solveInto`` so
   *  ``solveArmChain`` can pull palm landmarks for forearm-roll
   *  computation. Kept as a plain field (no smoothing) because the
   *  landmark-coordinate data is consumed read-only inside the same
   *  frame. Null when no hands were passed in. */
  private _latestHandResult: HandLandmarkerResult | null = null;

  /** Zero-allocation 3-frame ring buffer of pose worldLandmarks for
   *  median pre-smoothing. Each slot is a pre-allocated array of 33
   *  mutable ``{x,y,z,visibility}`` dicts. We median each component
   *  across the valid slots to suppress single-frame depth jitter and
   *  low-visibility noise before the body IK consumes them. */
  private readonly _poseHistorySlots: Array<
    Array<{ x: number; y: number; z: number; visibility: number }>
  > = [];
  private _poseHistoryHead = 0;
  private _poseHistorySize = 0;
  /** Pre-allocated output array returned by ``smoothPoseLandmarks``.
   *  Mutated in-place each frame — callers must treat it as read-only
   *  snapshot valid only for the current solve. */
  private readonly _poseMedian: Array<{
    x: number;
    y: number;
    z: number;
    visibility: number;
  }> = [];

  constructor(opts: SolverOptions = {}) {
    this.mirror = opts.mirror ?? true;
    this.cfg = opts.oneEuro;
    this.effectiveCalibration = { ...CALIBRATION };
    // Pre-allocate the median output and the 3 ring-buffer slots so the
    // steady-state solve never allocates (33 objects per slot × 3 slots
    // + 33 in ``_poseMedian`` = 132 tiny records, created once here).
    for (let i = 0; i < 33; i++) {
      this._poseMedian.push({ x: 0, y: 0, z: 0, visibility: 0 });
    }
    for (let r = 0; r < 3; r++) {
      const slot: Array<{
        x: number;
        y: number;
        z: number;
        visibility: number;
      }> = [];
      for (let i = 0; i < 33; i++) {
        slot.push({ x: 0, y: 0, z: 0, visibility: 0 });
      }
      this._poseHistorySlots.push(slot);
    }
  }

  /** Apply per-VRM calibration overrides on top of the default
   *  ``CALIBRATION`` table. Pass ``null`` to revert to defaults. Only
   *  specified fields are overridden — missing fields fall through to
   *  the base. Degree fields on the override are converted to radians
   *  at merge time so the solve loop never has to divide by π. */
  setVrmOverrides(overrides: VrmMocapOverrides | null): void {
    // Body defaults — restored whenever overrides are null or whenever
    // a specific body field is omitted. Keep in sync with the class-
    // field initialisers above.
    const defaultShoulderLiftGain = 1.0;
    const defaultForearmRollRestRad = 0;
    const defaultForearmRollGain = 0.8;
    const defaultForearmRollSignFlip = false;
    const defaultSpineWeightSpine = 0.45;
    const defaultSpineWeightChest = 0.35;
    const defaultSpineWeightUpperChest = 0.2;

    if (!overrides) {
      this.effectiveCalibration = { ...CALIBRATION };
      this.shoulderLiftGain = defaultShoulderLiftGain;
      this.forearmRollRestRad = defaultForearmRollRestRad;
      this.forearmRollGain = defaultForearmRollGain;
      this.forearmRollSignFlip = defaultForearmRollSignFlip;
      this.spineWeightSpine = defaultSpineWeightSpine;
      this.spineWeightChest = defaultSpineWeightChest;
      this.spineWeightUpperChest = defaultSpineWeightUpperChest;
      return;
    }
    const merged: Record<JointType, JointCalibration> = { ...CALIBRATION };
    for (const key of Object.keys(CALIBRATION) as JointType[]) {
      const base = CALIBRATION[key];
      const ov = overrides[key];
      if (!ov) continue;
      merged[key] = {
        axis: ov.axis ?? base.axis,
        flipSign: ov.flipSign ?? base.flipSign,
        // ``invertRaw`` is an intrinsic property of the joint type
        // (driven by which way the landmark geometry moves during
        // flexion) — not something a per-VRM override should be able
        // to flip. Always carry it through from the base.
        invertRaw: base.invertRaw,
        restRad:
          ov.restDeg !== undefined
            ? (ov.restDeg * Math.PI) / 180
            : base.restRad,
        fistRad:
          ov.fistDeg !== undefined
            ? (ov.fistDeg * Math.PI) / 180
            : base.fistRad,
        outRangeRad:
          ov.outDeg !== undefined
            ? (ov.outDeg * Math.PI) / 180
            : base.outRangeRad,
      };
    }
    this.effectiveCalibration = merged;

    // --- Body section --------------------------------------------------
    const body = overrides.body;
    this.shoulderLiftGain =
      body?.shoulderLiftGain !== undefined && isFinite(body.shoulderLiftGain)
        ? body.shoulderLiftGain
        : defaultShoulderLiftGain;

    if (body?.spineWeights) {
      const { spine, chest, upperChest } = body.spineWeights;
      const sum = spine + chest + upperChest;
      // Loose tolerance — the weights are applied as slerp parameters,
      // so small drift is fine but a wildly-off sum suggests the
      // catalog author intended something else. Warn + keep defaults.
      if (Math.abs(sum - 1.0) > 0.05) {
        console.warn(
          `[mocap] spineWeights sum ${sum.toFixed(3)} deviates from 1.0;` +
            " keeping default spine weights.",
        );
        this.spineWeightSpine = defaultSpineWeightSpine;
        this.spineWeightChest = defaultSpineWeightChest;
        this.spineWeightUpperChest = defaultSpineWeightUpperChest;
      } else {
        this.spineWeightSpine = spine;
        this.spineWeightChest = chest;
        this.spineWeightUpperChest = upperChest;
      }
    } else {
      this.spineWeightSpine = defaultSpineWeightSpine;
      this.spineWeightChest = defaultSpineWeightChest;
      this.spineWeightUpperChest = defaultSpineWeightUpperChest;
    }

    const forearm = body?.forearmRoll;
    this.forearmRollRestRad =
      forearm?.restRad !== undefined && isFinite(forearm.restRad)
        ? forearm.restRad
        : defaultForearmRollRestRad;
    this.forearmRollGain =
      forearm?.gain !== undefined && isFinite(forearm.gain)
        ? forearm.gain
        : defaultForearmRollGain;
    this.forearmRollSignFlip =
      forearm?.signFlip !== undefined
        ? forearm.signFlip
        : defaultForearmRollSignFlip;
  }

  reset(): void {
    for (const f of Object.values(this.quatFilters)) f?.reset();
    for (const f of Object.values(this.scalarFilters)) f?.reset();
    // Clear the pose smoothing ring so a camera restart doesn't carry
    // stale landmarks into the next session. The pre-allocated slot
    // objects are reused; only the size+head counters need resetting —
    // ``smoothPoseLandmarks`` overwrites the slot contents before the
    // median is read again.
    this._poseHistoryHead = 0;
    this._poseHistorySize = 0;
  }

  /** Push the latest pose worldLandmarks into the 3-frame ring buffer
   *  and materialise the per-component median into ``_poseMedian``.
   *
   *  Returns ``null`` if the input is too short (<33 landmarks).
   *  Otherwise returns the ``_poseMedian`` array, which is reused on
   *  every call (the caller must consume it before the next frame).
   *
   *  Graceful warm-up:
   *    - 1 sample  → passthrough (no smoothing until we have ≥2)
   *    - 2 samples → arithmetic mean of the two (median-of-2 is
   *                   undefined; the mean is a reasonable stopgap)
   *    - 3 samples → true per-component median
   *
   *  Zero allocation: the raw landmarks are copied field-by-field into
   *  the ring's next slot (mutating pre-allocated objects) and the
   *  median output array is similarly mutated in place. */
  private smoothPoseLandmarks(
    raw: Array<{ x: number; y: number; z: number; visibility?: number }>,
  ): Array<{ x: number; y: number; z: number; visibility: number }> | null {
    if (raw.length < 33) return null;

    // Copy raw[0..33) into the head slot in place — no new allocations.
    const headSlot = this._poseHistorySlots[this._poseHistoryHead];
    for (let i = 0; i < 33; i++) {
      const r = raw[i];
      const s = headSlot[i];
      s.x = r.x;
      s.y = r.y;
      s.z = r.z;
      s.visibility = r.visibility ?? 0;
    }
    this._poseHistoryHead = (this._poseHistoryHead + 1) % 3;
    if (this._poseHistorySize < 3) this._poseHistorySize++;

    const n = this._poseHistorySize;
    if (n === 1) {
      // Warm-up: just expose the raw frame. ``headSlot`` is the only
      // populated slot, but we copy into ``_poseMedian`` so callers
      // always read from the same array reference.
      for (let i = 0; i < 33; i++) {
        const s = headSlot[i];
        const m = this._poseMedian[i];
        m.x = s.x;
        m.y = s.y;
        m.z = s.z;
        m.visibility = s.visibility;
      }
      return this._poseMedian;
    }

    // Locate the valid slots. With ``_poseHistoryHead`` now pointing at
    // the NEXT write position, the most recent write is at head-1 mod 3
    // and earlier writes trail backward. We don't care about absolute
    // order for a median — only that all ``n`` samples are considered.
    const slot0 = this._poseHistorySlots[0];
    const slot1 = this._poseHistorySlots[1];
    const slot2 = this._poseHistorySlots[2];

    if (n === 2) {
      // With only two samples the 3rd slot hasn't been written yet.
      // Which two are valid depends on head position after the
      // increment: after 2 writes head = 2, so slots 0 and 1 hold data.
      for (let i = 0; i < 33; i++) {
        const a = slot0[i];
        const b = slot1[i];
        const m = this._poseMedian[i];
        m.x = (a.x + b.x) * 0.5;
        m.y = (a.y + b.y) * 0.5;
        m.z = (a.z + b.z) * 0.5;
        m.visibility = (a.visibility + b.visibility) * 0.5;
      }
      return this._poseMedian;
    }

    // n === 3: full median across all three slots.
    for (let i = 0; i < 33; i++) {
      const a = slot0[i];
      const b = slot1[i];
      const c = slot2[i];
      const m = this._poseMedian[i];
      m.x = median3(a.x, b.x, c.x);
      m.y = median3(a.y, b.y, c.y);
      m.z = median3(a.z, b.z, c.z);
      m.visibility = median3(a.visibility, b.visibility, c.visibility);
    }
    return this._poseMedian;
  }

  private quatFilter(name: MocapBone): OneEuroQuat {
    let f = this.quatFilters[name];
    if (!f) {
      // Body bones use the softer ``ONE_EURO_BODY`` preset (slower
      // wide-amplitude motion needs less aggressive smoothing to feel
      // responsive). Fingers keep the tighter ``ONE_EURO_DEFAULTS``
      // preset since their output amplifies raw landmark noise.
      // A caller-supplied ``opts.oneEuro`` still wins over the body
      // preset when set — it's intended as a full override.
      const config = this.cfg
        ? this.cfg
        : BODY_BONE_NAMES.has(name)
          ? ONE_EURO_BODY
          : ONE_EURO_DEFAULTS;
      f = new OneEuroQuat(config);
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
    // Stash hands FIRST so ``solvePose`` → ``solveArmChain`` can read
    // palm landmarks for forearm roll. ``solveHands`` still writes the
    // finger bones downstream from the same result.
    this._latestHandResult = hands;
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
      // Face mirror — final empirically-validated combination:
      //   * pitch (x): FLIP   — MP face-matrix's pitch axis is opposite
      //     three.js for this VRM rig, so negating x makes nod up/down
      //     match.
      //   * yaw (y):   FLIP   — sagittal-mirror convention for head
      //     shake (도리도리).
      //   * roll (z):  KEEP   — unlike y, the face-matrix's roll axis
      //     already produces screen-same-side tilt when applied to a
      //     viewer-facing VRM, so negating z would invert it.
      // History: we shipped all-three-flipped (conjugate) which left
      // roll inverted; then we went y-only which flipped pitch back to
      // wrong. The combination below matches all three axes the user
      // verified at once.
      if (this.mirror) {
        this.scratch[0] = -this.scratch[0];
        this.scratch[1] = -this.scratch[1];
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
    this.solveBodyIK(pose, tsSec, out);
  }

  /** Landmark-driven body IK. Replaces Kalidokit.Pose.solve (v4) with
   *  direct math against MediaPipe's ``worldLandmarks``. See the module
   *  docstring for the coordinate-system assumptions; in short: the
   *  world frame's +X points along the subject's anatomical LEFT, +Y
   *  is UP, +Z points BEHIND the subject (away from the camera). The
   *  VRM's T-pose uses the same convention after normalization — left
   *  arm along +X, head along +Y, facing +Z.
   *
   *  Bone families handled here, in solve order:
   *    1. torso frame (basis from hip + shoulder landmarks)
   *    2. hips: yaw component only (rotation around world Y)
   *    3. spine / chest / upperChest: cube-root slerp of the torso's
   *       remaining pitch+roll, so composing all three gives the full
   *       remaining rotation
   *    4. shoulders: identity (not solved this pass — scapula motion is
   *       hard to recover from landmarks alone)
   *    5. arms: upper → lower → hand via ``setFromUnitVectors`` in the
   *       parent bone's local frame
   *    6. legs: upper → lower → foot same way, rooted at ``hips``
   *
   *  NOT handled this pass:
   *    - arm/forearm roll (needs hand-landmark palm normals)
   *    - shoulder bones (``leftShoulder`` / ``rightShoulder``)
   *    - root translation (hips.position) — the playback pipeline only
   *      reads quaternions via ``sample.bones[name]``; position needs a
   *      separate track + playback wiring. TODO in next pass.
   *
   *  Mirror convention: ``this.mirror`` true (default) means the user's
   *  anatomical left maps to the VRM's right, so the preview reads as
   *  a mirror (user raises left hand → VRM raises right hand on the
   *  same side of the screen). Implemented two ways: (a) the torso
   *  basis flips its X and Z axes so the resulting yaw quaternion is
   *  already in the mirrored frame, and (b) arm/leg landmark-triplets
   *  swap L↔R source landmarks before passing to the chain solver.
   *
   *  Visibility gating: MediaPipe ships ``visibility`` per landmark. When
   *  a family's source landmarks fall below ``VIS_GATE`` we DELETE that
   *  family's bone entries from ``out.bones`` (via ``clearBones``) — we
   *  do NOT just ``return`` and leave stale values in place. Deletion
   *  lets the playback layer fall through to idle/procedural animation
   *  rather than freezing on a stale IK value, which was the
   *  "body doesn't move when partially out of frame" bug symptom.
   */
  private solveBodyIK(
    pose: PoseLandmarkerResult,
    tsSec: number,
    out: ClipSample,
  ): void {
    const raw = pose.worldLandmarks?.[0];
    if (!raw || raw.length < 33) return;
    // Pre-smooth the raw landmarks with a 3-frame median filter to
    // reject single-frame depth-axis outliers BEFORE they cascade into
    // the geometric reasoning (torso basis, arm/leg chains). OneEuro on
    // the post-IK bone quaternions can't fully cancel these because a
    // single landmark blip produces a large quaternion swing that
    // OneEuro lets through once its cutoff opens during fast motion.
    // ``_poseMedian`` is reused across frames — consume it within this
    // solve only.
    const world = this.smoothPoseLandmarks(raw);
    if (!world) return;

    // --- Step 1: Torso frame ----------------------------------------
    const lHip = world[LM_LEFT_HIP];
    const rHip = world[LM_RIGHT_HIP];
    const lSh = world[LM_LEFT_SHOULDER];
    const rSh = world[LM_RIGHT_SHOULDER];
    const hipVis =
      (lHip?.visibility ?? 0) > VIS_GATE &&
      (rHip?.visibility ?? 0) > VIS_GATE;
    const shoulderVis =
      (lSh?.visibility ?? 0) > VIS_GATE &&
      (rSh?.visibility ?? 0) > VIS_GATE;

    // If nothing's visible (subject entirely out of frame), we can't
    // drive any body bones — clear everything we own and bail.
    if (!hipVis && !shoulderVis) {
      this.clearBones(out, TORSO_BONES);
      // Arms / legs are cleared inside their own chains when their
      // own landmarks fail visibility, so no need to clear them here.
      return;
    }

    // Webcam-from-chest-up shot: shoulders visible, hips aren't. Skip
    // torso/spine writes (we'd need both hip and shoulder landmarks
    // to derive them reliably) but fall through to the ARM chain with
    // an identity torso frame. This is the common "recording from a
    // desk webcam with legs under the table" case.
    if (!hipVis) {
      this.clearBones(out, TORSO_BONES);
      _torsoQuat.identity();
      _yawQuat.identity();
      _upperChestWorld.identity();
      this.solveArmChainsOnly(world, tsSec, out);
      return;
    }

    // Hips visible, shoulders not (rare — user's head cut off). Clear
    // torso + neck-adjacent bones; legs can still compute from hip+knee
    // so fall through.
    if (!shoulderVis) {
      this.clearBones(out, TORSO_BONES);
      _torsoQuat.identity();
      _yawQuat.identity();
      _upperChestWorld.identity();
      this.solveLegChainsOnly(world, tsSec, out);
      return;
    }

    // MediaPipe world X increases toward the subject's LEFT, so
    // ``leftHip - rightHip`` has positive x → points from the right
    // hip to the left hip. That's body's +X (subject-left) and lines
    // up with the VRM T-pose convention (left arm along +X).
    _bX.set(lHip.x - rHip.x, lHip.y - rHip.y, lHip.z - rHip.z);
    fixCoord(_bX);
    if (_bX.lengthSq() < 1e-6) return;
    _bX.normalize();
    // Torso-up = midShoulder - midHip, orthogonalised against +X so the
    // three axes form a clean orthonormal basis.
    _midHip.set(
      (lHip.x + rHip.x) * 0.5,
      (lHip.y + rHip.y) * 0.5,
      (lHip.z + rHip.z) * 0.5,
    );
    fixCoord(_midHip);
    _midSh.set(
      (lSh.x + rSh.x) * 0.5,
      (lSh.y + rSh.y) * 0.5,
      (lSh.z + rSh.z) * 0.5,
    );
    fixCoord(_midSh);
    _bY.copy(_midSh).sub(_midHip);
    _bY.addScaledVector(_bX, -_bY.dot(_bX));
    if (_bY.lengthSq() < 1e-6) return;
    _bY.normalize();
    // Right-handed frame: +Z = +X × +Y. At rest (subject facing +Z),
    // this gives a +Z that points behind the subject, matching world.
    _bZ.copy(_bX).cross(_bY).normalize();

    _torsoMat.makeBasis(_bX, _bY, _bZ);
    _torsoQuat.setFromRotationMatrix(_torsoMat);

    // Mirror convention: same pattern we already use for ``solveFace``
    // — negate the y and z components of the rotation quaternion.
    // This flips yaw (rotation around Y) and roll (around Z) while
    // preserving pitch (around X), matching "VRM mirrors the user
    // across the screen's vertical axis" behaviour. Do NOT flip the
    // basis vectors themselves — that produced a 180° yaw baseline
    // which made the whole VRM face away from the camera at rest.
    if (this.mirror) {
      _torsoQuat.y = -_torsoQuat.y;
      _torsoQuat.z = -_torsoQuat.z;
    }

    // --- Step 2: Hips carry the yaw (rotation around world Y) -------
    //
    // Isolate the Y-component of the quaternion. We use the standard
    // "twist" decomposition: the yaw quaternion is the rotation around
    // world Y that, when composed with a residual pitch/roll, equals
    // ``_torsoQuat``.
    const yaw = Math.atan2(
      2 * (_torsoQuat.w * _torsoQuat.y + _torsoQuat.x * _torsoQuat.z),
      1 - 2 * (_torsoQuat.y * _torsoQuat.y + _torsoQuat.x * _torsoQuat.x),
    );
    _yawQuat.setFromAxisAngle(_Y_AXIS, yaw);
    this.writeBoneSmoothed(
      "hips",
      [_yawQuat.x, _yawQuat.y, _yawQuat.z, _yawQuat.w],
      tsSec,
      out,
    );

    // --- Step 3: Spine chain = lumbar-biased weighted split ---------
    //
    // remaining = torso * yaw^-1 (remove yaw; what's left is bend+lean).
    // Distribute it across spine (0.45) / chest (0.35) / upperChest
    // (0.20) via ``slerp(identity, remaining, w_i)``. For small-to-
    // medium rotations this approximates ``remaining^w_i`` and the
    // weights sum to 1.0, so composing the three locals recovers
    // ``remaining`` to first order. Lumbar-biased weights match real
    // spine anatomy — the lower back bends far more than the upper
    // thoracic / cervical-adjacent segments.
    _remaining.copy(_torsoQuat).multiply(_tmpQuat.copy(_yawQuat).invert());
    _identity.identity();
    _spineQuat.copy(_identity).slerp(_remaining, this.spineWeightSpine);
    _chestQuat.copy(_identity).slerp(_remaining, this.spineWeightChest);
    _upperChestQuat.copy(_identity).slerp(_remaining, this.spineWeightUpperChest);
    this.writeBoneSmoothed(
      "spine",
      [_spineQuat.x, _spineQuat.y, _spineQuat.z, _spineQuat.w],
      tsSec,
      out,
    );
    this.writeBoneSmoothed(
      "chest",
      [_chestQuat.x, _chestQuat.y, _chestQuat.z, _chestQuat.w],
      tsSec,
      out,
    );
    this.writeBoneSmoothed(
      "upperChest",
      [_upperChestQuat.x, _upperChestQuat.y, _upperChestQuat.z, _upperChestQuat.w],
      tsSec,
      out,
    );

    // The parent world rotation AT THE UPPERCHEST bone (end of the
    // spine chain). With the cube-root split this equalled torsoQuat
    // exactly; with the weighted split it equals
    // ``yaw * slerp(id,R,a) * slerp(id,R,b) * slerp(id,R,c)``, which
    // for small remaining rotations ≈ ``yaw * R^(a+b+c) = torsoQuat``.
    // Leave as-is — the drift from weighted distribution is within
    // OneEuro's smoothing tolerance for typical body motion.
    _upperChestWorld.copy(_torsoQuat);

    // --- Step 4: Arms (shoulder → upper → lower → hand) -------------
    //
    // ``mirror`` true (default) means the VRM's left bone is driven by
    // the user's anatomical RIGHT side and vice versa. That's exactly
    // the convention ``solveHands`` already uses for fingers, so the
    // hand attached to an arm lines up with its finger bones.
    const armVrmLeft_User = this.mirror
      ? [LM_RIGHT_SHOULDER, LM_RIGHT_ELBOW, LM_RIGHT_WRIST] as const
      : [LM_LEFT_SHOULDER, LM_LEFT_ELBOW, LM_LEFT_WRIST] as const;
    const armVrmRight_User = this.mirror
      ? [LM_LEFT_SHOULDER, LM_LEFT_ELBOW, LM_LEFT_WRIST] as const
      : [LM_RIGHT_SHOULDER, LM_RIGHT_ELBOW, LM_RIGHT_WRIST] as const;

    // Rest directions for arm bones in their PARENT's local frame at
    // T-pose. Shoulder rest direction in upperChest-local frame is +X
    // for left arm, -X for right (arms extend sideways). UpperArm's
    // child (elbow) in upperArm-local is along +Y because three-vrm's
    // normalized humanoid rotates each arm bone so its local +Y points
    // toward the next joint. Likewise for lowerArm (+Y toward wrist)
    // and hand (+Y toward middle finger).
    this.solveArmChain(
      "leftUpperArm",
      "leftLowerArm",
      "leftHand",
      "leftShoulder",
      armVrmLeft_User[0],
      armVrmLeft_User[1],
      armVrmLeft_User[2],
      +1,
      world,
      tsSec,
      out,
    );
    this.solveArmChain(
      "rightUpperArm",
      "rightLowerArm",
      "rightHand",
      "rightShoulder",
      armVrmRight_User[0],
      armVrmRight_User[1],
      armVrmRight_User[2],
      -1,
      world,
      tsSec,
      out,
    );

    // --- Step 5: Legs (hip → upper → lower → foot) ------------------
    //
    // Parent of the upper leg is ``hips`` (yaw only). Rest direction
    // for upperLeg in hips-local is essentially (0, -1, 0) — legs hang
    // straight down in T-pose. Small outward offset at the hip socket
    // is ignored (worst case a few degrees of baseline bias that the
    // OneEuro filter smooths over).
    const legVrmLeft_User = this.mirror
      ? [LM_RIGHT_HIP, LM_RIGHT_KNEE, LM_RIGHT_ANKLE, LM_RIGHT_FOOT_INDEX] as const
      : [LM_LEFT_HIP, LM_LEFT_KNEE, LM_LEFT_ANKLE, LM_LEFT_FOOT_INDEX] as const;
    const legVrmRight_User = this.mirror
      ? [LM_LEFT_HIP, LM_LEFT_KNEE, LM_LEFT_ANKLE, LM_LEFT_FOOT_INDEX] as const
      : [LM_RIGHT_HIP, LM_RIGHT_KNEE, LM_RIGHT_ANKLE, LM_RIGHT_FOOT_INDEX] as const;

    this.solveLegChain(
      "leftUpperLeg",
      "leftLowerLeg",
      "leftFoot",
      legVrmLeft_User[0],
      legVrmLeft_User[1],
      legVrmLeft_User[2],
      legVrmLeft_User[3],
      world,
      tsSec,
      out,
    );
    this.solveLegChain(
      "rightUpperLeg",
      "rightLowerLeg",
      "rightFoot",
      legVrmRight_User[0],
      legVrmRight_User[1],
      legVrmRight_User[2],
      legVrmRight_User[3],
      world,
      tsSec,
      out,
    );

    // NOTE (TODO): root translation. We track ``hipMidY`` across frames
    // so the VRM's hips.position.y can follow the user's crouch/jump;
    // the current clip format stores quaternions only, so hooking the
    // playback pipeline to accept a per-frame position vector is a
    // follow-up. Measured in metres relative to the first-valid-frame
    // baseline when enabled.
  }

  /** Fallback used when hips aren't visible (e.g. chest-up webcam
   *  shot). Drives both arms with the current (possibly identity)
   *  ``_upperChestWorld`` so the arms keep tracking even though the
   *  torso frame couldn't be built. Legs are skipped — without hip
   *  landmarks we can't compute them. */
  private solveArmChainsOnly(
    world: { x: number; y: number; z: number; visibility?: number }[],
    tsSec: number,
    out: ClipSample,
  ): void {
    const armVrmLeft_User = this.mirror
      ? ([LM_RIGHT_SHOULDER, LM_RIGHT_ELBOW, LM_RIGHT_WRIST] as const)
      : ([LM_LEFT_SHOULDER, LM_LEFT_ELBOW, LM_LEFT_WRIST] as const);
    const armVrmRight_User = this.mirror
      ? ([LM_LEFT_SHOULDER, LM_LEFT_ELBOW, LM_LEFT_WRIST] as const)
      : ([LM_RIGHT_SHOULDER, LM_RIGHT_ELBOW, LM_RIGHT_WRIST] as const);
    this.solveArmChain(
      "leftUpperArm", "leftLowerArm", "leftHand", "leftShoulder",
      armVrmLeft_User[0], armVrmLeft_User[1], armVrmLeft_User[2],
      +1, world, tsSec, out,
    );
    this.solveArmChain(
      "rightUpperArm", "rightLowerArm", "rightHand", "rightShoulder",
      armVrmRight_User[0], armVrmRight_User[1], armVrmRight_User[2],
      -1, world, tsSec, out,
    );
  }

  /** Fallback used when shoulders aren't visible (rare — user's head
   *  cut off). Drives only the legs. */
  private solveLegChainsOnly(
    world: { x: number; y: number; z: number; visibility?: number }[],
    tsSec: number,
    out: ClipSample,
  ): void {
    const legVrmLeft_User = this.mirror
      ? ([LM_RIGHT_HIP, LM_RIGHT_KNEE, LM_RIGHT_ANKLE, LM_RIGHT_FOOT_INDEX] as const)
      : ([LM_LEFT_HIP, LM_LEFT_KNEE, LM_LEFT_ANKLE, LM_LEFT_FOOT_INDEX] as const);
    const legVrmRight_User = this.mirror
      ? ([LM_LEFT_HIP, LM_LEFT_KNEE, LM_LEFT_ANKLE, LM_LEFT_FOOT_INDEX] as const)
      : ([LM_RIGHT_HIP, LM_RIGHT_KNEE, LM_RIGHT_ANKLE, LM_RIGHT_FOOT_INDEX] as const);
    this.solveLegChain(
      "leftUpperLeg", "leftLowerLeg", "leftFoot",
      legVrmLeft_User[0], legVrmLeft_User[1], legVrmLeft_User[2], legVrmLeft_User[3],
      world, tsSec, out,
    );
    this.solveLegChain(
      "rightUpperLeg", "rightLowerLeg", "rightFoot",
      legVrmRight_User[0], legVrmRight_User[1], legVrmRight_User[2], legVrmRight_User[3],
      world, tsSec, out,
    );
  }

  /** Solve one arm: upperArm (parent = upperChest), lowerArm (parent =
   *  upperArm), hand (parent = lowerArm). Each bone's local rotation is
   *  derived by transforming the observed child-direction into the
   *  parent bone's local frame, then ``setFromUnitVectors`` from the
   *  rest direction to the observed direction.
   *
   *  ``sideSign`` is +1 for the VRM's left arm, -1 for the VRM's right.
   *  It decides whether the shoulder-to-elbow rest direction in
   *  upperChest-local is +X or -X (T-pose: arms extend in opposite ±X
   *  directions from the spine).
   */
  private solveArmChain(
    upperBone: MocapBone,
    lowerBone: MocapBone,
    handBone: MocapBone,
    shoulderBone: MocapBone,
    lmShoulder: number,
    lmElbow: number,
    lmWrist: number,
    sideSign: 1 | -1,
    world: { x: number; y: number; z: number; visibility?: number }[],
    tsSec: number,
    out: ClipSample,
  ): void {
    const sh = world[lmShoulder];
    const el = world[lmElbow];
    const wr = world[lmWrist];
    if (
      (sh?.visibility ?? 0) < VIS_GATE ||
      (el?.visibility ?? 0) < VIS_GATE ||
      (wr?.visibility ?? 0) < VIS_GATE
    ) {
      // Preserve the Phase A idempotency contract: when visibility
      // fails we drop EVERY bone this chain is responsible for,
      // including the scapular shoulder bone written below.
      this.clearBones(out, [upperBone, lowerBone, shoulderBone]);
      return;
    }

    // Shoulder → elbow in world. No per-vector mirror flip here — the
    // mirror is applied once on ``_upperChestWorld`` (via y,z
    // negation of ``_torsoQuat``). Flipping the vector again would
    // double-mirror.
    _armA.set(el.x - sh.x, el.y - sh.y, el.z - sh.z);
    fixCoord(_armA);
    // Mirror: sagittal-plane reflection. ``fixCoord`` handles the
    // MP→three.js Y/Z coord conversion; the X flip here reflects
    // left↔right so user's anatomical-left arm data drives VRM's
    // right bone in screen-same-side orientation.
    if (this.mirror) _armA.x = -_armA.x;
    if (_armA.lengthSq() < 1e-8) return;
    _armA.normalize();

    // UpperArm's parent is upperChest. Observe the shoulder→elbow in
    // upperChest-local frame by inverse-rotating through
    // ``_upperChestWorld``. The rest direction in that frame is
    // (sideSign, 0, 0) — arms extend sideways in T-pose.
    _parentInv.copy(_upperChestWorld).invert();
    _obsLocal.copy(_armA).applyQuaternion(_parentInv);
    _restDir.set(sideSign, 0, 0);
    _bqA.setFromUnitVectors(_restDir, _obsLocal);
    this.writeBoneSmoothed(
      upperBone,
      [_bqA.x, _bqA.y, _bqA.z, _bqA.w],
      tsSec,
      out,
    );

    // --- Phase C: scapular shrug heuristic ---------------------------
    //
    // Exact scapula tracking from pose landmarks isn't available (no
    // sternoclavicular / acromion landmark). Approximation: when the
    // upperArm raises above horizontal, rotate the shoulder bone
    // proportional to the lift component. Rest direction is rotated
    // by ``_bqA`` so ``_shoulderDir`` is the current upperArm direction
    // in upperChest-local frame; its ``.y`` is how far above horizontal
    // the arm reaches (1 = straight up, 0 = horizontal, -1 = straight
    // down).
    _shoulderDir.set(sideSign, 0, 0).applyQuaternion(_bqA);
    const lift = _shoulderDir.y;
    if (lift > SHOULDER_LIFT_THRESHOLD) {
      // Map lift ∈ [0, 1] → shoulder rotation ∈ [0, SHOULDER_LIFT_MAX_RAD].
      // Negative ``sideSign`` on the right shoulder flips the axis so
      // both sides shrug UP (roll-in from the back) rather than one
      // shrugging up and the other down.
      //
      // SIGN-FLIP NOTE: the axis used is local X (pitch-forward axis
      // on a scapula); if the preview shows the shoulder rotating
      // anatomically-backwards instead of elevating, try negating
      // ``shrug`` (flip the sign in the axis-angle call).
      const clamped = Math.min(1, lift);
      const shrug =
        clamped * SHOULDER_LIFT_MAX_RAD * sideSign * this.shoulderLiftGain;
      // ``shoulderLiftGain === 0`` disables the shoulder write entirely
      // (bone stays at rest). Skip the bone write so the playback path
      // sees no track for it and keeps whatever the rest layer owns.
      if (this.shoulderLiftGain === 0) {
        this.clearBones(out, [shoulderBone]);
      } else {
        _shoulderQuat.setFromAxisAngle(_unitX, shrug);
        this.writeBoneSmoothed(
          shoulderBone,
          [_shoulderQuat.x, _shoulderQuat.y, _shoulderQuat.z, _shoulderQuat.w],
          tsSec,
          out,
        );
      }
    } else {
      // Arm below horizontal — let the bone rest. Delete so stale
      // shrug from the previous frame doesn't persist.
      this.clearBones(out, [shoulderBone]);
    }

    // Accumulated world rotation at the upperArm bone. In three-vrm's
    // normalized humanoid, the upperArm's local axes are the REST
    // frame — so the world rotation of upperArm is
    // ``parentWorld * localQuat``. When the arm is at rest, localQuat
    // is identity plus the "rest direction -> rest direction" rotation
    // from setFromUnitVectors which is also identity. So we correctly
    // compose parent * local here.
    _upperArmWorld.copy(_upperChestWorld).multiply(_bqA);

    // LowerArm: rest direction in upperArm-local frame.
    // In normalized humanoid, upperArm's child (elbow→wrist) points
    // along upperArm's local +Y. Guess-and-verify: if this produces a
    // lowerArm that points the WRONG way empirically, the rest-direction
    // is more likely along +X (same side as the upper arm's rest).
    // SIGN-FLIP NOTE: if elbow-to-wrist math looks inverted in the
    // preview, try flipping _restDirLower's Y sign here.
    _armB.set(wr.x - el.x, wr.y - el.y, wr.z - el.z);
    fixCoord(_armB);
    if (this.mirror) _armB.x = -_armB.x;
    if (_armB.lengthSq() < 1e-8) return;
    _armB.normalize();

    _parentInv.copy(_upperArmWorld).invert();
    _obsLocal.copy(_armB).applyQuaternion(_parentInv);
    _restDir.set(0, 1, 0);
    _bqB.setFromUnitVectors(_restDir, _obsLocal);

    // --- Phase B: forearm roll from hand landmarks -------------------
    //
    // ``_bqB`` so far captures elbow→wrist direction; what it does NOT
    // capture is the forearm's rotation around its own bone axis (a
    // twist). Extract that twist from the hand's palm-forward vector
    // (wrist→middle-MCP). Express it in lower-arm-local frame, project
    // onto the X-Z plane (perpendicular to the bone axis +Y), and take
    // the ``atan2`` — that is the twist angle around local Y.
    //
    // Handedness mapping: the arm chain here is driven by the USER'S
    // anatomical side that corresponds to this VRM side (via the
    // mirror-swap the caller already applied to ``lmShoulder``/etc).
    // MediaPipe HandLandmarker, fed the un-mirrored camera frame,
    // reports SWAPPED labels — "Left" means user's anatomical right.
    // So for VRM leftUpperArm (sideSign=+1), which receives user's
    // right arm data under mirror=true, pick the hand whose
    // ``categoryName === "Left"``. Conversely for the right arm.
    // When ``mirror=false`` the hand labels match, and the mapping
    // flips — so key off ``sideSign ^ mirror``.
    //
    // SIGN-FLIP NOTE: ``this.forearmRollRestRad`` + sign of
    // ``twistAngle`` are the knobs most likely to need a visual tweak.
    // If at rest the forearm sits pre-twisted or if turning the palm
    // toward the camera makes the bone rotate the wrong way, set
    // ``body.forearmRoll.signFlip`` in the catalog or adjust
    // ``body.forearmRoll.restRad``.
    const hr = this._latestHandResult;
    if (hr && hr.landmarks && hr.handednesses) {
      // Pick the MediaPipe label that matches this VRM side.
      // mirror=true: leftUpperArm (sideSign=+1) → "Left" label.
      // mirror=true: rightUpperArm (sideSign=-1) → "Right" label.
      // mirror=false: leftUpperArm → "Right" label (no swap applied
      //   upstream, so MediaPipe's raw selfie-frame labels stand).
      const vrmIsLeft = sideSign === 1;
      const wantLabel = this.mirror
        ? vrmIsLeft
          ? "Left"
          : "Right"
        : vrmIsLeft
          ? "Right"
          : "Left";
      let handIdx = -1;
      for (let i = 0; i < hr.handednesses.length; i++) {
        if (hr.handednesses[i]?.[0]?.categoryName === wantLabel) {
          handIdx = i;
          break;
        }
      }
      if (handIdx >= 0) {
        const lms = hr.landmarks[handIdx];
        if (lms && lms.length >= 21) {
          _handWristW.set(
            lms[WRIST_LM].x,
            lms[WRIST_LM].y,
            lms[WRIST_LM].z,
          );
          _handMidW.set(
            lms[MIDDLE_MCP_LM].x,
            lms[MIDDLE_MCP_LM].y,
            lms[MIDDLE_MCP_LM].z,
          );
          _handDir.copy(_handMidW).sub(_handWristW);
          fixCoord(_handDir);
          if (this.mirror) _handDir.x = -_handDir.x;
          if (_handDir.lengthSq() > 1e-8) {
            _handDir.normalize();
            // World rotation of the lower arm BEFORE applying twist.
            _lowerArmWorld.copy(_upperArmWorld).multiply(_bqB);
            _lowerArmWorldInv.copy(_lowerArmWorld).invert();
            _handDirLocal.copy(_handDir).applyQuaternion(_lowerArmWorldInv);
            // atan2(x, z): 0 when hand points along +Z in lower-arm-
            // local (matches the ``forearmRollRestRad`` reference);
            // π/2 when pointing along +X.
            let twistAngle =
              Math.atan2(_handDirLocal.x, _handDirLocal.z) -
              this.forearmRollRestRad;
            // Normalize to [-π, π] so clamp/gain act on the short arc.
            while (twistAngle > Math.PI) twistAngle -= 2 * Math.PI;
            while (twistAngle < -Math.PI) twistAngle += 2 * Math.PI;
            twistAngle *= this.forearmRollGain;
            if (this.forearmRollSignFlip) twistAngle = -twistAngle;
            if (twistAngle > FOREARM_ROLL_CLAMP_RAD) {
              twistAngle = FOREARM_ROLL_CLAMP_RAD;
            } else if (twistAngle < -FOREARM_ROLL_CLAMP_RAD) {
              twistAngle = -FOREARM_ROLL_CLAMP_RAD;
            }
            _twistQuat.setFromAxisAngle(_unitY, twistAngle);
            // Compose twist in lower-arm-local frame: the existing
            // ``_bqB`` puts the bone axis along +Y; multiplying by
            // ``_twistQuat`` (a rotation around local +Y) commutes
            // with the bone-axis component and adds the roll.
            _bqB.multiply(_twistQuat);
          }
        }
      }
    }

    this.writeBoneSmoothed(
      lowerBone,
      [_bqB.x, _bqB.y, _bqB.z, _bqB.w],
      tsSec,
      out,
    );

    // Hand bone: we don't have a distinct landmark for "middle-knuckle
    // of the body's hand" from the pose stream; HandLandmarker is a
    // separate solver. Keep the hand's orientation IDENTITY here so
    // the finger solver (driven from HandLandmarker per-hand landmarks)
    // owns the hand quaternion alone. If we wrote a rough hand rotation
    // here too, it would fight with whatever ``solveHands`` writes.
    //
    // SIMPLIFICATION: this means the hand bone rotation is whatever
    // ``solveHands`` writes (hand global orientation from palm frame)
    // OR nothing, if HandLandmarker isn't running.
    void handBone;
  }

  /** Solve one leg: upperLeg (parent = hips, yaw only), lowerLeg
   *  (parent = upperLeg), foot (parent = lowerLeg).
   *
   *  Rest direction for upperLeg in hips-local = (0, -1, 0) — legs
   *  hang down from the hip socket. LowerLeg's rest is (0, -1, 0) in
   *  upperLeg-local (three-vrm convention: child along local +Y, but
   *  the leg bones are flipped so child is toward local -Y. TODO:
   *  verify empirically — this is a GUESS and may need a sign flip).
   */
  private solveLegChain(
    upperBone: MocapBone,
    lowerBone: MocapBone,
    footBone: MocapBone,
    lmHip: number,
    lmKnee: number,
    lmAnkle: number,
    lmFoot: number,
    world: { x: number; y: number; z: number; visibility?: number }[],
    tsSec: number,
    out: ClipSample,
  ): void {
    const hip = world[lmHip];
    const kn = world[lmKnee];
    const an = world[lmAnkle];
    const ft = world[lmFoot];
    if (
      (hip?.visibility ?? 0) < VIS_GATE ||
      (kn?.visibility ?? 0) < VIS_GATE
    ) {
      this.clearBones(out, [upperBone, lowerBone]);
      return;
    }

    // Hip → knee in world. Mirror is already applied once on
    // ``_yawQuat`` (via torsoQuat's y,z negation). Don't flip vectors
    // again — double-mirror would invert legs.
    _legA.set(kn.x - hip.x, kn.y - hip.y, kn.z - hip.z);
    fixCoord(_legA);
    if (this.mirror) _legA.x = -_legA.x;
    if (_legA.lengthSq() < 1e-8) return;
    _legA.normalize();

    // Parent is hips (yaw only).
    _parentInv.copy(_yawQuat).invert();
    _obsLocal.copy(_legA).applyQuaternion(_parentInv);
    // Rest direction = straight down. SIGN-FLIP NOTE: if leg rotations
    // look inverted, try (0, +1, 0) here — depends on whether the rig's
    // upperLeg bone considers its child along local -Y or +Y.
    _restDir.set(0, -1, 0);
    _bqA.setFromUnitVectors(_restDir, _obsLocal);
    this.writeBoneSmoothed(
      upperBone,
      [_bqA.x, _bqA.y, _bqA.z, _bqA.w],
      tsSec,
      out,
    );

    // Accumulated world rotation at upperLeg.
    _upperArmWorld.copy(_yawQuat).multiply(_bqA);

    // LowerLeg: knee → ankle. No per-vector mirror flip (see _legA).
    if ((an?.visibility ?? 0) < VIS_GATE) {
      this.clearBones(out, [lowerBone, footBone]);
      return;
    }
    _legB.set(an.x - kn.x, an.y - kn.y, an.z - kn.z);
    fixCoord(_legB);
    if (this.mirror) _legB.x = -_legB.x;
    if (_legB.lengthSq() < 1e-8) return;
    _legB.normalize();
    _parentInv.copy(_upperArmWorld).invert();
    _obsLocal.copy(_legB).applyQuaternion(_parentInv);
    _restDir.set(0, -1, 0);
    _bqB.setFromUnitVectors(_restDir, _obsLocal);
    this.writeBoneSmoothed(
      lowerBone,
      [_bqB.x, _bqB.y, _bqB.z, _bqB.w],
      tsSec,
      out,
    );

    // Foot: ankle → foot-index. Rest direction for foot in
    // lowerLeg-local: the foot points FORWARD from the ankle (subject
    // facing +Z, so foot +Z in world). In lowerLeg-local at rest, that
    // translates roughly to (0, 0, 1). SIGN-FLIP NOTE: this is the
    // most-likely-wrong sign among the leg bones. If feet point
    // backward in preview, try (0, 0, -1).
    if ((ft?.visibility ?? 0) < VIS_GATE) {
      this.clearBones(out, [footBone]);
      return;
    }
    _legC.set(ft.x - an.x, ft.y - an.y, ft.z - an.z);
    fixCoord(_legC);
    if (this.mirror) _legC.x = -_legC.x;
    if (_legC.lengthSq() < 1e-8) return;
    _legC.normalize();
    // Accumulated world rotation at lowerLeg.
    _lowerArmWorld.copy(_upperArmWorld).multiply(_bqB);
    _parentInv.copy(_lowerArmWorld).invert();
    _obsLocal.copy(_legC).applyQuaternion(_parentInv);
    _restDir.set(0, 0, 1);
    _bqC.setFromUnitVectors(_restDir, _obsLocal);
    this.writeBoneSmoothed(
      footBone,
      [_bqC.x, _bqC.y, _bqC.z, _bqC.w],
      tsSec,
      out,
    );
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

  /** Remove bone entries from the output sample. Called at visibility-gate
   *  failure sites so the playback layer falls through to idle/procedural
   *  animation instead of holding the previous frame's stale IK value
   *  (which manifested as the VRM "freezing" when partially out of frame).
   */
  private clearBones(out: ClipSample, names: readonly MocapBone[]): void {
    for (const n of names) delete out.bones[n];
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
   *
   *  When a side is not detected this frame, its finger-joint entries
   *  are removed from ``out`` so the playback layer falls back to
   *  procedural idle rather than freezing on the last pose.
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
    // First pass: figure out which VRM sides have a valid hand this
    // frame (applying the same mirror-swap the solve pass uses). We
    // do this up-front so we can purge stale finger bones for sides
    // that went missing BEFORE the per-hand solve writes new ones.
    const wantedSides = { left: false, right: false };
    if (hands && sides) {
      for (let i = 0; i < hands.length; i++) {
        const lm = hands[i];
        const mpLabel = sides[i]?.[0]?.categoryName;
        if (!lm || lm.length < 21 || !mpLabel) continue;
        const userIsLeft = mpLabel === "Right";
        const vrmIsLeft = this.mirror ? !userIsLeft : userIsLeft;
        if (vrmIsLeft) wantedSides.left = true;
        else wantedSides.right = true;
      }
    }
    // Purge finger-bone entries for sides that weren't detected, so
    // the playback layer falls back to procedural idle rather than
    // freezing on whatever pose was written last frame. Only finger
    // joints (names from ``LEFT_JOINTS`` / ``RIGHT_JOINTS``) are
    // removed — body bones are untouched.
    if (!wantedSides.left) {
      for (const [boneName] of LEFT_JOINTS) {
        delete out.bones[boneName];
      }
    }
    if (!wantedSides.right) {
      for (const [boneName] of RIGHT_JOINTS) {
        delete out.bones[boneName];
      }
    }
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
   *  Non-thumb proximals also get a signed abduction rotation around
   *  the local palm-normal axis (see ``PROXIMAL_SPREAD``), composed on
   *  top of curl via a second quaternion multiply. This gives V-signs,
   *  spread-hand, and adjacent-finger-split gestures in addition to
   *  plain flexion. Thumb opposition still pending — would need its
   *  own palm-frame work.
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

    // Full palm frame — needed for finger spread (abduction). Build
    // ``palmX`` (across palm, index-MCP → pinky-MCP orthogonalised
    // against palmY) and ``palmZ`` (palm normal). If the hand is
    // edge-on to the camera, the index/pinky-MCP span collapses along
    // ``palmY`` and the cross axis becomes degenerate — in that case
    // flag the frame so spread is skipped but curl still runs.
    _hIdxMcp.set(lm[INDEX_MCP_LM].x, lm[INDEX_MCP_LM].y, lm[INDEX_MCP_LM].z);
    _hPkyMcp.set(lm[PINKY_MCP_LM].x, lm[PINKY_MCP_LM].y, lm[PINKY_MCP_LM].z);
    _palmX.copy(_hPkyMcp).sub(_hIdxMcp);
    const dotXY = _palmX.dot(_palmY);
    _palmX.addScaledVector(_palmY, -dotXY);
    if (_palmX.lengthSq() < 1e-10) {
      _palmFrameValid = false;
    } else {
      _palmX.normalize();
      _palmZ.copy(_palmX).cross(_palmY).normalize();
      _palmFrameValid = true;
    }

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
      // Thumb IP (``thumbDistal``) uses landmark 4 as its child-end,
      // which MediaPipe frequently occludes at a closed fist — tip
      // gets reported essentially on top of the IP (landmark 3),
      // producing a near-zero direction that normalizes to a jittery
      // quaternion. Use a more aggressive threshold for this joint
      // and skip writing it; the OneEuroQuat filter will hold the
      // last valid value, which is less disruptive than freezing on
      // a numerically-unstable update.
      const minChildLenSq = jointType === "thumbDistal" ? 1e-4 : 1e-10;
      if (_childDir.lengthSq() < minChildLenSq) continue;
      _childDir.normalize();

      // --- Curl angle, calibrated + signed ---
      const cos = Math.max(-1, Math.min(1, parentVec.dot(_childDir)));
      const curl = Math.acos(cos);
      // Track peak across ALL joints for the "is anything moving?"
      // diagnostic. Proximals dominate at open/close; intermediate/
      // distal reach similar magnitudes only at full fist. We
      // deliberately track the RAW curl here (pre-``invertRaw``) so
      // the diagnostic's "signal present?" semantics stay consistent
      // across joint types.
      this.latestFingerMaxCurl = Math.max(this.latestFingerMaxCurl, curl);

      const cal = this.effectiveCalibration[jointType];
      // Joints whose measured angle DECREASES during flexion (thumb
      // CMC opposition) invert the raw signal so the usual
      // rest<fist mapping still applies.
      const rawCurl = cal.invertRaw ? Math.PI / 2 - curl : curl;
      const span = cal.fistRad - cal.restRad;
      const norm =
        span > 1e-6 ? Math.max(0, Math.min(1, (rawCurl - cal.restRad) / span)) : 0;
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

      // --- Spread (abduction/adduction) composed on top of curl ---
      //
      // Only non-thumb proximals carry a ``SPREAD_REST_RAD`` entry. We
      // compose curl and spread inside the solver (rather than writing
      // two separate animation tracks per bone) because the OneEuro
      // filter is a single per-bone quaternion-space smoother — one
      // composed quaternion in / out preserves the filter's stability
      // guarantees. Composing externally would require two filters per
      // bone and a post-hoc multiply on the playback side, which is
      // more state to reason about with no quality gain.
      //
      // Application order: curl first, then spread. Both rotations act
      // on the rest-pose frame, so ``_handQ = curl; _handQ.multiply(
      // spread)`` produces ``curl ∘ spread`` applied to the rest
      // vector (three.js multiplies on the right).
      const restSplay = SPREAD_REST_RAD[boneName];
      if (restSplay !== undefined && _palmFrameValid) {
        // ``_childDir`` here is the MCP→PIP segment (e.g. lm 5→6 for
        // index). Project onto the palm plane by using its palmX /
        // palmY components only — palmZ would be "how much the finger
        // lifts off the palm plane" which we ignore.
        const dirX = _childDir.dot(_palmX);
        const dirY = _childDir.dot(_palmY);
        const splayRaw = Math.atan2(dirX, dirY);
        let delta = splayRaw - restSplay;
        if (delta > PROXIMAL_SPREAD.clampRad) delta = PROXIMAL_SPREAD.clampRad;
        else if (delta < -PROXIMAL_SPREAD.clampRad) delta = -PROXIMAL_SPREAD.clampRad;
        const normSplay = delta / PROXIMAL_SPREAD.clampRad; // -1..+1
        const spreadSign = (PROXIMAL_SPREAD.flipSign ? -1 : 1) * curlSign;
        const spreadRot = normSplay * PROXIMAL_SPREAD.outRangeRad * spreadSign;

        _spreadEuler.set(0, 0, 0, "XYZ");
        if (PROXIMAL_SPREAD.axis === "x") _spreadEuler.x = spreadRot;
        else if (PROXIMAL_SPREAD.axis === "y") _spreadEuler.y = spreadRot;
        else _spreadEuler.z = spreadRot;
        _composedQ.setFromEuler(_spreadEuler);
        _handQ.multiply(_composedQ);
      }

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

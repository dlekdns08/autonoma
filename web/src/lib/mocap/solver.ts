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
  /** Live calibration table — defaults to ``CALIBRATION`` and can be
   *  swapped in per-VRM via ``setVrmOverrides``. Read each frame by
   *  ``solveOneHand`` so override changes take effect on the next
   *  solve without needing a solver reinit. */
  private effectiveCalibration: Record<JointType, JointCalibration>;

  constructor(opts: SolverOptions = {}) {
    this.mirror = opts.mirror ?? true;
    this.cfg = opts.oneEuro;
    this.effectiveCalibration = { ...CALIBRATION };
  }

  /** Apply per-VRM calibration overrides on top of the default
   *  ``CALIBRATION`` table. Pass ``null`` to revert to defaults. Only
   *  specified fields are overridden — missing fields fall through to
   *  the base. Degree fields on the override are converted to radians
   *  at merge time so the solve loop never has to divide by π. */
  setVrmOverrides(overrides: VrmMocapOverrides | null): void {
    if (!overrides) {
      this.effectiveCalibration = { ...CALIBRATION };
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

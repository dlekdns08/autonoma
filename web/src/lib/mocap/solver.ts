/**
 * MediaPipe Tasks Vision → VRM humanoid adapter.
 *
 * Face (blendshapes + neck/head pose from facialTransformationMatrix)
 * is left untouched — the user verified it works.
 *
 * Body / arms / legs / hands / fingers use direct landmark IK with
 * a SINGLE mirror preprocessing pass at the top of each solve:
 *
 *   - Pose landmarks: swap left/right index pairs + negate X on every
 *     coordinate. After this, ``pose[LM_LEFT_*]`` directly means
 *     "what drives VRM-left" and downstream IK is oblivious to mirror.
 *   - Hand landmarks: negate X on every point; keep the MediaPipe
 *     handedness label (which under mirror=true becomes the VRM-side
 *     label directly). Under mirror=false we flip the label instead.
 *
 * Previous revisions composed three separate reflections (landmark
 * swap + per-vector X/Y flips + torso quaternion y,z negation) which
 * kept accumulating edge bugs. One reflection at the input boundary
 * is both correct and auditable.
 *
 * Partial-visibility handling: each bone family gates on its own
 * landmarks. When a gate fails, the bones are DROPPED from the
 * output sample — the apply layer (``applyBoneSampleAllWithDecay``)
 * slerps back toward the captured baseline. Chain dependencies:
 *   - hip pair missing → skip torso/spine/legs, still drive arms
 *     against identity upperChest (common "desk webcam" case)
 *   - shoulder pair missing → skip arms, still drive legs
 *   - per arm: any of shoulder/elbow/wrist low-vis → skip that arm
 *   - per leg: hip+knee needed for upper; ankle for lower; foot for foot
 *   - per hand: only solves for hands detected this frame
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

// ── Blendshape → VRM expression mapping (unchanged) ─────────────────
const BLENDSHAPE_TO_VRM: Record<string, [MocapExpression, number][]> = {
  eyeBlinkLeft: [["blinkLeft", 1], ["blink", 0.5]],
  eyeBlinkRight: [["blinkRight", 1], ["blink", 0.5]],
  mouthSmileLeft: [["happy", 0.6]],
  mouthSmileRight: [["happy", 0.6]],
  cheekSquintLeft: [["happy", 0.2]],
  cheekSquintRight: [["happy", 0.2]],
  browDownLeft: [["angry", 0.5]],
  browDownRight: [["angry", 0.5]],
  mouthFrownLeft: [["angry", 0.4], ["sad", 0.3]],
  mouthFrownRight: [["angry", 0.4], ["sad", 0.3]],
  browInnerUp: [["sad", 0.6]],
  mouthShrugUpper: [["relaxed", 0.3]],
  eyeWideLeft: [["surprised", 0.5]],
  eyeWideRight: [["surprised", 0.5]],
  browOuterUpLeft: [["surprised", 0.3]],
  browOuterUpRight: [["surprised", 0.3]],
  jawOpen: [["aa", 1]],
  mouthFunnel: [["ou", 1]],
  mouthPucker: [["ou", 0.6], ["oh", 0.4]],
  mouthClose: [["ih", 0.5]],
  mouthStretchLeft: [["ee", 0.5]],
  mouthStretchRight: [["ee", 0.5]],
};

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

// ── MediaPipe Pose landmark indices ────────────────────────────────
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

/** Left/right index pairs swapped during mirror preprocessing. Body IK
 *  reads only these indices; non-body landmarks (face, eyes, mouth)
 *  are left in place since the pose path ignores them. */
const LR_POSE_PAIRS: ReadonlyArray<readonly [number, number]> = [
  [LM_LEFT_SHOULDER, LM_RIGHT_SHOULDER],
  [LM_LEFT_ELBOW, LM_RIGHT_ELBOW],
  [LM_LEFT_WRIST, LM_RIGHT_WRIST],
  [LM_LEFT_HIP, LM_RIGHT_HIP],
  [LM_LEFT_KNEE, LM_RIGHT_KNEE],
  [LM_LEFT_ANKLE, LM_RIGHT_ANKLE],
  [LM_LEFT_FOOT_INDEX, LM_RIGHT_FOOT_INDEX],
];

/** Permissive gate for torso/leg landmarks — MediaPipe reports
 *  confident visibility in the 0.2-0.4 range for mid-torso webcam
 *  shots we still want to drive. */
const VIS_GATE = 0.15;

/** Stricter gate for arm landmarks (shoulder, elbow, wrist).
 *
 *  MediaPipe Pose hallucinates arm positions with non-zero visibility
 *  when the arm is occluded / out of frame — an upper-body webcam shot
 *  with hands below the desk produces "arms raised overhead" pose
 *  guesses. Empirically those guesses can hit visibility 0.5–0.7, so
 *  0.5 is NOT enough to suppress them.
 *
 *  0.7 was tuned by observing the midori.vrm occluded-arm case: at 0.5
 *  the VRM's arms stayed pinned overhead (hallucinated IK dominating);
 *  at 0.7 the apply-layer decay path kicks in and the arms settle to
 *  the captured baseline (arms-down). Kept strictly above
 *  ``MIN_VISIBILITY`` (0.3, skeleton overlay) so the IK never runs
 *  when the overlay isn't drawing the joint.
 *
 *  Rest-pose handling (what happens when this gate fails): the solver
 *  simply drops the bone from ``out.bones`` via ``clearBones``. The
 *  apply layer (``applyBoneSampleAllWithDecay`` in ``vrmShared``)
 *  slerps the VRM bone back toward its captured baseline (arms-down).
 *  Keeping rest logic out of the solver avoids the indirect path where
 *  solver writes → OneEuro → sample → apply that we previously tried,
 *  which was brittle enough to visibly fail on ``midori.vrm``. */
const ARM_VIS_GATE = 0.7;

const TORSO_BONES: readonly MocapBone[] = [
  "hips",
  "spine",
  "chest",
  "upperChest",
] as const;

// ── Coordinate conversion ────────────────────────────────────────────
// MediaPipe worldLandmarks: +X=subject-left, +Y=down, +Z=away.
// three.js / VRM:            +X=subject-left, +Y=up,   +Z=toward-camera.
function fixCoord(v: THREE.Vector3): void {
  v.y = -v.y;
  v.z = -v.z;
}

function median3(a: number, b: number, c: number): number {
  return Math.max(Math.min(a, b), Math.min(Math.max(a, b), c));
}

// ── Body IK scratch (zero-alloc steady state) ───────────────────────
const _Y_AXIS = new THREE.Vector3(0, 1, 0);
const _unitX = new THREE.Vector3(1, 0, 0);
const _unitY = new THREE.Vector3(0, 1, 0);
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
const _spineQuat = new THREE.Quaternion();
const _chestQuat = new THREE.Quaternion();
const _upperChestQuat = new THREE.Quaternion();

// Forearm roll scratch.
const _handWristW = new THREE.Vector3();
const _handMidW = new THREE.Vector3();
const _handDir = new THREE.Vector3();
const _handDirLocal = new THREE.Vector3();
const _twistQuat = new THREE.Quaternion();
const _lowerArmWorldInv = new THREE.Quaternion();

/** Cap applied forearm roll so a bad hand-landmark frame can't yank
 *  the bone 180°. */
const FOREARM_ROLL_CLAMP_RAD = (60 * Math.PI) / 180;

/** Spine distribution — fixed lumbar-biased weights. Sum to 1.0. */
const SPINE_WEIGHT = 0.45;
const CHEST_WEIGHT = 0.35;
const UPPERCHEST_WEIGHT = 0.20;

/** Body bones that get the softer OneEuro preset. Finger bones use
 *  the aggressive default since they amplify landmark noise. */
const BODY_BONE_NAMES: ReadonlySet<MocapBone> = new Set<MocapBone>([
  "hips", "spine", "chest", "upperChest", "neck", "head",
  "leftShoulder", "rightShoulder",
  "leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm",
  "leftHand", "rightHand",
  "leftUpperLeg", "rightUpperLeg", "leftLowerLeg", "rightLowerLeg",
  "leftFoot", "rightFoot",
]);

// ── Hand landmark indices ───────────────────────────────────────────
const WRIST_LM = 0;
const INDEX_MCP_LM = 5;
const MIDDLE_MCP_LM = 9;
const PINKY_MCP_LM = 17;

type JointType =
  | "proximal" | "intermediate" | "distal"
  | "thumbProximal" | "thumbDistal" | "thumbMetacarpal";

type RotationAxis = "x" | "y" | "z";

interface JointCalibration {
  restRad: number;
  fistRad: number;
  outRangeRad: number;
  axis: RotationAxis;
  flipSign?: boolean;
  invertRaw?: boolean;
}

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

const PROXIMAL_SPREAD = {
  axis: "y" as RotationAxis,
  outRangeRad: (30 * Math.PI) / 180,
  flipSign: false,
  clampRad: (20 * Math.PI) / 180,
};

const CALIBRATION: Record<JointType, JointCalibration> = {
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
  thumbProximal: {
    restRad: (5 * Math.PI) / 180,
    fistRad: (30 * Math.PI) / 180,
    outRangeRad: (45 * Math.PI) / 180,
    axis: "x",
    flipSign: true,
  },
  thumbDistal: {
    restRad: (5 * Math.PI) / 180,
    fistRad: (30 * Math.PI) / 180,
    outRangeRad: (55 * Math.PI) / 180,
    axis: "x",
    flipSign: true,
  },
  thumbMetacarpal: {
    restRad: (5 * Math.PI) / 180,
    fistRad: (55 * Math.PI) / 180,
    outRangeRad: (35 * Math.PI) / 180,
    axis: "y",
    flipSign: false,
    invertRaw: true,
  },
};

type ParentRef = "palm" | readonly [number, number];

type FingerJoint = readonly [
  bone: MocapBone,
  parent: ParentRef,
  childFrom: number,
  childTo: number,
  joint: JointType,
];

const LEFT_JOINTS: readonly FingerJoint[] = [
  ["leftThumbMetacarpal",    "palm", 1, 2, "thumbMetacarpal"],
  ["leftThumbProximal",      [1, 2], 2, 3, "thumbProximal"],
  ["leftThumbDistal",        [2, 3], 3, 4, "thumbDistal"],
  ["leftIndexProximal",      "palm", 5, 6, "proximal"],
  ["leftIndexIntermediate",  [5, 6], 6, 7, "intermediate"],
  ["leftIndexDistal",        [6, 7], 7, 8, "distal"],
  ["leftMiddleProximal",     "palm", 9, 10, "proximal"],
  ["leftMiddleIntermediate", [9, 10], 10, 11, "intermediate"],
  ["leftMiddleDistal",       [10, 11], 11, 12, "distal"],
  ["leftRingProximal",       "palm", 13, 14, "proximal"],
  ["leftRingIntermediate",   [13, 14], 14, 15, "intermediate"],
  ["leftRingDistal",         [14, 15], 15, 16, "distal"],
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
let _palmFrameValid = false;

// ── Public API ──────────────────────────────────────────────────────

export interface SolverOptions {
  /** Mirror the webcam so the user sees decalcomania behaviour: user
   *  raises anatomical-left arm → VRM raises its anatomical-right arm,
   *  with both appearing on the same screen side (the "real mirror"
   *  feel). Default true — turn off only for non-selfie sources. */
  mirror?: boolean;
  oneEuro?: OneEuroConfig;
}

/** Mutable landmark record shape used by the ring buffer + mirrored
 *  copy. Kept as a plain record so TS erases the type at runtime. */
type MutableLandmark = {
  x: number;
  y: number;
  z: number;
  visibility: number;
};

type MutableHandLandmark = { x: number; y: number; z: number };

export class MocapSolver {
  private readonly mirror: boolean;
  private readonly cfg: OneEuroConfig | undefined;
  private readonly quatFilters: Partial<Record<MocapBone, OneEuroQuat>> = {};
  private readonly scalarFilters: Partial<Record<MocapExpression, OneEuroScalar>> = {};
  private readonly scratch: [number, number, number, number] = [0, 0, 0, 1];
  /** Forearm roll is the only body-level override we still honour per
   *  rig — elbow→wrist direction alone can't recover bone twist, so
   *  a rest/gain/sign-flip knob is sometimes needed. Shoulder-lift,
   *  spine weights etc. are gone — the new IK derives them directly. */
  private forearmRollRestRad = 0;
  private forearmRollGain = 0.8;
  private forearmRollSignFlip = false;
  private effectiveCalibration: Record<JointType, JointCalibration>;

  // 3-frame median ring buffer for pose landmarks.
  private readonly _poseHistorySlots: MutableLandmark[][] = [];
  private _poseHistoryHead = 0;
  private _poseHistorySize = 0;
  private readonly _poseMedian: MutableLandmark[] = [];
  /** Mirror-preprocessed pose landmarks. Same object reused each frame.
   *  Body IK reads from this array; the raw unmirrored pose is still
   *  published to the overlay via useMocap's own ref. */
  private readonly _poseMirrored: MutableLandmark[] = [];

  // Pre-allocated mirror-preprocessed hand data. Up to two hands.
  private readonly _handMirrored: MutableHandLandmark[][] = [
    Array.from({ length: 21 }, () => ({ x: 0, y: 0, z: 0 })),
    Array.from({ length: 21 }, () => ({ x: 0, y: 0, z: 0 })),
  ];
  private readonly _handVrmSide: ("Left" | "Right" | null)[] = [null, null];
  private _handMirroredCount = 0;

  constructor(opts: SolverOptions = {}) {
    this.mirror = opts.mirror ?? true;
    this.cfg = opts.oneEuro;
    this.effectiveCalibration = { ...CALIBRATION };
    for (let i = 0; i < 33; i++) {
      this._poseMedian.push({ x: 0, y: 0, z: 0, visibility: 0 });
      this._poseMirrored.push({ x: 0, y: 0, z: 0, visibility: 0 });
    }
    for (let r = 0; r < 3; r++) {
      const slot: MutableLandmark[] = [];
      for (let i = 0; i < 33; i++) {
        slot.push({ x: 0, y: 0, z: 0, visibility: 0 });
      }
      this._poseHistorySlots.push(slot);
    }
  }

  setVrmOverrides(overrides: VrmMocapOverrides | null): void {
    const defaultForearmRollRestRad = 0;
    const defaultForearmRollGain = 0.8;
    const defaultForearmRollSignFlip = false;

    if (!overrides) {
      this.effectiveCalibration = { ...CALIBRATION };
      this.forearmRollRestRad = defaultForearmRollRestRad;
      this.forearmRollGain = defaultForearmRollGain;
      this.forearmRollSignFlip = defaultForearmRollSignFlip;
      return;
    }

    // Finger calibration overrides — required for cross-rig finger
    // curl axis conventions.
    const merged: Record<JointType, JointCalibration> = { ...CALIBRATION };
    for (const key of Object.keys(CALIBRATION) as JointType[]) {
      const base = CALIBRATION[key];
      const ov = overrides[key];
      if (!ov) continue;
      merged[key] = {
        axis: ov.axis ?? base.axis,
        flipSign: ov.flipSign ?? base.flipSign,
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

    // Forearm roll per-rig knob.
    const forearm = overrides.body?.forearmRoll;
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
    this._poseHistoryHead = 0;
    this._poseHistorySize = 0;
    this.resetArmDiag("left");
    this.resetArmDiag("right");
    this._armDiagRingSize.left = 0;
    this._armDiagRingSize.right = 0;
    this._armDiagRingHead.left = 0;
    this._armDiagRingHead.right = 0;
    this._handMirroredCount = 0;
  }

  private smoothPoseLandmarks(
    raw: Array<{ x: number; y: number; z: number; visibility?: number }>,
  ): MutableLandmark[] | null {
    if (raw.length < 33) return null;
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

    const slot0 = this._poseHistorySlots[0];
    const slot1 = this._poseHistorySlots[1];
    const slot2 = this._poseHistorySlots[2];

    if (n === 2) {
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

  /** Apply the single-reflection mirror to the median-smoothed pose.
   *  Under mirror=false this just copies through (so downstream IK
   *  always reads from one array). */
  private applyPoseMirror(src: MutableLandmark[]): MutableLandmark[] {
    if (!this.mirror) {
      for (let i = 0; i < 33; i++) {
        const s = src[i];
        const d = this._poseMirrored[i];
        d.x = s.x;
        d.y = s.y;
        d.z = s.z;
        d.visibility = s.visibility;
      }
      return this._poseMirrored;
    }
    for (let i = 0; i < 33; i++) {
      const s = src[i];
      const d = this._poseMirrored[i];
      d.x = -s.x;
      d.y = s.y;
      d.z = s.z;
      d.visibility = s.visibility;
    }
    for (const [a, b] of LR_POSE_PAIRS) {
      const ta = this._poseMirrored[a];
      const tb = this._poseMirrored[b];
      const tx = ta.x, ty = ta.y, tz = ta.z, tv = ta.visibility;
      ta.x = tb.x; ta.y = tb.y; ta.z = tb.z; ta.visibility = tb.visibility;
      tb.x = tx; tb.y = ty; tb.z = tz; tb.visibility = tv;
    }
    return this._poseMirrored;
  }

  /** Apply the single-reflection mirror to a HandLandmarker result.
   *  Populates ``_handMirrored`` + ``_handVrmSide`` for up to 2 hands.
   *  - mirror=true:  X-negate landmark coords, KEEP MediaPipe label
   *                  (MP "Left" = user-right = drives VRM-left)
   *  - mirror=false: no coord flip, FLIP label
   *                  (MP "Left" = user-right = drives VRM-right)
   *
   *  Returns the number of hands prepared. */
  private applyHandMirror(result: HandLandmarkerResult | null): number {
    this._handMirroredCount = 0;
    if (!result) return 0;
    const hands = result.landmarks;
    const sides = result.handednesses;
    if (!hands || !sides) return 0;
    const n = Math.min(hands.length, sides.length, 2);
    for (let i = 0; i < n; i++) {
      const src = hands[i];
      const mpLabel = sides[i]?.[0]?.categoryName;
      if (!src || src.length < 21 || (mpLabel !== "Left" && mpLabel !== "Right")) {
        this._handVrmSide[i] = null;
        continue;
      }
      const dst = this._handMirrored[i];
      for (let j = 0; j < 21; j++) {
        const p = src[j];
        const d = dst[j];
        d.x = this.mirror ? -p.x : p.x;
        d.y = p.y;
        d.z = p.z;
      }
      this._handVrmSide[i] = this.mirror
        ? mpLabel
        : mpLabel === "Left"
          ? "Right"
          : "Left";
    }
    this._handMirroredCount = n;
    return n;
  }

  private quatFilter(name: MocapBone): OneEuroQuat {
    let f = this.quatFilters[name];
    if (!f) {
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
    this.latestFingerMaxCurl = 0;

    // Mirror preprocessing: runs once regardless of whether pose/hands
    // are present (so _handMirroredCount etc. reset deterministically).
    this.applyHandMirror(hands);

    if (face) this.solveFace(face, tsSec, out);
    if (pose) this.solvePose(pose, tsSec, out);
    if (hands) this.solveHands(tsSec, out);
  }

  // ── Face (unchanged behaviour) ────────────────────────────────────
  private solveFace(
    face: FaceLandmarkerResult,
    tsSec: number,
    out: ClipSample,
  ): void {
    const shapes = face.faceBlendshapes?.[0]?.categories;
    if (shapes) {
      const raw: Partial<Record<MocapExpression, number>> = {};
      mapBlendshapes(shapes, raw);
      for (const [name, v] of Object.entries(raw) as [MocapExpression, number][]) {
        const smoothed = this.scalarFilter(name).filter(v, tsSec);
        out.expressions[name] = smoothed;
      }
    }
    const mats = face.facialTransformationMatrixes;
    const mat = mats?.[0]?.data;
    if (mat) {
      quatFromMatrix(mat, this.scratch);
      if (this.mirror) {
        this.scratch[0] = -this.scratch[0];
        this.scratch[1] = -this.scratch[1];
      }
      const half: [number, number, number, number] = [
        this.scratch[0] * 0.5,
        this.scratch[1] * 0.5,
        this.scratch[2] * 0.5,
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

  // ── Body IK ──────────────────────────────────────────────────────
  private solvePose(
    pose: PoseLandmarkerResult,
    tsSec: number,
    out: ClipSample,
  ): void {
    const raw = pose.worldLandmarks?.[0];
    if (!raw || raw.length < 33) return;
    const median = this.smoothPoseLandmarks(raw);
    if (!median) return;
    const lm = this.applyPoseMirror(median);
    this.solveBodyIK(lm, tsSec, out);
  }

  /** Drive hips / spine / arms / legs from mirror-preprocessed world
   *  landmarks. Each bone family gates independently on its own
   *  visibility so a subject only partially in frame still gets the
   *  parts we can see. */
  private solveBodyIK(
    lm: MutableLandmark[],
    tsSec: number,
    out: ClipSample,
  ): void {
    const lHip = lm[LM_LEFT_HIP];
    const rHip = lm[LM_RIGHT_HIP];
    const lSh = lm[LM_LEFT_SHOULDER];
    const rSh = lm[LM_RIGHT_SHOULDER];
    const hipVis =
      lHip.visibility > VIS_GATE && rHip.visibility > VIS_GATE;
    const shoulderVis =
      lSh.visibility > VIS_GATE && rSh.visibility > VIS_GATE;

    // Fully off-frame: clear torso and bail. Arms/legs would fail
    // their own gates anyway; leaving their existing entries out
    // lets the apply layer decay everything to baseline.
    if (!hipVis && !shoulderVis) {
      this.clearBones(out, TORSO_BONES);
      return;
    }

    // Desk-webcam case: shoulders visible, hips hidden under desk.
    // Identity torso frame → arms drive against rest upperChest,
    // legs can't be computed so we skip them.
    if (!hipVis) {
      this.clearBones(out, TORSO_BONES);
      _torsoQuat.identity();
      _yawQuat.identity();
      _upperChestWorld.identity();
      this.solveArms(lm, tsSec, out);
      return;
    }

    // Rare "head cut off" case: hips present, shoulders aren't.
    // Legs can still run from hips+knee; arms can't.
    if (!shoulderVis) {
      this.clearBones(out, TORSO_BONES);
      _torsoQuat.identity();
      _yawQuat.identity();
      _upperChestWorld.identity();
      this.solveLegs(lm, tsSec, out);
      return;
    }

    // Torso basis from mirror-preprocessed landmarks. No extra flips.
    _bX.set(lHip.x - rHip.x, lHip.y - rHip.y, lHip.z - rHip.z);
    fixCoord(_bX);
    if (_bX.lengthSq() < 1e-6) return;
    _bX.normalize();
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
    _bZ.copy(_bX).cross(_bY).normalize();

    _torsoMat.makeBasis(_bX, _bY, _bZ);
    _torsoQuat.setFromRotationMatrix(_torsoMat);

    // Hips: yaw only.
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

    // Spine chain: weighted slerp of the torso's remaining rotation
    // (bend + lean = torso × yaw⁻¹) across three vertebra bones.
    _remaining.copy(_torsoQuat).multiply(_tmpQuat.copy(_yawQuat).invert());
    _identity.identity();
    _spineQuat.copy(_identity).slerp(_remaining, SPINE_WEIGHT);
    _chestQuat.copy(_identity).slerp(_remaining, CHEST_WEIGHT);
    _upperChestQuat.copy(_identity).slerp(_remaining, UPPERCHEST_WEIGHT);
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

    _upperChestWorld.copy(_torsoQuat);

    this.solveArms(lm, tsSec, out);
    this.solveLegs(lm, tsSec, out);
  }

  private solveArms(
    lm: MutableLandmark[],
    tsSec: number,
    out: ClipSample,
  ): void {
    this.solveArmChain(
      "leftUpperArm", "leftLowerArm", "leftHand",
      LM_LEFT_SHOULDER, LM_LEFT_ELBOW, LM_LEFT_WRIST,
      +1, "Left",
      lm, tsSec, out,
    );
    this.solveArmChain(
      "rightUpperArm", "rightLowerArm", "rightHand",
      LM_RIGHT_SHOULDER, LM_RIGHT_ELBOW, LM_RIGHT_WRIST,
      -1, "Right",
      lm, tsSec, out,
    );
  }

  private solveLegs(
    lm: MutableLandmark[],
    tsSec: number,
    out: ClipSample,
  ): void {
    this.solveLegChain(
      "leftUpperLeg", "leftLowerLeg", "leftFoot",
      LM_LEFT_HIP, LM_LEFT_KNEE, LM_LEFT_ANKLE, LM_LEFT_FOOT_INDEX,
      lm, tsSec, out,
    );
    this.solveLegChain(
      "rightUpperLeg", "rightLowerLeg", "rightFoot",
      LM_RIGHT_HIP, LM_RIGHT_KNEE, LM_RIGHT_ANKLE, LM_RIGHT_FOOT_INDEX,
      lm, tsSec, out,
    );
  }

  private solveArmChain(
    upperBone: MocapBone,
    lowerBone: MocapBone,
    handBone: MocapBone,
    lmShoulder: number,
    lmElbow: number,
    lmWrist: number,
    sideSign: 1 | -1,
    vrmSideLabel: "Left" | "Right",
    lm: MutableLandmark[],
    tsSec: number,
    out: ClipSample,
  ): void {
    void handBone;
    const diagSide: "left" | "right" = sideSign === 1 ? "left" : "right";
    const sh = lm[lmShoulder];
    const el = lm[lmElbow];
    const wr = lm[lmWrist];
    if (
      sh.visibility < ARM_VIS_GATE ||
      el.visibility < ARM_VIS_GATE ||
      wr.visibility < ARM_VIS_GATE
    ) {
      this.clearBones(out, [upperBone, lowerBone]);
      this.resetArmDiag(diagSide);
      return;
    }

    // shoulder → elbow in world (mirror-preprocessed landmarks → no
    // per-vector flips needed).
    _armA.set(el.x - sh.x, el.y - sh.y, el.z - sh.z);
    fixCoord(_armA);
    if (_armA.lengthSq() < 1e-8) {
      this.clearBones(out, [upperBone, lowerBone]);
      this.resetArmDiag(diagSide);
      return;
    }
    _armA.normalize();
    this.updateArmDiag(diagSide, _armA.x, _armA.y, _armA.z);

    // UpperArm: rest direction in upperChest-local = (sideSign, 0, 0).
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

    _upperArmWorld.copy(_upperChestWorld).multiply(_bqA);

    // LowerArm: elbow → wrist; rest along upperArm-local +Y.
    _armB.set(wr.x - el.x, wr.y - el.y, wr.z - el.z);
    fixCoord(_armB);
    if (_armB.lengthSq() < 1e-8) {
      this.clearBones(out, [lowerBone]);
      return;
    }
    _armB.normalize();

    _parentInv.copy(_upperArmWorld).invert();
    _obsLocal.copy(_armB).applyQuaternion(_parentInv);
    // Rest direction is along the bone at T-pose expressed in the
    // parent (upperArm) local frame. three-vrm's normalized humanoid
    // bones at identity rotation have their LOCAL frame equal to the
    // parent's local frame (no baked along-bone rotation — the mesh
    // is baked to T-pose geometry separately). So the elbow→wrist
    // direction at T-pose in upperArm-local is the same as in world:
    // +X for the left arm, -X for the right. Using (0, +1, 0) here
    // produced an identity bone write whenever the user's forearm
    // actually pointed along +Y (raised), leaving the lowerArm
    // locked in T-pose horizontal — symptom the user hit when the
    // elbows-at-shoulder-level + forearm-up pose.
    _restDir.set(sideSign, 0, 0);
    _bqB.setFromUnitVectors(_restDir, _obsLocal);

    // Forearm roll from the matching hand's palm-forward direction.
    // Twist is around the bone's own axis (local ±X after the rest
    // change above) applied in the bone's post-``_bqB`` frame via
    // right-multiplication. Using ``sideSign`` keeps the rotation
    // sense consistent per side.
    const handIdx = this.findHandIndex(vrmSideLabel);
    if (handIdx >= 0) {
      const hLm = this._handMirrored[handIdx];
      _handWristW.set(hLm[WRIST_LM].x, hLm[WRIST_LM].y, hLm[WRIST_LM].z);
      _handMidW.set(hLm[MIDDLE_MCP_LM].x, hLm[MIDDLE_MCP_LM].y, hLm[MIDDLE_MCP_LM].z);
      _handDir.copy(_handMidW).sub(_handWristW);
      fixCoord(_handDir);
      if (_handDir.lengthSq() > 1e-8) {
        _handDir.normalize();
        _lowerArmWorld.copy(_upperArmWorld).multiply(_bqB);
        _lowerArmWorldInv.copy(_lowerArmWorld).invert();
        _handDirLocal.copy(_handDir).applyQuaternion(_lowerArmWorldInv);
        // Bone axis is local ±X; twist is measured in the YZ plane
        // (perpendicular to the bone). atan2(z, y) gives the angle
        // from +Y toward +Z, i.e. palm rotating forward/backward.
        let twistAngle =
          Math.atan2(_handDirLocal.z, _handDirLocal.y) -
          this.forearmRollRestRad;
        while (twistAngle > Math.PI) twistAngle -= 2 * Math.PI;
        while (twistAngle < -Math.PI) twistAngle += 2 * Math.PI;
        twistAngle *= this.forearmRollGain * sideSign;
        if (this.forearmRollSignFlip) twistAngle = -twistAngle;
        if (twistAngle > FOREARM_ROLL_CLAMP_RAD) {
          twistAngle = FOREARM_ROLL_CLAMP_RAD;
        } else if (twistAngle < -FOREARM_ROLL_CLAMP_RAD) {
          twistAngle = -FOREARM_ROLL_CLAMP_RAD;
        }
        _twistQuat.setFromAxisAngle(_unitX, twistAngle);
        _bqB.multiply(_twistQuat);
      }
    }

    this.writeBoneSmoothed(
      lowerBone,
      [_bqB.x, _bqB.y, _bqB.z, _bqB.w],
      tsSec,
      out,
    );

    // Hand bone rotation is owned by ``solveHands`` (palm frame). We
    // intentionally don't write it here — would fight with finger
    // solver output.
  }

  private solveLegChain(
    upperBone: MocapBone,
    lowerBone: MocapBone,
    footBone: MocapBone,
    lmHip: number,
    lmKnee: number,
    lmAnkle: number,
    lmFoot: number,
    lm: MutableLandmark[],
    tsSec: number,
    out: ClipSample,
  ): void {
    const hip = lm[lmHip];
    const kn = lm[lmKnee];
    const an = lm[lmAnkle];
    const ft = lm[lmFoot];
    if (hip.visibility < VIS_GATE || kn.visibility < VIS_GATE) {
      this.clearBones(out, [upperBone, lowerBone, footBone]);
      return;
    }

    // Hip → knee.
    _legA.set(kn.x - hip.x, kn.y - hip.y, kn.z - hip.z);
    fixCoord(_legA);
    if (_legA.lengthSq() < 1e-8) {
      this.clearBones(out, [upperBone, lowerBone, footBone]);
      return;
    }
    _legA.normalize();
    _parentInv.copy(_yawQuat).invert();
    _obsLocal.copy(_legA).applyQuaternion(_parentInv);
    _restDir.set(0, -1, 0);
    _bqA.setFromUnitVectors(_restDir, _obsLocal);
    this.writeBoneSmoothed(
      upperBone,
      [_bqA.x, _bqA.y, _bqA.z, _bqA.w],
      tsSec,
      out,
    );

    _upperArmWorld.copy(_yawQuat).multiply(_bqA);

    if (an.visibility < VIS_GATE) {
      this.clearBones(out, [lowerBone, footBone]);
      return;
    }
    _legB.set(an.x - kn.x, an.y - kn.y, an.z - kn.z);
    fixCoord(_legB);
    if (_legB.lengthSq() < 1e-8) {
      this.clearBones(out, [lowerBone, footBone]);
      return;
    }
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

    if (ft.visibility < VIS_GATE) {
      this.clearBones(out, [footBone]);
      return;
    }
    _legC.set(ft.x - an.x, ft.y - an.y, ft.z - an.z);
    fixCoord(_legC);
    if (_legC.lengthSq() < 1e-8) {
      this.clearBones(out, [footBone]);
      return;
    }
    _legC.normalize();
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

  private findHandIndex(vrmSide: "Left" | "Right"): number {
    for (let i = 0; i < this._handMirroredCount; i++) {
      if (this._handVrmSide[i] === vrmSide) return i;
    }
    return -1;
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

  private clearBones(out: ClipSample, names: readonly MocapBone[]): void {
    for (const n of names) delete out.bones[n];
  }

  // ── Hands + fingers ───────────────────────────────────────────────
  private solveHands(tsSec: number, out: ClipSample): void {
    this.latestHandCount = 0;
    this.latestHandSides.left = false;
    this.latestHandSides.right = false;

    const wantLeft = this.findHandIndex("Left") >= 0;
    const wantRight = this.findHandIndex("Right") >= 0;

    // Purge finger bones for sides missing this frame — apply layer
    // decays them back to baseline.
    if (!wantLeft) {
      for (const [boneName] of LEFT_JOINTS) delete out.bones[boneName];
    }
    if (!wantRight) {
      for (const [boneName] of RIGHT_JOINTS) delete out.bones[boneName];
    }

    for (let i = 0; i < this._handMirroredCount; i++) {
      const side = this._handVrmSide[i];
      if (!side) continue;
      this.latestHandCount++;
      if (side === "Left") this.latestHandSides.left = true;
      else this.latestHandSides.right = true;
      this.solveOneHand(this._handMirrored[i], side === "Left", tsSec, out);
    }
  }

  private solveOneHand(
    lm: MutableHandLandmark[],
    vrmIsLeft: boolean,
    tsSec: number,
    out: ClipSample,
  ): void {
    _hWrist.set(lm[WRIST_LM].x, lm[WRIST_LM].y, lm[WRIST_LM].z);
    _hMid.set(lm[MIDDLE_MCP_LM].x, lm[MIDDLE_MCP_LM].y, lm[MIDDLE_MCP_LM].z);

    _palmY.copy(_hMid).sub(_hWrist);
    if (_palmY.lengthSq() < 1e-10) return;
    _palmY.normalize();

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
    const curlSign = vrmIsLeft ? 1 : -1;

    for (const [boneName, parent, childFrom, childTo, jointType] of joints) {
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

      _hChildA.set(lm[childFrom].x, lm[childFrom].y, lm[childFrom].z);
      _hChildB.set(lm[childTo].x, lm[childTo].y, lm[childTo].z);
      _childDir.copy(_hChildB).sub(_hChildA);
      const minChildLenSq = jointType === "thumbDistal" ? 1e-4 : 1e-10;
      if (_childDir.lengthSq() < minChildLenSq) continue;
      _childDir.normalize();

      const cos = Math.max(-1, Math.min(1, parentVec.dot(_childDir)));
      const curl = Math.acos(cos);
      this.latestFingerMaxCurl = Math.max(this.latestFingerMaxCurl, curl);

      const cal = this.effectiveCalibration[jointType];
      const rawCurl = cal.invertRaw ? Math.PI / 2 - curl : curl;
      const span = cal.fistRad - cal.restRad;
      const norm =
        span > 1e-6 ? Math.max(0, Math.min(1, (rawCurl - cal.restRad) / span)) : 0;
      const sign = (cal.flipSign ? -1 : 1) * curlSign;
      const boneRot = norm * cal.outRangeRad * sign;

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

      const restSplay = SPREAD_REST_RAD[boneName];
      if (restSplay !== undefined && _palmFrameValid) {
        const dirX = _childDir.dot(_palmX);
        const dirY = _childDir.dot(_palmY);
        const splayRaw = Math.atan2(dirX, dirY);
        let delta = splayRaw - restSplay;
        if (delta > PROXIMAL_SPREAD.clampRad) delta = PROXIMAL_SPREAD.clampRad;
        else if (delta < -PROXIMAL_SPREAD.clampRad) delta = -PROXIMAL_SPREAD.clampRad;
        const normSplay = delta / PROXIMAL_SPREAD.clampRad;
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

  // ── Diagnostics ──────────────────────────────────────────────────
  latestHandCount = 0;
  readonly latestHandSides = { left: false, right: false };
  latestFingerMaxCurl = 0;

  readonly latestArmDiag = {
    left:  { jumpDeg: 0, rmsDeg: 0, ok: false },
    right: { jumpDeg: 0, rmsDeg: 0, ok: false },
  };
  private readonly _armDiagPrev = {
    left:  { x: 0, y: 0, z: 0, valid: false },
    right: { x: 0, y: 0, z: 0, valid: false },
  };
  private readonly _armDiagRing = {
    left:  new Float32Array(30),
    right: new Float32Array(30),
  };
  private readonly _armDiagRingHead = { left: 0, right: 0 };
  private readonly _armDiagRingSize = { left: 0, right: 0 };

  private updateArmDiag(
    side: "left" | "right",
    dx: number,
    dy: number,
    dz: number,
  ): void {
    const prev = this._armDiagPrev[side];
    const out = this.latestArmDiag[side];
    if (prev.valid) {
      const dot = prev.x * dx + prev.y * dy + prev.z * dz;
      const cx = prev.y * dz - prev.z * dy;
      const cy = prev.z * dx - prev.x * dz;
      const cz = prev.x * dy - prev.y * dx;
      const crossMag = Math.sqrt(cx * cx + cy * cy + cz * cz);
      const angleDeg = (Math.atan2(crossMag, dot) * 180) / Math.PI;
      out.jumpDeg = angleDeg;
      const ring = this._armDiagRing[side];
      ring[this._armDiagRingHead[side]] = angleDeg;
      this._armDiagRingHead[side] = (this._armDiagRingHead[side] + 1) % ring.length;
      if (this._armDiagRingSize[side] < ring.length) this._armDiagRingSize[side]++;
      let sumSq = 0;
      const n = this._armDiagRingSize[side];
      for (let i = 0; i < n; i++) sumSq += ring[i] * ring[i];
      out.rmsDeg = Math.sqrt(sumSq / n);
    } else {
      out.jumpDeg = 0;
    }
    out.ok = true;
    prev.x = dx;
    prev.y = dy;
    prev.z = dz;
    prev.valid = true;
  }

  private resetArmDiag(side: "left" | "right"): void {
    this._armDiagPrev[side].valid = false;
    this.latestArmDiag[side].ok = false;
  }
}

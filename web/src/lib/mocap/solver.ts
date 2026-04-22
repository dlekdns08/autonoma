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
const WRIST_LM = 0;
const INDEX_MCP_LM = 5;
const INDEX_PIP_LM = 6;
const MIDDLE_MCP_LM = 9;
const MIDDLE_PIP_LM = 10;
const RING_MCP_LM = 13;
const RING_PIP_LM = 14;
const PINKY_MCP_LM = 17;
const PINKY_PIP_LM = 18;
// Thumb landmarks CMC(1) → MCP(2) → IP(3) → TIP(4) are intentionally
// NOT used — see the note in ``LEFT_FINGERS`` below.

/** (boneName, start-landmark, end-landmark). Each pair spans the bone
 *  whose rotation we're computing — start is the bone's base joint and
 *  end is where the bone points toward at rest.
 *
 *  Thumbs are omitted: our curl metric (angle between MCP→PIP and
 *  wrist→middle-MCP) assumes the finger's rest direction is palm-
 *  forward. That's true for index/middle/ring/little but not the
 *  thumb, which pokes out orthogonal to the palm at rest. Without a
 *  thumb-specific rest reference the thumb would read as ~60°-curled
 *  even in a relaxed hand. We'll add thumbs back with their own
 *  reference once the main four fingers are validated visually. */
type FingerBone = readonly [MocapBone, number, number];
const LEFT_FINGERS: readonly FingerBone[] = [
  ["leftIndexProximal", INDEX_MCP_LM, INDEX_PIP_LM],
  ["leftMiddleProximal", MIDDLE_MCP_LM, MIDDLE_PIP_LM],
  ["leftRingProximal", RING_MCP_LM, RING_PIP_LM],
  ["leftLittleProximal", PINKY_MCP_LM, PINKY_PIP_LM],
];
const RIGHT_FINGERS: readonly FingerBone[] = [
  ["rightIndexProximal", INDEX_MCP_LM, INDEX_PIP_LM],
  ["rightMiddleProximal", MIDDLE_MCP_LM, MIDDLE_PIP_LM],
  ["rightRingProximal", RING_MCP_LM, RING_PIP_LM],
  ["rightLittleProximal", PINKY_MCP_LM, PINKY_PIP_LM],
];

// Pre-allocated scratch vectors / quaternions used by ``solveHands``.
// Keeping them module-scoped means the per-frame solve does zero
// allocations in steady state.
const _hWrist = new THREE.Vector3();
const _hMid = new THREE.Vector3();
const _hMcp = new THREE.Vector3();
const _hPip = new THREE.Vector3();
const _hDir = new THREE.Vector3();
const _palmY = new THREE.Vector3();
const _curlEuler = new THREE.Euler();
const _handQ = new THREE.Quaternion();

// Curl range calibration. Empirically ``Math.acos(dot(MCP→PIP,
// wrist→middleMCP))`` doesn't map 0°→straight / 90°→fist because:
//   - At an open "rest" hand the MAX finger splays ~50° from palm-
//     forward (pinky naturally abducts; fingers aren't perfectly
//     parallel to the palm midline).
//   - At a full fist the max tops out around 90° (MediaPipe's 2D
//     dominance compresses the 3D curl into image-plane angles).
// Without remapping, the output bone rotation at rest is already a
// visible 50° and the full-fist delta is only 40° — looks subtle.
// We normalise [REST..FIST] → [0..OUT_RANGE] so open hand reads as
// identity and fist reads as a clean 90° bone rotation.
const CURL_REST_RAD = (50 * Math.PI) / 180;
const CURL_FIST_RAD = (90 * Math.PI) / 180;
const CURL_OUT_RANGE_RAD = (90 * Math.PI) / 180;

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

  /** Curl-only finger solver. Computes one scalar — the angle between
   *  the finger's MCP→PIP segment and the palm-forward axis
   *  (wrist→middle-MCP) — then writes that as a positive rotation
   *  around the VRM bone's local Z axis.
   *
   *  Why curl-only instead of full 3D rotation:
   *  - The palm-local X/Z axes we derived from landmarks don't reliably
   *    line up with the VRM rig's hand-bone local frame (different
   *    exporters, different resting palm orientation). Full 3D
   *    ``setFromUnitVectors`` in the wrong frame silently produces zero
   *    visible motion because the rotation vanishes when projected onto
   *    the wrong axes.
   *  - Curl angle is axis-independent — it's a scalar derived from a
   *    dot product, so it's robust to every frame convention.
   *
   *  Why +Z specifically (validated with the finger-axis test harness
   *  in ``MocapPreview`` on a VRoid-exported rig):
   *  - Local +X points along the finger (rest direction toward tip), so
   *    rotation around X is an invisible twist on cylindrical geometry.
   *  - Local Y is the abduction/adduction axis — rotation around Y
   *    swings the finger sideways (pinky-ward for the left hand),
   *    which is NOT flexion.
   *  - Local Z is the flexion/extension axis; +Z brings the finger tip
   *    from forward (+X) toward the palm-ward direction (+Y-ish),
   *    which is anatomical curl.
   *
   *  Chirality: we expected three-vrm's normalized humanoid to mirror
   *  the finger bones' local axes per hand so both sides could use the
   *  same sign, but empirically (Midori rig) left and right hands
   *  share the same Z direction — +Z flexes the left hand but
   *  hyperextends the right. We negate for the right hand to compensate.
   *
   *  Trade-off: fingers can't spread apart (abduction) — only flex.
   *  That's fine for the headline "open hand / closed fist / point"
   *  vocabulary; we can layer in spread once curl is visually correct.
   */
  private solveOneHand(
    lm: { x: number; y: number; z: number }[],
    vrmIsLeft: boolean,
    tsSec: number,
    out: ClipSample,
  ): void {
    _hWrist.set(lm[WRIST_LM].x, lm[WRIST_LM].y, lm[WRIST_LM].z);
    _hMid.set(lm[MIDDLE_MCP_LM].x, lm[MIDDLE_MCP_LM].y, lm[MIDDLE_MCP_LM].z);

    // Palm forward direction (wrist → middle MCP). This is what we
    // measure each finger's curl against.
    _palmY.copy(_hMid).sub(_hWrist);
    if (_palmY.lengthSq() < 1e-10) return;
    _palmY.normalize();

    const fingers = vrmIsLeft ? LEFT_FINGERS : RIGHT_FINGERS;
    // See docstring "Chirality" section — right hand rotates the opposite
    // way around the local Z axis on this rig family.
    const curlSign = vrmIsLeft ? 1 : -1;
    for (const [boneName, baseIdx, tipIdx] of fingers) {
      _hMcp.set(lm[baseIdx].x, lm[baseIdx].y, lm[baseIdx].z);
      _hPip.set(lm[tipIdx].x, lm[tipIdx].y, lm[tipIdx].z);
      _hDir.copy(_hPip).sub(_hMcp);
      if (_hDir.lengthSq() < 1e-10) continue;
      _hDir.normalize();

      // Cosine of angle between finger direction and palm-forward.
      // Raw curl ranges empirically ~50° (open rest) → ~90° (full
      // fist); we normalise that span to a visible 0° → 90° bone
      // rotation. See ``CURL_REST_RAD`` comment.
      const cos = Math.max(-1, Math.min(1, _hDir.dot(_palmY)));
      const curl = Math.acos(cos);
      this.latestFingerMaxCurl = Math.max(this.latestFingerMaxCurl, curl);

      const norm = Math.max(
        0,
        Math.min(1, (curl - CURL_REST_RAD) / (CURL_FIST_RAD - CURL_REST_RAD)),
      );
      const boneRot = norm * CURL_OUT_RANGE_RAD * curlSign;

      // Rotation around local Z = flexion/extension axis. The sign is
      // applied per-hand above (``curlSign``) because left and right
      // hands on this rig share the same axis direction rather than
      // being mirrored.
      _curlEuler.set(0, 0, boneRot, "XYZ");
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

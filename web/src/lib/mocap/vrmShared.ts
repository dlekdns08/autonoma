/**
 * Helpers shared between the VRMCharacter playback path and the mocap
 * recorder preview. Keeps the three-vrm surface area in one place so the
 * recorder's output can be applied to exactly the same bones the
 * dashboard renderer reads.
 *
 * Nothing in this module renders — it's pure logic over a ``VRM`` handle.
 */

import type { VRM } from "@pixiv/three-vrm";
import type { Object3D } from "three";
import * as THREE from "three";
import { MOCAP_BONES, type MocapBone, type MocapExpression } from "./clipFormat";
import type { ClipSample } from "./clipPlayer";

// ── Rest-pose tuning for mocap preview ──────────────────────────────
//
// Three-vrm's normalized humanoid rests at T-pose (identity localQuat
// = arms extended horizontally). That's awkward as a fallback pose for
// mocap: when the user's arms aren't in frame, the VRM drops back to
// T-pose which reads as "the character is doing a crucifixion".
//
// Fix: pre-rotate the upperArm bones to a natural arms-at-side pose
// AT VRM LOAD TIME (before ``collectMocapBones`` captures the baseline).
// Then the baseline == arms-down, and the decay-apply path slerps the
// VRM back to arms-down whenever the solver drops a bone from ``out``.
//
// Math: ``rightUpperArm`` at rest points along parent's -X (character's
// anatomical right). Rotating 90° around +Z takes (-X, 0, 0) to
// (0, -Y, 0) — arm hanging. ``leftUpperArm`` rests at +X; rotating -90°
// around +Z (equivalent to +90° around -Z) takes it to the same -Y.
// Quaternion for axis-angle(±Z, 90°) = (0, 0, ±sin(π/4), cos(π/4)).
const _R2D2 = Math.SQRT1_2;
const ARMS_DOWN_LEFT_QUAT  = new THREE.Quaternion(0, 0, -_R2D2, _R2D2);
const ARMS_DOWN_RIGHT_QUAT = new THREE.Quaternion(0, 0,  _R2D2, _R2D2);

/** Pre-rotate ``vrm``'s upperArm bones into an arms-hanging-at-side
 *  pose. Call BEFORE ``collectMocapBones`` so the captured baseline
 *  matches the adjusted pose — the decay-apply path below will slerp
 *  back toward exactly these quaternions whenever a bone leaves the
 *  mocap output.
 *
 *  No-op on rigs that don't expose the normalized upperArm bones
 *  (shouldn't happen for any humanoid VRM, but we're defensive). */
export function adjustVrmRestToArmsDown(vrm: VRM): void {
  const h = vrm.humanoid;
  if (!h) return;
  const left = h.getNormalizedBoneNode("leftUpperArm");
  const right = h.getNormalizedBoneNode("rightUpperArm");
  if (left) left.quaternion.copy(ARMS_DOWN_LEFT_QUAT);
  if (right) right.quaternion.copy(ARMS_DOWN_RIGHT_QUAT);
}

// ── Finger-bone fallback (scene traversal) ───────────────────────────
// Many VRM exports (VRoid especially) include finger mesh bones but
// don't register them in the VRM 1.0 humanoid map — finger bones are
// optional in the spec. We still want to drive them from mocap, so we
// search the raw three.js scene for the standard naming patterns.
//
// The VRoid export convention is ``J_Bip_{L|R}_{Finger}{N}`` where N is
// 1-based along the chain from the palm. Thumb has one extra joint
// (``Thumb1`` = metacarpal, ``Thumb2`` = proximal, ``Thumb3`` = distal)
// so thumbProximal maps to ``...Thumb2`` — not ``...Thumb1``.
//
// We include a few other common naming conventions (Mixamo-style,
// Blender-default) so imports from those pipelines also resolve.
type FingerFallback = {
  side: "L" | "R";
  finger: "Thumb" | "Index" | "Middle" | "Ring" | "Little";
  /** VRoid numeric suffix for the *proximal* joint — thumb offsets by 1. */
  proximalIndex: 2 | 1;
};
const FINGER_FALLBACKS: Partial<Record<MocapBone, FingerFallback>> = {
  leftThumbProximal: { side: "L", finger: "Thumb", proximalIndex: 2 },
  leftIndexProximal: { side: "L", finger: "Index", proximalIndex: 1 },
  leftMiddleProximal: { side: "L", finger: "Middle", proximalIndex: 1 },
  leftRingProximal: { side: "L", finger: "Ring", proximalIndex: 1 },
  leftLittleProximal: { side: "L", finger: "Little", proximalIndex: 1 },
  rightThumbProximal: { side: "R", finger: "Thumb", proximalIndex: 2 },
  rightIndexProximal: { side: "R", finger: "Index", proximalIndex: 1 },
  rightMiddleProximal: { side: "R", finger: "Middle", proximalIndex: 1 },
  rightRingProximal: { side: "R", finger: "Ring", proximalIndex: 1 },
  rightLittleProximal: { side: "R", finger: "Little", proximalIndex: 1 },
};

function candidateNames(fb: FingerFallback): string[] {
  const side = fb.side;
  const long = side === "L" ? "Left" : "Right";
  const finger = fb.finger;
  const n = fb.proximalIndex;
  // "Pinky" is used by some rigs where VRM says "Little"; include both.
  const fingers =
    finger === "Little" ? [finger, "Pinky"] : [finger];
  const out: string[] = [];
  for (const f of fingers) {
    out.push(
      `J_Bip_${side}_${f}${n}`, // VRoid
      `${long}Hand${f}${n}`, // Mixamo
      `${f}${n}_${side}`, // Blender-default
      `${long}_${f}_${n}`, // snake variant
      `${long}${f}${n}`, // concatenated
    );
  }
  return out;
}

function findSceneBone(root: Object3D, candidates: string[]): Object3D | null {
  // Lower-case set lookup — some exporters mangle casing in re-rigs.
  const want = new Set(candidates.map((n) => n.toLowerCase()));
  let found: Object3D | null = null;
  root.traverse((obj) => {
    if (found) return;
    if (want.has(obj.name.toLowerCase())) found = obj;
  });
  return found;
}

/** Resolved bone nodes for every bone in ``MOCAP_BONES``. Values are
 *  ``null`` when the VRM rig omits the bone — the playback path should
 *  treat ``null`` bones as "no-op" rather than crashing. */
export type MocapBoneMap = Partial<Record<MocapBone, Object3D | null>>;

/** Resolve every tracked bone. Cheap enough to call once after VRM load;
 *  the playback frame loop should reuse the returned map.
 *
 *  Finger-proximal bones fall through to scene traversal if the VRM
 *  humanoid map doesn't expose them (common in VRoid exports). When a
 *  raw scene bone is returned instead of a humanoid-normalized bone,
 *  its resting quaternion is ALSO captured in ``userData.mocapRest`` so
 *  the playback path can compose the mocap rotation on top of rest
 *  instead of overwriting it (raw bones aren't identity-rest).
 *
 *  In addition, every resolved bone gets its CURRENT quaternion cloned
 *  into ``userData.mocapBaseline``. This is distinct from ``mocapRest``
 *  (which only exists on finger scene-fallback bones and affects the
 *  playback compose path in ``applyBoneSample``). ``mocapBaseline`` is
 *  captured for future phases — a Phase A.5 idle↔mocap crossfade will
 *  read it to blend live IK samples against the idle/rest pose when
 *  landmarks drop out. The current playback path does NOT read it, so
 *  adding this field is behaviour-neutral. If callers adjust bone
 *  rotations AFTER ``collectMocapBones`` returns (e.g. dropping T-pose
 *  arms to a hanging silhouette), they should call
 *  ``recaptureMocapBaseline`` to refresh the stored values. */
export function collectMocapBones(vrm: VRM): MocapBoneMap {
  const h = vrm.humanoid;
  const out: MocapBoneMap = {};
  if (!h) return out;
  for (const name of MOCAP_BONES) {
    const humanoid = h.getNormalizedBoneNode(name);
    if (humanoid) {
      (humanoid.userData ??= {}).mocapBaseline = humanoid.quaternion.clone();
      out[name] = humanoid;
      continue;
    }
    // Fallback: look in the raw scene for a finger bone matching common
    // export naming conventions.
    const fb = FINGER_FALLBACKS[name];
    if (!fb) {
      out[name] = null;
      continue;
    }
    const scene = findSceneBone(vrm.scene, candidateNames(fb));
    if (scene) {
      // Stash the rest pose so the apply path can compose with it.
      (scene.userData ??= {}).mocapRest = scene.quaternion.clone();
      scene.userData.mocapBaseline = scene.quaternion.clone();
    }
    out[name] = scene;
  }
  return out;
}

/** Re-capture the baseline quaternion for every resolved bone. Use this
 *  when bone rotations are adjusted AFTER ``collectMocapBones`` (e.g.
 *  the T-pose-to-hanging-arms adjustment in ``VRMCharacter``) so the
 *  stored ``userData.mocapBaseline`` reflects the final rest pose
 *  rather than the pre-adjustment identity state. Does not touch
 *  ``userData.mocapRest`` — that field drives the finger scene-fallback
 *  compose path and must stay pinned to the original rest. */
export function recaptureMocapBaseline(map: MocapBoneMap): void {
  for (const bone of Object.values(map)) {
    if (bone) {
      (bone.userData ??= {}).mocapBaseline = bone.quaternion.clone();
    }
  }
}

/** Count how many bones in the map are non-null. Useful as a one-line
 *  diagnostic when finger mocap "seems broken" — if the rig omits
 *  finger bones there's nothing for the clip to drive. Separates bones
 *  resolved via the VRM humanoid map from those that fell back to a
 *  raw scene node (``userData.mocapRest`` is only set on the latter). */
export function countResolvedBones(map: MocapBoneMap): {
  resolved: number;
  total: number;
  missing: MocapBone[];
  fallbacks: MocapBone[];
} {
  let resolved = 0;
  const missing: MocapBone[] = [];
  const fallbacks: MocapBone[] = [];
  for (const name of MOCAP_BONES) {
    const bone = map[name];
    if (bone) {
      resolved++;
      if (bone.userData?.mocapRest) fallbacks.push(name);
    } else {
      missing.push(name);
    }
  }
  return { resolved, total: MOCAP_BONES.length, missing, fallbacks };
}

/** Scratch quaternion reused by ``applyBoneSample`` so writing a full
 *  clip sample stays allocation-free. */
const _q = new THREE.Quaternion();

/** Apply one ``ClipSample`` entry to a bone. Does nothing when the bone
 *  is absent from the rig or the sample doesn't cover it. If the bone
 *  has a stashed ``userData.mocapRest`` (raw scene bone found via the
 *  finger fallback), we compose the sample rotation on top of rest
 *  instead of overwriting — raw bones aren't at identity in rest pose.
 */
export function applyBoneSample(
  map: MocapBoneMap,
  name: MocapBone,
  sample: ClipSample,
): void {
  const bone = map[name];
  if (!bone) return;
  const q = sample.bones[name];
  if (!q) return;
  _q.set(q[0], q[1], q[2], q[3]);
  const rest = bone.userData?.mocapRest as THREE.Quaternion | undefined;
  if (rest) {
    bone.quaternion.copy(rest).multiply(_q);
  } else {
    bone.quaternion.copy(_q);
  }
}

/** Write every covered bone from a ``ClipSample`` onto the rig. If
 *  ``skipBones`` is provided, bones in that set are left untouched —
 *  used by the preview path to suppress finger tracks on cross-rig
 *  playback (the source VRM's finger curl axis may not match the
 *  preview rig's). */
export function applyBoneSampleAll(
  map: MocapBoneMap,
  sample: ClipSample,
  skipBones?: ReadonlySet<MocapBone>,
): void {
  for (const name of Object.keys(sample.bones) as MocapBone[]) {
    if (skipBones && skipBones.has(name)) continue;
    applyBoneSample(map, name, sample);
  }
}

/** Live-preview variant of ``applyBoneSampleAll`` that handles the
 *  "bone missing from the sample this frame" case gracefully.
 *
 *  For every ``MOCAP_BONES`` entry the rig actually exposes:
 *    - If ``sample.bones[name]`` exists → apply it (same semantics as
 *      ``applyBoneSample``: humanoid bones get copied directly; scene-
 *      fallback bones compose with ``mocapRest``).
 *    - Otherwise → slerp the bone's current quaternion toward
 *      ``mocapBaseline`` at the given per-frame ``decayAlpha``.
 *
 *  Why this exists: the solver drops bones from ``out`` whenever the
 *  source landmarks fail visibility (classic "arms below the desk"
 *  case). Without the decay path the three-vrm bone would sit frozen
 *  at whatever the last frame wrote, producing the "VRM stuck in
 *  raised-arms hallucination" bug we kept chasing in the solver layer.
 *  Handling it here, DIRECTLY on the VRM quaternion, is immune to
 *  whether the solver's sample path is working — no indirect
 *  sample → OneEuro → apply chain to break.
 *
 *  ``decayAlpha`` is a nlerp factor per call. 0.15 at 60fps → bone
 *  reaches ~95% of baseline in ~20 frames (~0.3s). Raise for a
 *  snappier return, lower for a lazier settle.
 *
 *  ``skipBones`` semantics match ``applyBoneSampleAll`` — listed bones
 *  are neither applied nor decayed (the cross-rig finger suppression
 *  case leaves fingers entirely to the rig's own rest).
 *
 *  The finger-scene-fallback bones keep using ``applyBoneSample``'s
 *  compose-with-mocapRest path, so their baseline includes the per-rig
 *  finger rest offset. Baseline for humanoid bones is whatever
 *  ``collectMocapBones`` captured — meaning the caller must have run
 *  ``adjustVrmRestToArmsDown`` BEFORE collecting bones if arms-down is
 *  the desired fallback (otherwise baseline == T-pose identity). */
const _decayCurrent = new THREE.Quaternion();
export function applyBoneSampleAllWithDecay(
  map: MocapBoneMap,
  sample: ClipSample,
  decayAlpha: number,
  skipBones?: ReadonlySet<MocapBone>,
): void {
  const clampedAlpha = Math.max(0, Math.min(1, decayAlpha));
  for (const name of MOCAP_BONES) {
    if (skipBones && skipBones.has(name)) continue;
    const bone = map[name];
    if (!bone) continue;
    if (sample.bones[name]) {
      applyBoneSample(map, name, sample);
      continue;
    }
    const baseline = bone.userData?.mocapBaseline as
      | THREE.Quaternion
      | undefined;
    if (!baseline) continue;
    // nlerp (lerp + normalise). Cheaper than slerp and visually
    // indistinguishable at these per-frame step sizes.
    _decayCurrent.copy(bone.quaternion);
    // Guard the double-cover: pick the short-arc hemisphere before
    // lerping or the bone will slerp the long way around in quaternion
    // space (visible as a flip).
    const dot =
      _decayCurrent.x * baseline.x +
      _decayCurrent.y * baseline.y +
      _decayCurrent.z * baseline.z +
      _decayCurrent.w * baseline.w;
    const sign = dot < 0 ? -1 : 1;
    bone.quaternion.set(
      (1 - clampedAlpha) * _decayCurrent.x + clampedAlpha * sign * baseline.x,
      (1 - clampedAlpha) * _decayCurrent.y + clampedAlpha * sign * baseline.y,
      (1 - clampedAlpha) * _decayCurrent.z + clampedAlpha * sign * baseline.z,
      (1 - clampedAlpha) * _decayCurrent.w + clampedAlpha * sign * baseline.w,
    );
    bone.quaternion.normalize();
  }
}

/** Write every covered expression from a ``ClipSample`` onto the rig.
 *  Expressions not covered by the sample are left alone so the caller's
 *  other drivers (idle blink, amplitude lip-sync, mood) keep working. */
export function applyExpressionSample(vrm: VRM, sample: ClipSample): void {
  const em = vrm.expressionManager;
  if (!em) return;
  for (const [name, value] of Object.entries(sample.expressions) as [
    MocapExpression,
    number,
  ][]) {
    if (em.getExpression?.(name)) em.setValue(name, value);
  }
}

/** Read a bone's current quaternion into a 4-tuple. Used by the recorder
 *  after the MediaPipe landmark-IK solver has written the live frame into the
 *  VRM — we snapshot the resulting quaternion instead of re-deriving it
 *  from the landmark math, which keeps any per-rig compensation the
 *  solver applied. */
export function snapshotBoneQuat(
  bone: Object3D | null,
  out: [number, number, number, number],
): void {
  if (!bone) {
    out[0] = 0;
    out[1] = 0;
    out[2] = 0;
    out[3] = 1;
    return;
  }
  const q = bone.quaternion;
  out[0] = q.x;
  out[1] = q.y;
  out[2] = q.z;
  out[3] = q.w;
}

/** Read an expression's current scalar. ``0`` when the rig lacks it. */
export function snapshotExpression(
  vrm: VRM,
  name: MocapExpression,
): number {
  const em = vrm.expressionManager;
  if (!em) return 0;
  if (!em.getExpression?.(name)) return 0;
  return em.getValue?.(name) ?? 0;
}

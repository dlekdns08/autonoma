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
 *  instead of overwriting it (raw bones aren't identity-rest). */
export function collectMocapBones(vrm: VRM): MocapBoneMap {
  const h = vrm.humanoid;
  const out: MocapBoneMap = {};
  if (!h) return out;
  for (const name of MOCAP_BONES) {
    const humanoid = h.getNormalizedBoneNode(name);
    if (humanoid) {
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
    }
    out[name] = scene;
  }
  return out;
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

/** Write every covered bone from a ``ClipSample`` onto the rig. */
export function applyBoneSampleAll(
  map: MocapBoneMap,
  sample: ClipSample,
): void {
  for (const name of Object.keys(sample.bones) as MocapBone[]) {
    applyBoneSample(map, name, sample);
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
 *  after Kalidokit/MediaPipe solve has written the live frame into the
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

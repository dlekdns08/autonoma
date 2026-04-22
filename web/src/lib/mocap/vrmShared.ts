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

/** Resolved bone nodes for every bone in ``MOCAP_BONES``. Values are
 *  ``null`` when the VRM rig omits the bone — the playback path should
 *  treat ``null`` bones as "no-op" rather than crashing. */
export type MocapBoneMap = Partial<Record<MocapBone, Object3D | null>>;

/** Resolve every tracked bone. Cheap enough to call once after VRM load;
 *  the playback frame loop should reuse the returned map. */
export function collectMocapBones(vrm: VRM): MocapBoneMap {
  const h = vrm.humanoid;
  const out: MocapBoneMap = {};
  if (!h) return out;
  for (const name of MOCAP_BONES) {
    out[name] = h.getNormalizedBoneNode(name) ?? null;
  }
  return out;
}

/** Scratch quaternion reused by ``applyBoneSample`` so writing a full
 *  clip sample stays allocation-free. */
const _q = new THREE.Quaternion();

/** Apply one ``ClipSample`` entry to a bone. Does nothing when the bone
 *  is absent from the rig or the sample doesn't cover it. */
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
  bone.quaternion.copy(_q);
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

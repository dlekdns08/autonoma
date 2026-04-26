/**
 * Pose Editor IK — analytic two-bone IK for arms / legs, plus a small
 * "point this bone at that world position" helper used both by the IK
 * and by the bone-handle dragging path.
 *
 * Conventions:
 *   - The bone's local "to child" direction in PARENT-local space is
 *     ``child.position`` (three.js stores child offset in parent-local).
 *     Normalising and feeding it into ``setFromUnitVectors`` against the
 *     desired direction (also in parent-local) gives the rotation that
 *     points the bone correctly. We compose in WORLD space and convert
 *     back via ``localR = parentQWInv * R_world * parentQW`` so the
 *     existing parent twist survives — important when the operator has
 *     already rotated upstream bones (e.g. shoulder twist before the
 *     elbow IK runs).
 *
 * No allocations on the IK hot path are NOT a goal here — this fires
 * only on user drag, not every frame.
 */

import * as THREE from "three";

const _v1 = new THREE.Vector3();
const _v2 = new THREE.Vector3();
const _v3 = new THREE.Vector3();
const _qWorld = new THREE.Quaternion();
const _qParent = new THREE.Quaternion();

/** Rotate ``bone`` so that ``child`` (which must be a descendant of
 *  ``bone`` in the scene graph) lands as close as possible to
 *  ``targetWorld``. Preserves any existing twist of ``bone`` around the
 *  bone→child axis: we apply only the swing rotation needed to align
 *  the chain.
 *
 *  Caller must have updated ``bone.parent.matrixWorld`` already (the
 *  IK driver below does this between bones). */
export function rotateBoneToward(
  bone: THREE.Object3D,
  child: THREE.Object3D,
  targetWorld: THREE.Vector3,
): void {
  if (!bone.parent) return;

  bone.getWorldPosition(_v1);
  child.getWorldPosition(_v2);

  _v2.sub(_v1);
  if (_v2.lengthSq() < 1e-12) return;
  _v2.normalize();

  _v3.copy(targetWorld).sub(_v1);
  if (_v3.lengthSq() < 1e-12) return;
  _v3.normalize();

  const dot = _v2.dot(_v3);
  if (dot > 0.999999) return;

  _qWorld.setFromUnitVectors(_v2, _v3);

  bone.parent.getWorldQuaternion(_qParent);
  const parentInv = _qParent.clone().invert();
  const localDelta = parentInv.multiply(_qWorld).multiply(_qParent);
  bone.quaternion.premultiply(localDelta);
  bone.updateMatrixWorld(true);
}

export interface TwoBoneIKInput {
  /** Top of the chain (e.g. upperArm). */
  root: THREE.Object3D;
  /** Middle joint (e.g. lowerArm). Must be a child of root in the
   *  scene graph. */
  mid: THREE.Object3D;
  /** End effector (e.g. hand). Must be a child of mid. */
  end: THREE.Object3D;
  /** World-space target position the end effector should reach. */
  targetWorld: THREE.Vector3;
  /** Optional pole hint in world space — used to disambiguate the
   *  bending plane (which way the elbow points). When omitted, the
   *  current elbow position is used as the hint, which preserves the
   *  existing bend direction frame to frame. */
  poleHintWorld?: THREE.Vector3;
}

/** Solve a 2-bone analytic IK chain. Mutates ``root`` and ``mid``
 *  quaternions in place. ``end`` is left alone (its world transform
 *  follows from the parent rotations). */
export function solveTwoBoneIK(p: TwoBoneIKInput): void {
  if (!p.root.parent) return;

  // Refresh world matrices upstream so our "current" reads are accurate.
  p.root.parent.updateMatrixWorld(true);

  const sW = new THREE.Vector3();
  const eW = new THREE.Vector3();
  const wW = new THREE.Vector3();
  p.root.getWorldPosition(sW);
  p.mid.getWorldPosition(eW);
  p.end.getWorldPosition(wW);

  const L1 = eW.distanceTo(sW);
  const L2 = wW.distanceTo(eW);
  if (L1 < 1e-6 || L2 < 1e-6) return;

  // Distance from root to target, clamped into the reachable band so
  // the chain never tries to invert through itself.
  const minReach = Math.abs(L1 - L2) + 1e-4;
  const maxReach = L1 + L2 - 1e-4;
  const toTarget = new THREE.Vector3().copy(p.targetWorld).sub(sW);
  let d = toTarget.length();
  if (d < 1e-6) {
    // Target sits on the root pivot — nothing meaningful to solve.
    return;
  }
  d = Math.min(maxReach, Math.max(minReach, d));
  const chainAxis = toTarget.clone().normalize();

  // Pole direction = component of (hint - S) perpendicular to chainAxis.
  // Falls back to the current elbow if the caller didn't supply a hint;
  // that preserves the user's bend direction across drags.
  const hint = p.poleHintWorld ?? eW;
  const fromS = new THREE.Vector3().copy(hint).sub(sW);
  const along = chainAxis.dot(fromS);
  let poleDir = fromS.sub(chainAxis.clone().multiplyScalar(along));
  if (poleDir.lengthSq() < 1e-8) {
    // Degenerate (hint sits on the chain axis). Pick any perpendicular
    // — world up first, world right as a fallback.
    poleDir = new THREE.Vector3(0, 1, 0).sub(
      chainAxis.clone().multiplyScalar(chainAxis.y),
    );
    if (poleDir.lengthSq() < 1e-8) {
      poleDir = new THREE.Vector3(1, 0, 0).sub(
        chainAxis.clone().multiplyScalar(chainAxis.x),
      );
    }
  }
  poleDir.normalize();

  // Law of cosines — distance along chain to the elbow, and the
  // perpendicular offset along pole.
  const dAlong = (L1 * L1 - L2 * L2 + d * d) / (2 * d);
  const dPerpSq = L1 * L1 - dAlong * dAlong;
  const dPerp = dPerpSq > 0 ? Math.sqrt(dPerpSq) : 0;

  const eTarget = new THREE.Vector3()
    .copy(sW)
    .addScaledVector(chainAxis, dAlong)
    .addScaledVector(poleDir, dPerp);

  // Target world pos used for the end effector (clamped target — what
  // the chain can actually reach). Use the original target when within
  // reach; clamped one when beyond.
  const reachTarget =
    d === toTarget.length()
      ? p.targetWorld
      : new THREE.Vector3().copy(sW).addScaledVector(chainAxis, d);

  rotateBoneToward(p.root, p.mid, eTarget);
  rotateBoneToward(p.mid, p.end, reachTarget);
}

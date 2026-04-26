/**
 * Finger pose presets — fist, V-sign, thumbs-up, etc.
 *
 * Each preset is a per-joint curl ratio in [0, 1]:
 *   0 → joint at the rig's resting (extended) position
 *   1 → joint at full curl (matches the solver's "fist" rotation)
 *
 * Applying a preset writes a curl quaternion onto each finger bone
 * using the same axis / sign convention the live mocap solver uses
 * (mirrored from CALIBRATION in ``solver.ts``). Scene-fallback bones
 * (the VRoid finger bones that aren't in the VRM 1.0 humanoid map)
 * compose against ``userData.mocapRest``; humanoid-mapped bones get
 * the curl quaternion written directly — same rule as
 * ``applyBoneSample`` in ``vrmShared.ts``.
 *
 * If a preset omits a finger entirely, that finger is left untouched
 * (so the operator can stack presets — apply "fist" then a different
 * preset that only touches the index finger to get a "pointing fist"
 * variation).
 */

import * as THREE from "three";
import type { MocapBone } from "@/lib/mocap/clipFormat";
import type { MocapBoneMap } from "@/lib/mocap/vrmShared";

type JointType =
  | "proximal"
  | "intermediate"
  | "distal"
  | "thumbProximal"
  | "thumbDistal"
  | "thumbMetacarpal";

type RotationAxis = "x" | "y" | "z";

interface JointCalibration {
  /** Maximum rotation (radians) at curl=1. Mirrors ``outRangeRad``
   *  in the solver's CALIBRATION table — keep these in sync if the
   *  solver tuning changes. */
  outRangeRad: number;
  axis: RotationAxis;
  flipSign?: boolean;
}

// Same numbers as ``CALIBRATION`` in ``solver.ts`` (lines 306-347).
// Duplicated rather than imported because the solver keeps the table
// private and re-exporting it would force exposing the surrounding
// JointType / scratch state.
const CALIBRATION: Record<JointType, JointCalibration> = {
  proximal:        { outRangeRad: (90 * Math.PI) / 180, axis: "z" },
  intermediate:    { outRangeRad: (90 * Math.PI) / 180, axis: "z" },
  distal:          { outRangeRad: (70 * Math.PI) / 180, axis: "z" },
  thumbProximal:   { outRangeRad: (45 * Math.PI) / 180, axis: "x", flipSign: true },
  thumbDistal:     { outRangeRad: (55 * Math.PI) / 180, axis: "x", flipSign: true },
  thumbMetacarpal: { outRangeRad: (35 * Math.PI) / 180, axis: "y" },
};

/** Per-finger curl. Each value is in [0, 1] (clamped on apply).
 *  Omitting a key leaves that joint untouched. */
export interface FingerCurls {
  thumbMetacarpal?: number;
  thumbProximal?: number;
  thumbDistal?: number;
  indexProximal?: number;
  indexIntermediate?: number;
  indexDistal?: number;
  middleProximal?: number;
  middleIntermediate?: number;
  middleDistal?: number;
  ringProximal?: number;
  ringIntermediate?: number;
  ringDistal?: number;
  littleProximal?: number;
  littleIntermediate?: number;
  littleDistal?: number;
}

export type Hand = "L" | "R" | "both";

export interface FingerPreset {
  id: string;
  name: string;
  emoji?: string;
  curls: FingerCurls;
}

/** Shorthand factory — same curl on every joint of every finger. Used
 *  to build "fist" (1.0) and "open hand" (0.0) presets compactly. */
function uniformCurls(c: number): FingerCurls {
  return {
    thumbProximal: c, thumbDistal: c, thumbMetacarpal: c,
    indexProximal: c, indexIntermediate: c, indexDistal: c,
    middleProximal: c, middleIntermediate: c, middleDistal: c,
    ringProximal: c, ringIntermediate: c, ringDistal: c,
    littleProximal: c, littleIntermediate: c, littleDistal: c,
  };
}

/** Built-in preset library. Add new entries here; the panel renders
 *  whatever is in this array. */
export const FINGER_PRESETS: readonly FingerPreset[] = [
  {
    id: "open",
    name: "손 펴기",
    emoji: "🖐",
    curls: uniformCurls(0),
  },
  {
    id: "fist",
    name: "주먹",
    emoji: "✊",
    curls: { ...uniformCurls(1), thumbProximal: 0.7, thumbDistal: 0.7 },
  },
  {
    id: "v",
    name: "V자",
    emoji: "✌️",
    curls: {
      thumbProximal: 0.6, thumbDistal: 0.6, thumbMetacarpal: 0.4,
      indexProximal: 0,   indexIntermediate: 0,   indexDistal: 0,
      middleProximal: 0,  middleIntermediate: 0,  middleDistal: 0,
      ringProximal: 1,    ringIntermediate: 1,    ringDistal: 1,
      littleProximal: 1,  littleIntermediate: 1,  littleDistal: 1,
    },
  },
  {
    id: "thumbsup",
    name: "엄지척",
    emoji: "👍",
    curls: {
      thumbProximal: 0,   thumbDistal: 0,   thumbMetacarpal: 0,
      indexProximal: 1,   indexIntermediate: 1,   indexDistal: 1,
      middleProximal: 1,  middleIntermediate: 1,  middleDistal: 1,
      ringProximal: 1,    ringIntermediate: 1,    ringDistal: 1,
      littleProximal: 1,  littleIntermediate: 1,  littleDistal: 1,
    },
  },
  {
    id: "point",
    name: "검지 가리키기",
    emoji: "☝️",
    curls: {
      thumbProximal: 0.6, thumbDistal: 0.6, thumbMetacarpal: 0.5,
      indexProximal: 0,   indexIntermediate: 0,   indexDistal: 0,
      middleProximal: 1,  middleIntermediate: 1,  middleDistal: 1,
      ringProximal: 1,    ringIntermediate: 1,    ringDistal: 1,
      littleProximal: 1,  littleIntermediate: 1,  littleDistal: 1,
    },
  },
  {
    id: "ok",
    name: "OK",
    emoji: "👌",
    curls: {
      thumbProximal: 0.7, thumbDistal: 0.6, thumbMetacarpal: 0.6,
      indexProximal: 0.7, indexIntermediate: 0.7, indexDistal: 0.5,
      middleProximal: 0.1, middleIntermediate: 0, middleDistal: 0,
      ringProximal: 0.1,   ringIntermediate: 0,  ringDistal: 0,
      littleProximal: 0.1, littleIntermediate: 0, littleDistal: 0,
    },
  },
  {
    id: "rock",
    name: "락 🤘",
    emoji: "🤘",
    curls: {
      thumbProximal: 0.7, thumbDistal: 0.7, thumbMetacarpal: 0.5,
      indexProximal: 0,   indexIntermediate: 0,   indexDistal: 0,
      middleProximal: 1,  middleIntermediate: 1,  middleDistal: 1,
      ringProximal: 1,    ringIntermediate: 1,    ringDistal: 1,
      littleProximal: 0,  littleIntermediate: 0,  littleDistal: 0,
    },
  },
  {
    id: "relaxed",
    name: "자연스러운 손",
    emoji: "🤚",
    curls: {
      thumbProximal: 0.15, thumbDistal: 0.15, thumbMetacarpal: 0.1,
      indexProximal: 0.25, indexIntermediate: 0.25, indexDistal: 0.2,
      middleProximal: 0.3, middleIntermediate: 0.3, middleDistal: 0.25,
      ringProximal: 0.35,  ringIntermediate: 0.35, ringDistal: 0.3,
      littleProximal: 0.4, littleIntermediate: 0.4, littleDistal: 0.35,
    },
  },
];

/** Map a (side, finger-relative-key) pair into the actual ``MocapBone``
 *  name. Centralised so the apply loop doesn't repeat string concat
 *  for every joint. */
function boneName(side: "L" | "R", joint: keyof FingerCurls): MocapBone {
  const prefix = side === "L" ? "left" : "right";
  // joint is e.g. "indexProximal" → "leftIndexProximal"
  const cap = joint[0].toUpperCase() + joint.slice(1);
  return `${prefix}${cap}` as MocapBone;
}

/** Resolve a curl key to the joint type used in CALIBRATION. */
function jointTypeOf(key: keyof FingerCurls): JointType {
  if (key === "thumbProximal") return "thumbProximal";
  if (key === "thumbDistal") return "thumbDistal";
  if (key === "thumbMetacarpal") return "thumbMetacarpal";
  if (key.endsWith("Distal")) return "distal";
  if (key.endsWith("Intermediate")) return "intermediate";
  return "proximal";
}

const _euler = new THREE.Euler();
const _q = new THREE.Quaternion();

function applyOneSide(
  bones: MocapBoneMap,
  curls: FingerCurls,
  side: "L" | "R",
): void {
  // The solver mirrors finger curl direction across hands via
  // ``curlSign = vrmIsLeft ? 1 : -1`` (solver.ts line 1359). Match it.
  const handSign = side === "L" ? 1 : -1;

  for (const key of Object.keys(curls) as (keyof FingerCurls)[]) {
    const raw = curls[key];
    if (raw === undefined) continue;
    const norm = Math.max(0, Math.min(1, raw));

    const cal = CALIBRATION[jointTypeOf(key)];
    const sign = (cal.flipSign ? -1 : 1) * handSign;
    const boneRot = norm * cal.outRangeRad * sign;

    _euler.set(
      cal.axis === "x" ? boneRot : 0,
      cal.axis === "y" ? boneRot : 0,
      cal.axis === "z" ? boneRot : 0,
      "XYZ",
    );
    _q.setFromEuler(_euler);

    const bone = bones[boneName(side, key)];
    if (!bone) continue;

    // Same compose rule as ``applyBoneSample`` in vrmShared.ts:
    // scene-fallback bones (VRoid finger bones not in the humanoid map)
    // have ``mocapRest`` and need composition; humanoid bones don't.
    const rest = bone.userData?.mocapRest as THREE.Quaternion | undefined;
    if (rest) {
      bone.quaternion.copy(rest).multiply(_q);
    } else {
      bone.quaternion.copy(_q);
    }
  }
}

/** Apply a finger preset to one or both hands. */
export function applyFingerPreset(
  bones: MocapBoneMap,
  preset: FingerPreset,
  hand: Hand,
): void {
  if (hand === "L" || hand === "both") applyOneSide(bones, preset.curls, "L");
  if (hand === "R" || hand === "both") applyOneSide(bones, preset.curls, "R");
}

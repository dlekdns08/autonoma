/**
 * Per-VRM mocap calibration overrides. Read from
 * ``vrmCatalog.json`` and merged into the solver's base calibration
 * at hand-capture start. Each field is optional; unset → use the
 * solver default.
 *
 * Add a per-rig override when a VRM's normalized finger bones use
 * different local-axis conventions than Midori (the tuning baseline).
 * Symptom that usually drives a new override: a finger joint that
 * "twists" or moves sideways instead of flexing palm-ward.
 */
import catalogJson from "@/components/vtuber/vrmCatalog.json";

export type JointAxis = "x" | "y" | "z";

export interface JointOverride {
  axis?: JointAxis;
  flipSign?: boolean;
  restDeg?: number;
  fistDeg?: number;
  outDeg?: number;
}

export interface BodyIKOverrides {
  /** Multiplier applied to the shoulder-lift heuristic. 0 disables the
   *  shoulder bone write entirely (bone stays at rest). 1 = default. */
  shoulderLiftGain?: number;

  /** Spine chain distribution weights — must sum to 1.0. Override to
   *  shift bend toward a different vertebra. */
  spineWeights?: {
    spine: number;
    chest: number;
    upperChest: number;
  };

  /** Forearm-roll calibration. ``restRad`` is the palm-direction angle
   *  at which the VRM's forearm should be at identity twist (usually
   *  0 but some rigs need ±π/2). ``signFlip`` reverses the twist
   *  direction when the VRM's forearm local +Y axis runs opposite the
   *  expected convention. ``gain`` scales the applied rotation. */
  forearmRoll?: {
    restRad?: number;
    signFlip?: boolean;
    gain?: number;
  };
}

export interface VrmMocapOverrides {
  proximal?: JointOverride;
  intermediate?: JointOverride;
  distal?: JointOverride;
  thumbProximal?: JointOverride;
  thumbDistal?: JointOverride;
  thumbMetacarpal?: JointOverride;
  body?: BodyIKOverrides;
}

/** Parse the flat ``mocap.*`` keys from vrmCatalog into the nested
 *  per-joint-type shape the solver wants. Returns null when the VRM
 *  has no ``mocap`` section (solver uses its own defaults). */
export function loadVrmOverrides(
  vrmFile: string,
): VrmMocapOverrides | null {
  const catalog = catalogJson as Record<
    string,
    { mocap?: Record<string, unknown> }
  >;
  const entry = catalog[vrmFile];
  if (!entry?.mocap) return null;
  const raw = entry.mocap;
  const out: VrmMocapOverrides = {};
  const joints = [
    "proximal",
    "intermediate",
    "distal",
    "thumbProximal",
    "thumbDistal",
    "thumbMetacarpal",
  ] as const;
  for (const j of joints) {
    const joint: JointOverride = {};
    const axis = raw[`${j}Axis`];
    if (axis === "x" || axis === "y" || axis === "z") joint.axis = axis;
    const flip = raw[`${j}FlipSign`];
    if (typeof flip === "boolean") joint.flipSign = flip;
    for (const field of ["rest", "fist", "out"] as const) {
      const v = raw[`${j}${field[0].toUpperCase()}${field.slice(1)}Deg`];
      if (typeof v === "number" && isFinite(v)) {
        joint[`${field}Deg` as keyof JointOverride] = v as never;
      }
    }
    if (Object.keys(joint).length > 0) out[j] = joint;
  }

  // Body section — flat keys "body*" in the catalog for readability.
  const bodyOut: BodyIKOverrides = {};

  const shoulderLiftGain = raw["bodyShoulderLiftGain"];
  if (typeof shoulderLiftGain === "number" && isFinite(shoulderLiftGain)) {
    bodyOut.shoulderLiftGain = shoulderLiftGain;
  }

  const spineSpine = raw["bodySpineWeightSpine"];
  const spineChest = raw["bodySpineWeightChest"];
  const spineUpper = raw["bodySpineWeightUpperChest"];
  if (
    typeof spineSpine === "number" &&
    typeof spineChest === "number" &&
    typeof spineUpper === "number"
  ) {
    bodyOut.spineWeights = {
      spine: spineSpine,
      chest: spineChest,
      upperChest: spineUpper,
    };
  }

  const forearmRest = raw["bodyForearmRollRestDeg"];
  const forearmFlip = raw["bodyForearmRollFlipSign"];
  const forearmGain = raw["bodyForearmRollGain"];
  if (
    forearmRest !== undefined ||
    forearmFlip !== undefined ||
    forearmGain !== undefined
  ) {
    bodyOut.forearmRoll = {};
    if (typeof forearmRest === "number" && isFinite(forearmRest)) {
      bodyOut.forearmRoll.restRad = (forearmRest * Math.PI) / 180;
    }
    if (typeof forearmFlip === "boolean") {
      bodyOut.forearmRoll.signFlip = forearmFlip;
    }
    if (typeof forearmGain === "number" && isFinite(forearmGain)) {
      bodyOut.forearmRoll.gain = forearmGain;
    }
  }

  if (Object.keys(bodyOut).length > 0) out.body = bodyOut;

  return Object.keys(out).length > 0 ? out : null;
}

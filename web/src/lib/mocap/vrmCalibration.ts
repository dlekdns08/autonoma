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

export interface VrmMocapOverrides {
  proximal?: JointOverride;
  intermediate?: JointOverride;
  distal?: JointOverride;
  thumbProximal?: JointOverride;
  thumbDistal?: JointOverride;
  thumbMetacarpal?: JointOverride;
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
  const joints: Array<keyof VrmMocapOverrides> = [
    "proximal",
    "intermediate",
    "distal",
    "thumbProximal",
    "thumbDistal",
    "thumbMetacarpal",
  ];
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
  return Object.keys(out).length > 0 ? out : null;
}

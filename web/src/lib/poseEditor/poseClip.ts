/**
 * Pose Editor → Mocap clip bridge.
 *
 * The editor mutates VRM bone quaternions directly (FK gizmo or IK
 * solver). Saving a pose just snapshots every tracked bone and emits
 * a 2-frame ``MocapClip`` — both frames identical so the playback
 * runtime treats it as a static pose. fps=10 + durationS=0.1 is the
 * smallest combination that satisfies the server's
 * ``frameCount = round(durationS * fps) + 1`` invariant with
 * ``frameCount >= 2``.
 */

import {
  MOCAP_BONES,
  MOCAP_CLIP_VERSION,
  type MocapBone,
  type MocapClip,
  type QuatTrack,
} from "@/lib/mocap/clipFormat";
import {
  snapshotBoneQuat,
  type MocapBoneMap,
} from "@/lib/mocap/vrmShared";

const STATIC_FPS = 10;
const STATIC_DURATION_S = 0.1; // → frameCount = round(0.1 * 10) + 1 = 2

/** Snapshot every resolved bone in ``map`` and pack into a 2-frame
 *  static-pose clip. Bones missing from the rig are simply omitted
 *  from the ``bones`` map (playback runtime treats absent tracks as
 *  "don't touch this bone"). */
export function snapshotPoseAsClip(
  map: MocapBoneMap,
  opts: { name: string; sourceVrm: string },
): MocapClip {
  const bones: Partial<Record<MocapBone, QuatTrack>> = {};
  const buf: [number, number, number, number] = [0, 0, 0, 1];
  for (const name of MOCAP_BONES) {
    const bone = map[name];
    if (!bone) continue;
    snapshotBoneQuat(bone, buf);
    // Two identical frames — playback samples either and gets the
    // same pose. We allocate a fresh array so the buffer mutation in
    // the next iteration doesn't leak across tracks.
    bones[name] = {
      data: [
        buf[0], buf[1], buf[2], buf[3],
        buf[0], buf[1], buf[2], buf[3],
      ],
    };
  }
  return {
    version: MOCAP_CLIP_VERSION,
    id: "",
    name: opts.name,
    sourceVrm: opts.sourceVrm,
    durationS: STATIC_DURATION_S,
    fps: STATIC_FPS,
    frameCount: 2,
    bones,
    expressions: {},
  };
}

/** Reset every resolved bone back to the captured baseline (the pose
 *  ``collectMocapBones`` recorded when the rig was loaded — arms-down
 *  for upperArms, identity for everything else). Called from the
 *  panel's "초기화" button. */
export function resetPoseToBaseline(map: MocapBoneMap): void {
  for (const bone of Object.values(map)) {
    if (!bone) continue;
    const baseline = bone.userData?.mocapBaseline;
    if (
      baseline &&
      typeof baseline === "object" &&
      "x" in baseline &&
      "y" in baseline &&
      "z" in baseline &&
      "w" in baseline
    ) {
      bone.quaternion.set(baseline.x, baseline.y, baseline.z, baseline.w);
    }
  }
}

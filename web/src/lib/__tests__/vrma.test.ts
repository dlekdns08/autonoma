import { describe, expect, it } from "vitest";
import * as THREE from "three";
import {
  buildVrmaScene,
  nearestPresentParent,
  sampleToMocapClip,
  vrmaExtensionJson,
  type FrameSampler,
  type RigRestPositions,
} from "@/lib/mocap/vrma";
import { expectedFrameCount, type MocapClip } from "@/lib/mocap/clipFormat";

/** Synthetic sampler: head yaws linearly over time, hips static, blink
 *  ramps 0→1. Deterministic so frame values can be asserted. */
function makeSampler(): FrameSampler {
  return {
    bones: ["hips", "head"],
    expressions: ["blink"],
    sample(t) {
      const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), t);
      return {
        bones: { hips: [0, 0, 0, 1], head: [q.x, q.y, q.z, q.w] },
        expressions: { blink: Math.min(1, t) },
      };
    },
  };
}

describe("sampleToMocapClip", () => {
  it("samples a uniform clip at the requested fps", () => {
    const clip = sampleToMocapClip(makeSampler(), {
      durationS: 1,
      fps: 30,
      name: "synthetic",
      sourceVrm: "midori.vrm",
      maxDurationS: 60,
    });
    expect(clip.version).toBe(1);
    expect(clip.frameCount).toBe(expectedFrameCount(1, 30)); // 31
    expect(clip.bones.hips?.data).toHaveLength(31 * 4);
    expect(clip.bones.head?.data).toHaveLength(31 * 4);
    expect(clip.expressions.blink?.data).toHaveLength(31);
    // Frame 0: identity, blink 0.
    expect(clip.bones.head?.data.slice(0, 4)).toEqual([0, 0, 0, 1]);
    expect(clip.expressions.blink?.data[0]).toBe(0);
    // Frame 15 is t=0.5: yaw 0.5 rad → y = sin(0.25), w = cos(0.25).
    const y = clip.bones.head!.data[15 * 4 + 1];
    const w = clip.bones.head!.data[15 * 4 + 3];
    expect(y).toBeCloseTo(Math.sin(0.25), 5);
    expect(w).toBeCloseTo(Math.cos(0.25), 5);
    expect(clip.expressions.blink!.data[15]).toBeCloseTo(0.5, 5);
    // Last frame is clamped to the clip end.
    expect(clip.expressions.blink!.data[30]).toBeCloseTo(1, 5);
    // Untouched slots are absent (idle keeps driving them on playback).
    expect(clip.bones.spine).toBeUndefined();
    expect(clip.expressions.aa).toBeUndefined();
  });

  it("rejects clips over the server limit or without duration", () => {
    expect(() =>
      sampleToMocapClip(makeSampler(), {
        durationS: 61,
        fps: 30,
        name: "x",
        sourceVrm: "",
        maxDurationS: 60,
      }),
    ).toThrow(/server limit/);
    expect(() =>
      sampleToMocapClip(makeSampler(), {
        durationS: 0,
        fps: 30,
        name: "x",
        sourceVrm: "",
        maxDurationS: 60,
      }),
    ).toThrow(/no duration/);
  });
});

describe("buildVrmaScene / vrmaExtensionJson", () => {
  const rig: RigRestPositions = {
    hips: [0, 1, 0],
    spine: [0, 1.1, 0],
    head: [0, 1.5, 0.05],
  };

  function makeClip(): MocapClip {
    return sampleToMocapClip(makeSampler(), {
      durationS: 0.5,
      fps: 10,
      name: "synthetic",
      sourceVrm: "midori.vrm",
      maxDurationS: 60,
    });
  }

  it("walks the humanoid parent map to the nearest exported bone", () => {
    const present = new Set(["hips", "head"] as const);
    // head → neck → upperChest → chest → spine → hips: only hips present.
    expect(nearestPresentParent("head", present)).toBe("hips");
    expect(nearestPresentParent("hips", present)).toBeNull();
  });

  it("builds a translation-only T-pose tree with local offsets", () => {
    const scene = buildVrmaScene(makeClip(), rig);
    const hips = scene.boneNodes.get("hips")!;
    const head = scene.boneNodes.get("head")!;
    expect(hips.parent).toBe(scene.root);
    // spine is in the rig but not in the clip → head hangs off hips.
    expect(head.parent).toBe(hips);
    expect(hips.position.toArray()).toEqual([0, 1, 0]);
    expect(head.position.x).toBeCloseTo(0);
    expect(head.position.y).toBeCloseTo(0.5);
    expect(head.position.z).toBeCloseTo(0.05);
    // Identity rest rotations → local == normalized on re-import.
    expect(hips.quaternion.toArray()).toEqual([0, 0, 0, 1]);
    expect(head.quaternion.toArray()).toEqual([0, 0, 0, 1]);
    // Expression node exists and sits under the root.
    expect(scene.expressionNodes.get("blink")?.parent).toBe(scene.root);
  });

  it("emits one rotation track per bone and an X-translation track per expression", () => {
    const clip = makeClip();
    const scene = buildVrmaScene(clip, rig);
    const names = scene.animation.tracks.map((t) => t.name).sort();
    expect(names).toEqual(["expression_blink.position", "head.quaternion", "hips.quaternion"]);
    const blink = scene.animation.tracks.find((t) => t.name === "expression_blink.position")!;
    expect(blink).toBeInstanceOf(THREE.VectorKeyframeTrack);
    expect(blink.times).toHaveLength(clip.frameCount);
    // Weight lives in X; Y/Z stay 0.
    expect(blink.values[0]).toBe(0);
    expect(blink.values[(clip.frameCount - 1) * 3]).toBeCloseTo(0.5, 5);
    expect(blink.values[1]).toBe(0);
    expect(blink.values[2]).toBe(0);
    const head = scene.animation.tracks.find((t) => t.name === "head.quaternion")!;
    expect(head).toBeInstanceOf(THREE.QuaternionKeyframeTrack);
    expect(head.values).toHaveLength(clip.frameCount * 4);
    expect(scene.animation.duration).toBeCloseTo(0.5);
  });

  it("writes the VRMC_vrm_animation JSON against the node index map", () => {
    const scene = buildVrmaScene(makeClip(), rig);
    const order: THREE.Object3D[] = [];
    scene.root.traverse((o) => {
      if (o !== scene.root) order.push(o);
    });
    const ext = vrmaExtensionJson(scene, (n) => {
      const i = order.indexOf(n);
      return i < 0 ? undefined : i;
    });
    expect(ext.specVersion).toBe("1.0");
    expect(ext.humanoid?.humanBones.hips?.node).toBe(order.indexOf(scene.boneNodes.get("hips")!));
    expect(ext.humanoid?.humanBones.head?.node).toBe(order.indexOf(scene.boneNodes.get("head")!));
    expect(ext.humanoid?.humanBones.spine).toBeUndefined();
    expect(ext.expressions?.preset.blink?.node).toBe(
      order.indexOf(scene.expressionNodes.get("blink")!),
    );
  });

  it("throws when the clip has nothing exportable for the rig", () => {
    const clip = makeClip();
    expect(() => buildVrmaScene({ ...clip, bones: {}, expressions: {} }, rig)).toThrow(
      /nothing exportable/,
    );
  });
});

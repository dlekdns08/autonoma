/**
 * VRM Animation (``.vrma``, glTF + ``VRMC_vrm_animation`` 1.0) ⇄ MocapClip.
 *
 * Import
 * ──────
 *   loadVrmAnimationFile()   .vrma bytes → VRMAnimation (three-vrm-animation)
 *   makeVrmAnimationSampler() VRMAnimation + target VRM → FrameSampler that
 *                             plays the clip through an AnimationMixer on
 *                             the VRM's scene and reads the NORMALIZED
 *                             humanoid bone quaternions + expression weights
 *   sampleToMocapClip()       FrameSampler → MocapClip at a fixed fps
 *                             (pure; unit-tested with a synthetic sampler)
 *
 *   Normalized-bone quaternions are exactly what the recorder stores and
 *   what ``VRMCharacter`` copies back onto the rig, so a sampled .vrma
 *   plays like a recorded clip. Hips translation is not part of the
 *   MocapClip format and is dropped on import.
 *
 * Export
 * ──────
 *   buildVrmaScene()          MocapClip + rest positions → Object3D tree
 *                             + AnimationClip (pure; unit-tested)
 *   vrmaExtensionJson()       node index map → VRMC_vrm_animation JSON
 *   exportMocapClipToVrma()   runs three's GLTFExporter (binary .glb) with
 *                             a plugin that writes the extension
 *
 *   Spec facts this relies on (read from the loader shipped in
 *   ``@pixiv/three-vrm-animation`` 3.5.5, ``_parseAnimation``):
 *     - ``extensions.VRMC_vrm_animation.humanoid.humanBones[name].node``
 *       maps humanoid bones to glTF nodes; bone ``rotation`` channels are
 *       local rotations which the loader converts to normalized space
 *       with the nodes' rest world rotations. We export every node with
 *       an IDENTITY rest rotation (translation-only T-pose skeleton), so
 *       local == normalized and the round-trip is exact.
 *     - Only ``hips`` may carry a ``translation`` channel (we emit none).
 *     - ``expressions.preset[name].node`` maps an expression to a node whose
 *       ``translation`` channel's X component is the weight.
 *     - ``specVersion`` must be ``"1.0"`` and the extension must be listed
 *       in ``extensionsUsed``; the loader warns when the rest hips world
 *       Y is ~0, so rest positions come from a real rig.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import {
  GLTFExporter,
  type GLTFExporterPlugin,
  type GLTFWriter,
} from "three/addons/exporters/GLTFExporter.js";
import {
  VRMHumanBoneParentMap,
  VRMLoaderPlugin,
  VRMUtils,
  type VRM,
  type VRMExpressionPresetName,
  type VRMHumanBoneName,
} from "@pixiv/three-vrm";
import {
  VRMAnimationLoaderPlugin,
  createVRMAnimationClip,
  type VRMAnimation,
} from "@pixiv/three-vrm-animation";
import {
  MOCAP_BONES,
  MOCAP_CLIP_VERSION,
  MOCAP_EXPRESSIONS,
  expectedFrameCount,
  type MocapBone,
  type MocapClip,
  type MocapExpression,
} from "./clipFormat";
import { clipCache } from "./clipPlayer";

export const VRMA_EXTENSION = "VRMC_vrm_animation";

// ── Sampling (import) ───────────────────────────────────────────────

export type Quat4 = readonly [number, number, number, number];

export interface FrameSample {
  bones: Partial<Record<MocapBone, Quat4>>;
  expressions: Partial<Record<MocapExpression, number>>;
}

/** Something that can be asked for the pose at an arbitrary time. */
export interface FrameSampler {
  /** Bones the sampler drives (subset of MOCAP_BONES, stable order). */
  bones: readonly MocapBone[];
  /** Expressions the sampler drives (subset of MOCAP_EXPRESSIONS). */
  expressions: readonly MocapExpression[];
  sample(tSec: number): FrameSample;
}

export interface SampleOptions {
  durationS: number;
  fps: number;
  name: string;
  sourceVrm: string;
  /** Server ceiling (``/api/mocap/triggers`` → ``maxClipDurationS``). */
  maxDurationS: number;
}

/** Uniformly sample a ``FrameSampler`` into the MocapClip wire format.
 *  Throws on an empty/over-limit duration or an empty sampler so the UI
 *  can show the reason instead of uploading junk. */
export function sampleToMocapClip(sampler: FrameSampler, opts: SampleOptions): MocapClip {
  const { durationS, fps } = opts;
  if (!(durationS > 0) || !Number.isFinite(durationS)) {
    throw new Error("animation has no duration");
  }
  if (durationS > opts.maxDurationS) {
    throw new Error(
      `animation is ${durationS.toFixed(1)}s, server limit is ${opts.maxDurationS}s`,
    );
  }
  if (!(fps > 0)) throw new Error("fps must be positive");
  if (sampler.bones.length === 0 && sampler.expressions.length === 0) {
    throw new Error("animation drives no supported bones or expressions");
  }
  const frameCount = expectedFrameCount(durationS, fps);
  const bones: MocapClip["bones"] = {};
  const expressions: MocapClip["expressions"] = {};
  for (const b of sampler.bones) bones[b] = { data: new Array<number>(frameCount * 4) };
  for (const e of sampler.expressions) expressions[e] = { data: new Array<number>(frameCount) };
  for (let i = 0; i < frameCount; i++) {
    // Clamp the last frame to the clip end so rounding never overshoots.
    const t = Math.min(durationS, i / fps);
    const frame = sampler.sample(t);
    for (const b of sampler.bones) {
      const q = frame.bones[b] ?? [0, 0, 0, 1];
      const d = bones[b]!.data;
      d[i * 4] = q[0];
      d[i * 4 + 1] = q[1];
      d[i * 4 + 2] = q[2];
      d[i * 4 + 3] = q[3];
    }
    for (const e of sampler.expressions) {
      expressions[e]!.data[i] = frame.expressions[e] ?? 0;
    }
  }
  return {
    version: MOCAP_CLIP_VERSION,
    id: "",
    name: opts.name,
    sourceVrm: opts.sourceVrm,
    durationS,
    fps,
    frameCount,
    bones,
    expressions,
    meta: { createdAt: new Date().toISOString() },
  };
}

/** Parse a ``.vrma`` (or any glTF carrying ``VRMC_vrm_animation``) and
 *  return its first animation. */
export async function loadVrmAnimationFile(bytes: ArrayBuffer): Promise<VRMAnimation> {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMAnimationLoaderPlugin(parser));
  const gltf = await loader.parseAsync(bytes, "");
  const anims = (gltf.userData as { vrmAnimations?: VRMAnimation[] }).vrmAnimations ?? [];
  if (anims.length === 0) {
    throw new Error("file has no VRMC_vrm_animation data");
  }
  return anims[0];
}

/** Load a catalog VRM off-screen (no renderer) for sampling or rest-pose
 *  extraction. Caller must ``VRMUtils.deepDispose(vrm.scene)`` when done. */
export async function loadVrmForSampling(url: string): Promise<VRM> {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(url);
  const vrm = (gltf.userData as { vrm?: VRM }).vrm;
  if (!vrm) throw new Error(`${url} is not a VRM`);
  return vrm;
}

export interface VrmAnimationSampler {
  sampler: FrameSampler;
  durationS: number;
  /** Stop the mixer and put the rig back at rest. */
  dispose(): void;
}

/** Drive ``vrm`` with the animation through an AnimationMixer and read the
 *  normalized bone quaternions / expression weights per frame. Only bones
 *  the animation actually animates AND the rig has are emitted, so the
 *  clip layers like a recording (absent tracks = idle keeps driving). */
export function makeVrmAnimationSampler(anim: VRMAnimation, vrm: VRM): VrmAnimationSampler {
  const clip = createVRMAnimationClip(anim, vrm);
  const mixer = new THREE.AnimationMixer(vrm.scene);
  const action = mixer.clipAction(clip);
  action.setLoop(THREE.LoopOnce, 1);
  action.clampWhenFinished = true;
  action.play();

  const humanoid = vrm.humanoid;
  const em = vrm.expressionManager ?? null;
  const boneNodes = new Map<MocapBone, THREE.Object3D>();
  for (const b of MOCAP_BONES) {
    if (!anim.humanoidTracks.rotation.has(b as VRMHumanBoneName)) continue;
    const node = humanoid?.getNormalizedBoneNode(b as VRMHumanBoneName) ?? null;
    if (node) boneNodes.set(b, node);
  }
  const exprNames: MocapExpression[] = [];
  for (const e of MOCAP_EXPRESSIONS) {
    if (!em) break;
    if (!anim.expressionTracks.preset.has(e as VRMExpressionPresetName)) continue;
    if (!em.getExpression(e)) continue;
    exprNames.push(e);
  }
  const bones = Array.from(boneNodes.keys());

  const sampler: FrameSampler = {
    bones,
    expressions: exprNames,
    sample(tSec) {
      mixer.setTime(tSec);
      const out: FrameSample = { bones: {}, expressions: {} };
      for (const [b, node] of boneNodes) {
        const q = node.quaternion;
        out.bones[b] = [q.x, q.y, q.z, q.w];
      }
      for (const e of exprNames) {
        out.expressions[e] = em?.getValue(e) ?? 0;
      }
      return out;
    },
  };
  return {
    sampler,
    durationS: anim.duration,
    dispose() {
      action.stop();
      mixer.uncacheRoot(vrm.scene);
      humanoid?.resetNormalizedPose();
      em?.resetValues();
    },
  };
}

// ── Building (export) ───────────────────────────────────────────────

/** Normalized rest-pose WORLD positions per bone (metres), from the
 *  target rig. Only bones present here can be exported. */
export type RigRestPositions = Partial<Record<MocapBone, readonly [number, number, number]>>;

/** Read rest positions off a freshly loaded VRM (rig at rest). */
export function rigRestFromVrm(vrm: VRM): RigRestPositions {
  const out: RigRestPositions = {};
  const humanoid = vrm.humanoid;
  if (!humanoid) return out;
  vrm.scene.updateMatrixWorld(true);
  const v = new THREE.Vector3();
  for (const b of MOCAP_BONES) {
    const node = humanoid.getNormalizedBoneNode(b as VRMHumanBoneName);
    if (!node) continue;
    node.getWorldPosition(v);
    out[b] = [v.x, v.y, v.z];
  }
  return out;
}

/** Nearest ancestor (per the VRM humanoid parent map) that is in ``present``. */
export function nearestPresentParent(
  bone: MocapBone,
  present: ReadonlySet<MocapBone>,
): MocapBone | null {
  let parent = VRMHumanBoneParentMap[bone as VRMHumanBoneName] as MocapBone | null | undefined;
  while (parent != null) {
    if (present.has(parent)) return parent;
    parent = VRMHumanBoneParentMap[parent as VRMHumanBoneName] as MocapBone | null | undefined;
  }
  return null;
}

export interface VrmaScene {
  root: THREE.Group;
  animation: THREE.AnimationClip;
  boneNodes: Map<MocapBone, THREE.Object3D>;
  expressionNodes: Map<MocapExpression, THREE.Object3D>;
}

/** Build the exportable node tree + AnimationClip for a MocapClip.
 *  Bones missing from ``rig`` are skipped (the clip may have been
 *  recorded on a different rig). Pure — no exporter involved. */
export function buildVrmaScene(clip: MocapClip, rig: RigRestPositions): VrmaScene {
  const root = new THREE.Group();
  root.name = "vrma_root";

  const present = new Set<MocapBone>();
  for (const b of MOCAP_BONES) {
    if (clip.bones[b] && rig[b]) present.add(b);
  }
  if (present.size === 0 && Object.keys(clip.expressions).length === 0) {
    throw new Error("clip has nothing exportable for this rig");
  }

  const boneNodes = new Map<MocapBone, THREE.Object3D>();
  // Create in MOCAP_BONES order (parents precede children in that list).
  for (const b of MOCAP_BONES) {
    if (!present.has(b)) continue;
    const node = new THREE.Object3D();
    node.name = b;
    const parentBone = nearestPresentParent(b, present);
    const parentPos = parentBone ? rig[parentBone]! : [0, 0, 0];
    const pos = rig[b]!;
    node.position.set(pos[0] - parentPos[0], pos[1] - parentPos[1], pos[2] - parentPos[2]);
    (parentBone ? boneNodes.get(parentBone)! : root).add(node);
    boneNodes.set(b, node);
  }

  const expressionNodes = new Map<MocapExpression, THREE.Object3D>();
  for (const e of MOCAP_EXPRESSIONS) {
    if (!clip.expressions[e]) continue;
    const node = new THREE.Object3D();
    node.name = `expression_${e}`;
    root.add(node);
    expressionNodes.set(e, node);
  }

  const times = new Float32Array(clip.frameCount);
  for (let i = 0; i < clip.frameCount; i++) times[i] = i / clip.fps;

  const tracks: THREE.KeyframeTrack[] = [];
  for (const [b, node] of boneNodes) {
    tracks.push(
      new THREE.QuaternionKeyframeTrack(
        `${node.name}.quaternion`,
        times,
        Float32Array.from(clip.bones[b]!.data),
      ),
    );
  }
  for (const [e, node] of expressionNodes) {
    const src = clip.expressions[e]!.data;
    const values = new Float32Array(clip.frameCount * 3);
    for (let i = 0; i < clip.frameCount; i++) values[i * 3] = src[i] ?? 0;
    tracks.push(new THREE.VectorKeyframeTrack(`${node.name}.position`, times, values));
  }
  const animation = new THREE.AnimationClip(clip.name || "clip", clip.durationS, tracks);
  return { root, animation, boneNodes, expressionNodes };
}

export interface VrmaExtensionJson {
  specVersion: "1.0";
  humanoid?: { humanBones: Partial<Record<MocapBone, { node: number }>> };
  expressions?: { preset: Partial<Record<MocapExpression, { node: number }>> };
}

/** The ``VRMC_vrm_animation`` object given a node→index map. */
export function vrmaExtensionJson(
  scene: VrmaScene,
  indexOf: (node: THREE.Object3D) => number | undefined,
): VrmaExtensionJson {
  const ext: VrmaExtensionJson = { specVersion: "1.0" };
  const humanBones: Partial<Record<MocapBone, { node: number }>> = {};
  for (const [b, node] of scene.boneNodes) {
    const idx = indexOf(node);
    if (idx !== undefined) humanBones[b] = { node: idx };
  }
  if (Object.keys(humanBones).length > 0) ext.humanoid = { humanBones };
  const preset: Partial<Record<MocapExpression, { node: number }>> = {};
  for (const [e, node] of scene.expressionNodes) {
    const idx = indexOf(node);
    if (idx !== undefined) preset[e] = { node: idx };
  }
  if (Object.keys(preset).length > 0) ext.expressions = { preset };
  return ext;
}

/** Shape of the writer internals the extension plugin needs (present in
 *  three's GLTFExporter.js but not in its ``.d.ts``). */
type WriterInternals = {
  json: { extensions?: Record<string, unknown> };
  nodeMap: Map<THREE.Object3D, number>;
  extensionsUsed: Record<string, boolean>;
};

/** Serialise a MocapClip to ``.vrma`` bytes (binary glTF). */
export async function exportMocapClipToVrma(
  clip: MocapClip,
  rig: RigRestPositions,
): Promise<ArrayBuffer> {
  const scene = buildVrmaScene(clip, rig);
  const exporter = new GLTFExporter();
  exporter.register((writer: GLTFWriter): GLTFExporterPlugin => ({
    afterParse: () => {
      // afterParse runs inside processInputAsync, before writeAsync
      // assembles ``json.extensionsUsed`` from ``writer.extensionsUsed``.
      const w = writer as unknown as WriterInternals;
      w.extensionsUsed[VRMA_EXTENSION] = true;
      w.json.extensions ??= {};
      w.json.extensions[VRMA_EXTENSION] = vrmaExtensionJson(scene, (n) => w.nodeMap.get(n));
    },
  }));
  const out = await exporter.parseAsync(scene.root, {
    binary: true,
    animations: [scene.animation],
  });
  if (!(out instanceof ArrayBuffer)) throw new Error("exporter did not return binary glTF");
  return out;
}

/** Browser download helper. */
export function downloadVrma(bytes: ArrayBuffer, baseName: string): void {
  const safe = (baseName || "clip").replace(/[^\w.-]+/g, "_").slice(0, 64);
  const blob = new Blob([bytes], { type: "model/gltf-binary" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${safe}.vrma`;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** End-to-end: fetch rest positions from the rig at ``vrmUrl``, build and
 *  download ``clip`` as ``.vrma``. The rig is disposed afterwards. */
export async function exportClipAsVrma(clip: MocapClip, vrmUrl: string): Promise<void> {
  const vrm = await loadVrmForSampling(vrmUrl);
  try {
    const bytes = await exportMocapClipToVrma(clip, rigRestFromVrm(vrm));
    downloadVrma(bytes, clip.name);
  } finally {
    VRMUtils.deepDispose(vrm.scene);
  }
}

/** Export a library clip or a generated gesture (``gen:<id>``) by its
 *  clip ref — fetched through the shared ``clipCache``. */
export async function exportClipRefAsVrma(clipRef: string, vrmUrl: string): Promise<void> {
  clipCache.retain(clipRef);
  try {
    const clip = await clipCache.ensure(clipRef);
    await exportClipAsVrma(clip, vrmUrl);
  } finally {
    clipCache.release(clipRef);
  }
}

/** End-to-end import: ``.vrma`` bytes + target rig → MocapClip. */
export async function importVrmaAsClip(
  bytes: ArrayBuffer,
  vrmUrl: string,
  opts: { name: string; sourceVrm: string; fps: number; maxDurationS: number },
): Promise<MocapClip> {
  const anim = await loadVrmAnimationFile(bytes);
  const vrm = await loadVrmForSampling(vrmUrl);
  const run = makeVrmAnimationSampler(anim, vrm);
  try {
    return sampleToMocapClip(run.sampler, {
      durationS: run.durationS,
      fps: opts.fps,
      name: opts.name,
      sourceVrm: opts.sourceVrm,
      maxDurationS: opts.maxDurationS,
    });
  } finally {
    run.dispose();
    VRMUtils.deepDispose(vrm.scene);
  }
}

#!/usr/bin/env node
/**
 * Download MediaPipe Tasks Vision WASM runtime + Face/Pose Landmarker
 * model bundles into ``web/public/mediapipe/`` so the ``/mocap`` page
 * can load them without a CDN round-trip (offline-safe + reproducible).
 *
 * Pinned versions — bump these together when upgrading
 * ``@mediapipe/tasks-vision`` in package.json, then commit the updated
 * ``public/mediapipe/*`` files so downstream installs don't have to
 * re-fetch.
 */
import { createWriteStream } from "node:fs";
import { mkdir, stat, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { pipeline } from "node:stream/promises";

const TASKS_VERSION = "0.10.17";
const TASKS_CDN = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${TASKS_VERSION}/wasm`;
const MODELS = {
  "face_landmarker.task":
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
  "pose_landmarker_full.task":
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
};
const WASM_FILES = [
  "vision_wasm_internal.js",
  "vision_wasm_internal.wasm",
  "vision_wasm_nosimd_internal.js",
  "vision_wasm_nosimd_internal.wasm",
];

const here = dirname(fileURLToPath(import.meta.url));
const targetDir = join(here, "..", "public", "mediapipe");

async function exists(p) {
  try {
    await stat(p);
    return true;
  } catch {
    return false;
  }
}

async function download(url, dest) {
  process.stdout.write(`  fetching ${url}\n`);
  const res = await fetch(url);
  if (!res.ok || !res.body) {
    throw new Error(`HTTP ${res.status} ${res.statusText} for ${url}`);
  }
  const tmp = dest + ".part";
  await pipeline(res.body, createWriteStream(tmp));
  const { rename } = await import("node:fs/promises");
  await rename(tmp, dest);
}

async function main() {
  const force = process.argv.includes("--force");
  await mkdir(targetDir, { recursive: true });

  for (const name of WASM_FILES) {
    const dest = join(targetDir, name);
    if (!force && (await exists(dest))) continue;
    await download(`${TASKS_CDN}/${name}`, dest);
  }
  for (const [name, url] of Object.entries(MODELS)) {
    const dest = join(targetDir, name);
    if (!force && (await exists(dest))) continue;
    await download(url, dest);
  }
  process.stdout.write(`done → ${targetDir}\n`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

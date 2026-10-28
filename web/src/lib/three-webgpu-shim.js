/**
 * Build-time shim for ``three/webgpu`` (wired via ``turbopack.resolveAlias``
 * in ``next.config.ts``).
 *
 * ``@pixiv/three-vrm/nodes`` (the WebGPU MToon node material) keeps a
 * runtime compatibility branch for three < r168 that reads
 * ``THREE_WEBGPU.tslFn``. Three r168+ renamed it to ``Fn`` (exported from
 * ``three/tsl``), so the name no longer exists in ``three.webgpu.js`` and
 * Turbopack's static ESM analysis turns the dead branch into a hard
 * build error ("The export tslFn was not found").
 *
 * This module re-exports the real build (by relative path, bypassing the
 * package export map — the file still imports the same ``three.core.js``
 * as ``three``, so there is exactly one copy of three at runtime) and adds
 * an explicit, never-read ``tslFn`` so the static check passes. three-vrm
 * takes its ``Fn`` branch on r168+ and never calls this value.
 */
export * from "../../node_modules/three/build/three.webgpu.js";
export const tslFn = undefined;

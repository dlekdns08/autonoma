/**
 * Opt-in WebGPU rendering for the VRM character.
 *
 *   ?renderer=webgpu            → per page load
 *   NEXT_PUBLIC_RENDERER=webgpu → build-time default
 *
 * Anything else (including an unsupported browser, a failed
 * ``WebGPURenderer.init()`` or a failed chunk load) keeps the stock
 * WebGL path: ``<Canvas gl={{ alpha: true }}>`` with the plain
 * ``VRMLoaderPlugin``. The WebGPU modules (``three/webgpu`` and the
 * MToon node material) are pulled in through dynamic imports so the
 * default route never pays for them.
 *
 * MToon on WebGPU: three-vrm ≥ 3 ships ``MToonNodeMaterial``
 * (``@pixiv/three-vrm/nodes``); passing it as ``materialType`` to
 * ``MToonMaterialLoaderPlugin`` makes the loader build node materials
 * instead of the WebGL ``MToonMaterial``. We wire that up whenever the
 * WebGPU path is active, so the character keeps its toon shading.
 */

import type { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import type { WebGPURenderer } from "three/webgpu";
import type { GLProps } from "@react-three/fiber";

/** Argument r3f hands an async ``gl`` factory (``DefaultGLProps`` is
 *  not re-exported from the package root, so derive it). */
type DefaultGLProps = Parameters<Extract<GLProps, (p: never) => unknown>>[0];
import type { MToonNodeMaterial } from "@pixiv/three-vrm/nodes";
import {
  MToonMaterialLoaderPlugin,
  VRMLoaderPlugin,
} from "@pixiv/three-vrm";

export type RendererMode = "webgl" | "webgpu";

export interface WebGpuModules {
  WebGPURenderer: typeof WebGPURenderer;
  MToonNodeMaterial: typeof MToonNodeMaterial;
}

/** Decide the renderer for this page load. Safe to call during render
 *  (pure, no side effects) and on the server (always ``"webgl"``). */
export function resolveRendererMode(): RendererMode {
  if (typeof window === "undefined") return "webgl";
  let fromQuery: string | null = null;
  try {
    fromQuery = new URLSearchParams(window.location.search).get("renderer");
  } catch {
    fromQuery = null;
  }
  const wanted = (fromQuery ?? process.env.NEXT_PUBLIC_RENDERER ?? "").toLowerCase();
  return wanted === "webgpu" ? "webgpu" : "webgl";
}

let modulesPromise: Promise<WebGpuModules | null> | null = null;

/** Load the WebGPU renderer + MToon node material once. Resolves
 *  ``null`` when the browser has no ``navigator.gpu`` or either chunk
 *  fails to load, which callers treat as "use WebGL". */
export function loadWebGpuModules(): Promise<WebGpuModules | null> {
  if (modulesPromise) return modulesPromise;
  modulesPromise = (async () => {
    if (typeof navigator === "undefined" || !("gpu" in navigator)) return null;
    try {
      const [webgpu, nodes] = await Promise.all([
        import("three/webgpu"),
        import("@pixiv/three-vrm/nodes"),
      ]);
      return {
        WebGPURenderer: webgpu.WebGPURenderer,
        MToonNodeMaterial: nodes.MToonNodeMaterial,
      };
    } catch (err) {
      console.warn("[VRMCharacter] WebGPU modules failed to load — using WebGL", err);
      return null;
    }
  })();
  return modulesPromise;
}

/** Register the VRM parser on a GLTFLoader for the active renderer.
 *  With WebGPU modules present the MToon loader plugin is told to build
 *  ``MToonNodeMaterial``; otherwise the stock plugin is used (the
 *  WebGL path is unchanged). */
export function registerVrmLoaderPlugin(
  loader: GLTFLoader,
  webgpu: WebGpuModules | null | undefined,
): void {
  if (webgpu) {
    loader.register((parser) => {
      const mtoonMaterialPlugin = new MToonMaterialLoaderPlugin(parser, {
        materialType: webgpu.MToonNodeMaterial,
      });
      return new VRMLoaderPlugin(parser, { mtoonMaterialPlugin });
    });
    return;
  }
  loader.register((parser) => new VRMLoaderPlugin(parser));
}

/** Async ``gl`` factory for ``<Canvas>``: builds and initialises a
 *  ``WebGPURenderer``; falls back to a ``WebGLRenderer`` when
 *  ``init()`` throws (no adapter, lost device, etc.). */
export function createWebGpuRendererFactory(webgpu: WebGpuModules) {
  return async (defaults: DefaultGLProps) => {
    const THREE = await import("three");
    // WebGPU's power preference has no "default" member.
    const powerPreference =
      defaults.powerPreference === "high-performance" ||
      defaults.powerPreference === "low-power"
        ? defaults.powerPreference
        : undefined;
    try {
      const renderer = new webgpu.WebGPURenderer({
        canvas: defaults.canvas as HTMLCanvasElement,
        alpha: true,
        antialias: defaults.antialias ?? true,
        powerPreference,
      });
      // r3f requires the factory to await init() so readiness (and later
      // teardown) includes device acquisition.
      await renderer.init();
      return renderer;
    } catch (err) {
      console.warn("[VRMCharacter] WebGPURenderer.init() failed — using WebGL", err);
      return new THREE.WebGLRenderer({
        canvas: defaults.canvas as HTMLCanvasElement,
        alpha: true,
        antialias: defaults.antialias ?? true,
        powerPreference: defaults.powerPreference,
      });
    }
  };
}

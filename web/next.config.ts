import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: {
    resolveAlias: {
      // See src/lib/three-webgpu-shim.js — adds the ``tslFn`` name that
      // @pixiv/three-vrm/nodes references in a three<r168 compat branch,
      // which Turbopack otherwise rejects at build time. The shim
      // re-exports the real ``three/webgpu`` build unchanged.
      "three/webgpu": "./src/lib/three-webgpu-shim.js",
    },
  },
};

export default nextConfig;

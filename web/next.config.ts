import type { NextConfig } from "next";

// Baseline security headers for every page. ``frame-ancestors`` /
// ``X-Frame-Options`` stop the dashboard being framed by another site
// (OBS browser sources load pages top-level, so they are unaffected).
// No script CSP: the app relies on inline bootstrap scripts, wasm and
// blob workers (three, mediapipe, transformers.js).
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "SAMEORIGIN" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'self'" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
  { key: "Permissions-Policy", value: "geolocation=(), payment=(), usb=()" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
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

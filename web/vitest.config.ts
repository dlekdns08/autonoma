import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Why Vitest over Jest:
// - One config drives both unit and component tests, no Babel layer.
// - The `@vitejs/plugin-react` handles JSX + Fast Refresh equivalently
//   to what Next provides at runtime, so test renders match prod.
// - Native ESM, which `next` 16 + React 19 expect.
//
// The `@/` alias matches `tsconfig.json#paths` so `import x from
// "@/components/Foo"` works the same in tests as in app code.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    // Don't pull these into Vitest's runner — they ship binary deps,
    // talk to the GPU, or rely on browser-only APIs that jsdom can't
    // emulate. Tests for code that uses them should stub the module.
    exclude: ["node_modules", ".next", "dist"],
    css: false,
  },
});

import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end + visual regression for the web app.
 *
 * The backend is never started: ``e2e/support/mockBackend.ts`` answers
 * the HTTP API with empty payloads and plays the WebSocket server,
 * pushing a fixed swarm snapshot so the stage renders the same cast every
 * run. The app is served from a production build (``next start``) so
 * screenshots match what users see.
 *
 * WebGL runs on SwiftShader (software) in headless Chromium, which is
 * slow but deterministic enough for the VRM render checks.
 *
 * Snapshot baselines are per-OS (``*-darwin.png`` / ``*-linux.png``):
 * font rasterisation differs. CI runs with ``--update-snapshots=missing``
 * until Linux baselines are committed, and uploads them as an artifact.
 */
const PORT = Number(process.env.E2E_PORT ?? 3100);

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./e2e/.results",
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}-{platform}{ext}",
  timeout: 60_000,
  expect: {
    timeout: 20_000,
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      scale: "css",
      maxDiffPixelRatio: 0.01,
    },
  },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["list"], ["html", { outputFolder: "e2e/.report", open: "never" }]]
    : [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
    colorScheme: "dark",
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 800 },
        deviceScaleFactor: 1,
        launchOptions: {
          args: [
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--ignore-gpu-blocklist",
            "--font-render-hinting=none",
            "--disable-lcd-text",
          ],
        },
      },
    },
  ],
  webServer: {
    command: `npm run build && npx next start -p ${PORT} -H 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});

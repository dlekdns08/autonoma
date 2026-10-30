import path from "node:path";
import { expect, test } from "@playwright/test";
import { CAST, collectPageErrors, mockBackend, seedRandom } from "./support/mockBackend";
import { nonKeyPixelRatio } from "./support/pixels";

// Canvases animate every frame and cover the whole viewport, so a
// ``mask`` would paint over the UI we want to pin. Hide them for the
// screenshot instead (stylePath); the overlays stay in the image. The 3D
// render itself is covered by the green-screen pixel check.
const HIDE_CANVAS = path.join(__dirname, "support", "hide-canvas.css");

// OBS chroma key is broadcast green #00b140, not pure 0,255,0.
const GREEN: [number, number, number] = [0x00, 0xb1, 0x40];

test.describe("VTuber stage", () => {
  test.beforeEach(async ({ page }) => {
    await seedRandom(page);
  });

  test("OBS green screen renders a VRM character over a clean key", async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockBackend(page);
    await page.goto("/obs?bg=green&ui=0&chat=0&ticker=0");

    const canvas = page.locator("canvas").first();
    await expect(canvas).toBeVisible();

    // VRM load + first frames on SwiftShader take a while; poll until
    // the character covers a meaningful part of the frame.
    await expect
      .poll(async () => nonKeyPixelRatio(page, canvas, GREEN), {
        message: "VRM character should cover part of the green screen",
        timeout: 45_000,
        intervals: [1000, 2000, 3000],
      })
      .toBeGreaterThan(0.03);

    // …but the key colour must still dominate (no mood tint or
    // backdrop bleeding over the chroma, see the P1-3 fix).
    const page_ratio = await nonKeyPixelRatio(page, page.locator("body"), GREEN);
    expect(page_ratio).toBeLessThan(0.6);
    expect(errors).toEqual([]);
  });

  test("OBS shows no site chrome (theme/BGM buttons would be on stream)", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/obs?bg=green&chat=0&ticker=0");
    await expect(page.locator("canvas").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /theme/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /music/i })).toHaveCount(0);
  });

  test("OBS overlay UI (name tag + subtitle) matches baseline", async ({ page }) => {
    const backend = await mockBackend(page);
    await page.goto("/obs?bg=green&chat=0&ticker=0");
    await expect(page.locator("canvas").first()).toBeVisible();
    // The spotlight follows whoever speaks; a finished line shows as the
    // subtitle for the spotlit agent.
    const line = "API 라우터 다 만들었어요!";
    backend.send("agent.speech", { agent: CAST[0].name, text: line, style: "bold" });
    await expect(page.getByText(line).first()).toBeVisible({ timeout: 20_000 });
    // The 3D canvas animates (idle sway, blinks); mask it so the
    // baseline pins the 2D overlay only.
    await expect(page).toHaveScreenshot("obs-overlay.png", {
      stylePath: HIDE_CANVAS,
      maxDiffPixels: 150,
    });
  });

  test("dashboard with a running swarm matches baseline", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await expect(page.getByText("Midori").first()).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(1500);
    await expect(page).toHaveScreenshot("dashboard-running.png", {
      stylePath: HIDE_CANVAS,
      // The spotlight character (role="img", labelled "<name> — mood,
      // state") keeps animating; pin everything around it.
      // Pixel-stage sprites wander on a timer; their layout is checked by
      // the smoke tests, not pixel-for-pixel.
      mask: [page.getByRole("img", { name: / — / }), page.getByTestId("pixel-stage")],
      maxDiffPixels: 150,
    });
  });

  test("refusal from the backend reaches the stage as a toast", async ({ page }) => {
    const backend = await mockBackend(page);
    await page.goto("/");
    await expect(page.getByText("Midori").first()).toBeVisible({ timeout: 20_000 });
    backend.send("agent.refusal", { agent: "Midori", category: "cyber" });
    await expect(page.getByText(/declined that turn/i).first()).toBeVisible();
  });

  test("login prompt shows when the socket is unauthenticated", async ({ page }) => {
    await mockBackend(page, { authenticated: false });
    await page.goto("/");
    await expect(page.getByRole("dialog", { name: /Autonoma/ })).toBeVisible({ timeout: 20_000 });
    await expect(page).toHaveScreenshot("login-prompt.png", {
      stylePath: HIDE_CANVAS,
      maxDiffPixels: 150,
    });
  });
});

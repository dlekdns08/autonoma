import { expect, test } from "@playwright/test";
import { collectPageErrors, mockBackend } from "./support/mockBackend";

/** Every route must render without an uncaught exception. Pages that
 *  need data get empty payloads from the mock backend, so this also
 *  catches crashes on empty states. */
const ROUTES = [
  "/",
  "/obs",
  "/live",
  "/leaderboard",
  "/chibi-gallery",
  "/cutscenes",
  "/showdown",
  "/podcast",
  "/voice",
  "/mocap",
  "/pose-editor",
  "/watch/ABC123",
  "/share/ABC123",
  "/agent/Midori",
  "/admin/runs",
  "/admin/users",
  "/admin/memory",
  "/admin/achievements",
  "/admin/ab-compare",
];

for (const route of ROUTES) {
  test(`route ${route} renders without page errors`, async ({ page }) => {
    const errors = collectPageErrors(page);
    await mockBackend(page);
    const res = await page.goto(route, { waitUntil: "domcontentloaded" });
    expect(res?.status(), `HTTP status for ${route}`).toBeLessThan(400);
    await expect(page.locator("body")).toBeVisible();
    // Let client effects (WS connect, fetches, dynamic imports) settle.
    await page.waitForTimeout(1500);
    expect(errors, `uncaught errors on ${route}`).toEqual([]);
  });
}

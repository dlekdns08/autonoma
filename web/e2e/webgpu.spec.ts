import { expect, test } from "@playwright/test";
import { collectPageErrors, mockBackend, seedRandom } from "./support/mockBackend";
import { nonKeyPixelRatio } from "./support/pixels";

/**
 * Opt-in WebGPU renderer (``?renderer=webgpu``) on the OBS green screen.
 *
 * Headless Chromium only exposes a WebGPU adapter behind
 * ``--enable-unsafe-webgpu`` (Chromium 156 then hands out SwiftShader's
 * software adapter, ``isFallbackAdapter: true``), and only in a secure
 * context — ``http://127.0.0.1`` qualifies. The test probes the adapter
 * from the app page itself and then asserts the matching path:
 *
 *   adapter available → ``data-renderer="webgpu"`` (WebGPURenderer on its
 *                       WebGPU backend) and the VRM covers the green key;
 *   no adapter        → the documented WebGL fallback renders the VRM
 *                       (``data-renderer`` "webgl" or "webgpu-on-webgl"),
 *                       and an annotation records that WebGPU itself was
 *                       not exercised.
 *
 * Whichever path ran is always written to ``test.info().annotations``.
 */

const GREEN: [number, number, number] = [0x00, 0xb1, 0x40];

test.use({
  launchOptions: {
    args: [
      "--use-angle=swiftshader",
      "--enable-unsafe-swiftshader",
      "--ignore-gpu-blocklist",
      "--font-render-hinting=none",
      "--disable-lcd-text",
      "--enable-unsafe-webgpu",
    ],
  },
});

test("OBS ?renderer=webgpu renders the character (WebGPU or documented fallback)", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const errors = collectPageErrors(page);
  await seedRandom(page);
  await mockBackend(page);
  await page.goto("/obs?bg=green&ui=0&chat=0&ticker=0&renderer=webgpu");

  const probe = await page.evaluate(async () => {
    const gpu = (navigator as Navigator & { gpu?: { requestAdapter(): Promise<unknown> } }).gpu;
    if (!gpu) return { gpu: false, adapter: false, info: "" };
    const adapter = (await gpu.requestAdapter()) as
      | {
          info?: { vendor?: string; architecture?: string; isFallbackAdapter?: boolean };
          isFallbackAdapter?: boolean;
        }
      | null;
    return {
      gpu: true,
      adapter: !!adapter,
      info: adapter
        ? `${adapter.info?.vendor ?? "?"}/${adapter.info?.architecture ?? "?"}` +
          (adapter.info?.isFallbackAdapter ?? adapter.isFallbackAdapter ? " (fallback)" : "")
        : "",
    };
  });

  const stage = page.locator("[data-renderer]").first();
  await expect(stage, "VRMCharacter should report the renderer it created").toBeVisible({
    timeout: 45_000,
  });
  const renderer = await stage.getAttribute("data-renderer");

  if (probe.adapter) {
    test.info().annotations.push({
      type: "renderer-path",
      description: `webgpu: adapter ${probe.info}; data-renderer=${renderer}`,
    });
    expect(renderer, "an adapter exists, so the WebGPU backend must be used").toBe("webgpu");
  } else {
    test.info().annotations.push({
      type: "renderer-path",
      description:
        `webgl-fallback: navigator.gpu=${probe.gpu}, no adapter; data-renderer=${renderer}. ` +
        "WebGPU itself was NOT exercised.",
    });
    expect(["webgl", "webgpu-on-webgl"]).toContain(renderer);
  }

  // Same green-screen check as vtuber.spec.ts: the character covers a
  // meaningful part of the frame and the key still dominates.
  const canvas = page.locator("canvas").first();
  await expect
    .poll(async () => nonKeyPixelRatio(page, canvas, GREEN), {
      message: `VRM should cover part of the green screen (renderer=${renderer})`,
      timeout: 60_000,
      intervals: [1000, 2000, 3000],
    })
    .toBeGreaterThan(0.03);
  expect(await nonKeyPixelRatio(page, page.locator("body"), GREEN)).toBeLessThan(0.6);
  // Keep the frame with the result so a human can eyeball what the
  // (software) WebGPU path actually drew.
  await test.info().attach(`frame-${renderer}`, {
    body: await canvas.screenshot({ animations: "disabled" }),
    contentType: "image/png",
  });
  expect(errors).toEqual([]);
});

test("default OBS (no ?renderer) stays on WebGL", async ({ page }) => {
  await seedRandom(page);
  await mockBackend(page);
  await page.goto("/obs?bg=green&ui=0&chat=0&ticker=0");
  await expect(page.locator("[data-renderer]").first()).toHaveAttribute("data-renderer", "webgl", {
    timeout: 45_000,
  });
});

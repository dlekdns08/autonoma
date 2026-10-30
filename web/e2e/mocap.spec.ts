import { gunzipSync } from "node:zlib";
import { expect, test } from "@playwright/test";
import { collectPageErrors, mockBackend } from "./support/mockBackend";

/**
 * /mocap with Chromium's fake camera.
 *
 * The fake device streams a synthetic test pattern (a moving pac-man
 * and a frame counter), not a person, so MediaPipe 1.2 normally finds no
 * face/pose landmarks.
 *
 * Two outcomes are accepted, and the test records which one ran in
 * ``test.info().annotations``:
 *   - "tracked":   the take has ≥1 bone/expression track → upload is
 *     offered, and the POST /api/mocap-clips body is gunzipped and its
 *     MocapClip shape validated (version 1, fps 30, frameCount, tracks);
 *   - "untracked": nothing was detected → the badge reads "카메라 연결됨"
 *     (connected, not tracking — vs "추적 중") and the page shows the
 *     "사람이 감지되지 않아…" message instead of offering an upload the
 *     server would reject as ``empty_clip``. No upload may be sent.
 * In both cases the MediaPipe pipeline must have initialised from the
 * local /mediapipe assets (status reached "running", no error banner,
 * no uncaught page errors).
 */

const API = "http://localhost:3479";

test.use({
  permissions: ["camera"],
  launchOptions: {
    args: [
      // Keep the project's SwiftShader flags (MocapPreview is WebGL, and
      // MediaPipe's GPU delegate needs WebGL2).
      "--use-angle=swiftshader",
      "--enable-unsafe-swiftshader",
      "--ignore-gpu-blocklist",
      "--font-render-hinting=none",
      "--disable-lcd-text",
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
    ],
  },
});

interface WireClip {
  version: number;
  id: string;
  name: string;
  sourceVrm: string;
  durationS: number;
  fps: number;
  frameCount: number;
  bones: Record<string, { data: number[] }>;
  expressions: Record<string, { data: number[] }>;
}

test("fake camera → record → upload sends a valid gzip/base64 clip", async ({ page }) => {
  test.setTimeout(150_000);
  const errors = collectPageErrors(page);
  // MediaPipe/wasm logs land on the console; keep them for the report
  // only if the run fails.
  const consoleErrors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") consoleErrors.push(m.text());
  });

  await mockBackend(page);

  // Upload endpoint: capture the body and answer like the server does.
  let uploadBody: Record<string, unknown> | null = null;
  await page.route(`${API}/api/mocap-clips`, async (route) => {
    if (route.request().method() !== "POST") {
      return route.fulfill({ json: { clips: [] } });
    }
    uploadBody = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({
      status: 201,
      json: {
        clip: {
          id: "clip-e2e",
          owner_user_id: "u-e2e",
          name: String(uploadBody.name ?? ""),
          source_vrm: String(uploadBody.source_vrm ?? ""),
          duration_s: 1,
          fps: 30,
          frame_count: 31,
          size_bytes: 1,
          created_at: "2026-10-30T00:00:00Z",
          updated_at: "2026-10-30T00:00:00Z",
        },
      },
    });
  });

  // Count MediaPipe asset fetches — proves the local wasm + models are
  // what the page actually loaded (served from web/public/mediapipe).
  const mediapipeAssets = new Set<string>();
  page.on("requestfinished", (req) => {
    const u = new URL(req.url());
    if (u.pathname.startsWith("/mediapipe/")) mediapipeAssets.add(u.pathname);
  });

  await page.goto("/mocap");
  await page.getByRole("button", { name: /midori\.vrm/ }).click();

  await page.getByRole("button", { name: "카메라 켜기" }).click();
  // FilesetResolver + Face/Pose landmarkers are created before status
  // flips to "running", which is what reveals the record button.
  const record = page.getByRole("button", { name: "● 녹화" });
  await expect(record).toBeVisible({ timeout: 90_000 });
  // WebcamPanel's error banner is role="alert" with text; Next's route
  // announcer is an always-present, empty role="alert" — ignore that one.
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);

  await record.click();
  await expect(page.getByRole("button", { name: /■ 정지/ })).toBeVisible();
  await page.waitForTimeout(3000);
  await page.getByRole("button", { name: /■ 정지/ }).click();

  // Badge state at stop time: "추적 중" (bones landing) vs "카메라 연결됨".
  const badge = page.getByText(/^(추적 중|카메라 연결됨)$/);
  await expect(badge).toBeVisible();
  const badgeText = (await badge.textContent())?.trim() ?? "";

  // Either the take had tracks (upload offered) or nothing was detected
  // (the page says so instead of offering an upload the server would
  // reject as ``empty_clip``).
  const upload = page.getByRole("button", { name: "업로드" });
  const noPerson = page.getByText(/사람이 감지되지 않아/);
  await expect(upload.or(noPerson)).toBeVisible();

  if (await noPerson.isVisible()) {
    test.info().annotations.push({
      type: "mocap-path",
      description:
        "untracked: fake camera pattern produced no landmarks; asserted pipeline init, " +
        "'카메라 연결됨' badge and the no-person message (no upload attempted)",
    });
    expect(badgeText).toBe("카메라 연결됨");
    await expect(upload).toHaveCount(0);
    expect(uploadBody, "no upload for an empty take").toBeNull();
    assertPipelineHealthy(mediapipeAssets, errors, consoleErrors);
    return;
  }

  await upload.click();
  await expect(page.getByText(/저장 완료/)).toBeVisible();

  expect(uploadBody, "POST /api/mocap-clips was sent").not.toBeNull();
  const body = uploadBody as unknown as {
    name: string;
    source_vrm: string;
    payload_gz_b64: string;
    expected_size_bytes: number;
  };
  expect(body.source_vrm).toBe("midori.vrm");
  const raw = gunzipSync(Buffer.from(body.payload_gz_b64, "base64"));
  expect(raw.byteLength).toBe(body.expected_size_bytes);
  const clip = JSON.parse(raw.toString("utf8")) as WireClip;

  expect(clip.version).toBe(1);
  expect(clip.fps).toBe(30);
  expect(clip.sourceVrm).toBe("midori.vrm");
  expect(clip.durationS).toBeGreaterThan(1);
  expect(clip.frameCount).toBe(Math.round(clip.durationS * clip.fps) + 1);
  for (const t of Object.values(clip.bones)) expect(t.data).toHaveLength(clip.frameCount * 4);
  for (const t of Object.values(clip.expressions)) expect(t.data).toHaveLength(clip.frameCount);

  const boneTracks = Object.keys(clip.bones).length;
  const exprTracks = Object.keys(clip.expressions).length;
  expect(boneTracks + exprTracks, "at least one bone or expression track").toBeGreaterThan(0);
  test.info().annotations.push({
    type: "mocap-path",
    description: `tracked: ${boneTracks} bone + ${exprTracks} expression tracks uploaded`,
  });
  assertPipelineHealthy(mediapipeAssets, errors, consoleErrors);
});

/** MediaPipe ran from the locally served assets and nothing crashed. */
function assertPipelineHealthy(assets: Set<string>, errors: string[], consoleErrors: string[]) {
  expect([...assets].some((p) => p.endsWith(".wasm")), "local MediaPipe wasm loaded").toBe(true);
  expect([...assets]).toEqual(
    expect.arrayContaining(["/mediapipe/face_landmarker.task", "/mediapipe/pose_landmarker_full.task"]),
  );
  expect(errors, `uncaught page errors; console errors: ${consoleErrors.join(" | ")}`).toEqual([]);
}

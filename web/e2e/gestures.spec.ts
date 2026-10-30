import { gzipSync } from "node:zlib";
import { expect, test, type Request } from "@playwright/test";
import { collectPageErrors, mockBackend, seedRandom } from "./support/mockBackend";

/**
 * Self-authored signature moves in the agent modal: list, preview,
 * vote, compose. All gesture endpoints are mocked here; the clip
 * payload is a real gzip+base64 MocapClip so the client decodes and
 * plays it like a server-served one.
 *
 * Bear is deliberately NOT the agent in the spotlight (Director is), so
 * the preview also checks that previewing pins the spotlight to the
 * agent — only the spotlit VRM plays mocap clips.
 */

const API = "http://localhost:3479";
const AGENT = "Bear";

function gesture(id: string, name: string, kind: string, value: string, extra = {}) {
  return {
    id,
    clip_ref: `gen:${id}`,
    agent_name: AGENT,
    trigger_kind: kind,
    trigger_value: value,
    name,
    duration_s: 1,
    source: "llm",
    votes_up: 2,
    votes_down: 0,
    status: "active",
    ...extra,
  };
}

const GESTURES = [
  gesture("g-victory", "Victory Stomp", "state", "celebrating"),
  gesture("g-ponder", "Paw on Chin", "state", "thinking", { source: "fallback", votes_up: 0 }),
];

/** A tiny valid MocapClip (1 s @ 30 fps, head yaw + blink), encoded the
 *  way the server serves it: JSON → gzip → base64. */
function clipPayload() {
  const fps = 30;
  const durationS = 1;
  const frameCount = Math.round(durationS * fps) + 1;
  const head: number[] = [];
  const blink: number[] = [];
  for (let i = 0; i < frameCount; i++) {
    const a = (i / (frameCount - 1)) * 0.4;
    head.push(0, Math.sin(a / 2), 0, Math.cos(a / 2));
    blink.push(i % 10 === 0 ? 1 : 0);
  }
  const clip = {
    version: 1,
    id: "gen:g-victory",
    name: "Victory Stomp",
    sourceVrm: "midori.vrm",
    durationS,
    fps,
    frameCount,
    bones: { head: { data: head } },
    expressions: { blink: { data: blink } },
  };
  return {
    clip: { id: "gen:g-victory", name: clip.name, source_vrm: clip.sourceVrm },
    payload_gz_b64: gzipSync(Buffer.from(JSON.stringify(clip))).toString("base64"),
  };
}

test.beforeEach(async ({ page }) => {
  await seedRandom(page);
});

test("signature moves: list, preview, upvote, compose", async ({ page }) => {
  const errors = collectPageErrors(page);
  await mockBackend(page);

  const listCalls: string[] = [];
  const clipRequests: Request[] = [];
  const votes: Array<{ id: string; body: unknown }> = [];
  const composes: unknown[] = [];

  // Registered after mockBackend → takes precedence over its catch-all.
  await page.route(`${API}/api/gestures**`, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    if (req.method() === "GET" && p === "/api/gestures") {
      const agent = url.searchParams.get("agent") ?? "";
      listCalls.push(agent);
      return route.fulfill({ json: { gestures: agent === AGENT ? GESTURES : [] } });
    }
    const clip = /^\/api\/gestures\/([^/]+)\/clip$/.exec(p);
    if (req.method() === "GET" && clip) {
      clipRequests.push(req);
      return route.fulfill({ json: clipPayload() });
    }
    const vote = /^\/api\/gestures\/([^/]+)\/vote$/.exec(p);
    if (req.method() === "POST" && vote) {
      const body = req.postDataJSON() as { up: boolean };
      votes.push({ id: decodeURIComponent(vote[1]), body });
      const g = GESTURES.find((x) => x.id === decodeURIComponent(vote[1]))!;
      return route.fulfill({ json: { gesture: { ...g, votes_up: g.votes_up + (body.up ? 1 : 0) } } });
    }
    if (req.method() === "POST" && p === "/api/gestures/generate") {
      composes.push(req.postDataJSON());
      return route.fulfill({
        json: { gesture: gesture("g-new", "Fresh Celebration", "state", "celebrating") },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "not_mocked" } });
  });

  await page.goto("/");
  await expect(page.getByText(AGENT).first()).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => listCalls).toContain(AGENT);

  // Open Bear's modal from the agent card list → Info tab.
  // Card name is "<emoji> Bear L3 tester rare"; the gallery tile is
  // "Pin Bear to the spotlight", so anchor on name + level + role.
  await page.getByRole("button", { name: new RegExp(`${AGENT} L\\d+ tester`) }).click();
  const modal = page.getByRole("dialog");
  await expect(modal).toBeVisible();
  await modal.getByRole("button", { name: "♪ Info" }).click();

  await expect(modal.getByText("Signature moves")).toBeVisible();
  await expect(modal.getByText("2 composed")).toBeVisible();
  await expect(modal.getByText("Victory Stomp")).toBeVisible();
  await expect(modal.getByText("Paw on Chin")).toBeVisible();
  await expect(modal.getByText("state:celebrating")).toBeVisible();

  // ▶ preview → the clip is fetched from the BACKEND origin (not the
  // Next server) and Bear takes the spotlight.
  await modal.getByRole("button", { name: "Preview Victory Stomp" }).click();
  await expect.poll(() => clipRequests.length, { timeout: 20_000 }).toBeGreaterThan(0);
  expect(new URL(clipRequests[0].url()).origin).toBe(API);
  expect(new URL(clipRequests[0].url()).pathname).toBe("/api/gestures/g-victory/clip");

  // 👍 → POST /api/gestures/g-victory/vote {up:true}; both vote buttons
  // for that move disable, the other move's stay live.
  await modal.getByRole("button", { name: "Upvote Victory Stomp" }).click();
  await expect.poll(() => votes).toEqual([{ id: "g-victory", body: { up: true } }]);
  await expect(modal.getByRole("button", { name: "Upvote Victory Stomp" })).toBeDisabled();
  await expect(modal.getByRole("button", { name: "Downvote Victory Stomp" })).toBeDisabled();
  await expect(modal.getByRole("button", { name: "Upvote Paw on Chin" })).toBeEnabled();
  await expect(modal.getByText("+3")).toBeVisible();

  // ✨ celebrate → POST /api/gestures/generate with the agent context.
  const listBefore = listCalls.filter((a) => a === AGENT).length;
  await modal.getByRole("button", { name: "✨ celebrate" }).click();
  await expect.poll(() => composes.length).toBe(1);
  expect(composes[0]).toEqual({
    agent: AGENT,
    kind: "state",
    value: "celebrating",
    force: true,
    role: "tester",
    mood: "determined",
  });
  // The hook refetches the agent's list after a successful compose.
  await expect
    .poll(() => listCalls.filter((a) => a === AGENT).length)
    .toBeGreaterThan(listBefore);

  // Close the modal: the spotlight now shows Bear (preview pinned it).
  await page.keyboard.press("Escape");
  await expect(modal).toBeHidden();
  await expect(page.getByRole("img", { name: new RegExp(`^${AGENT} — `) })).toBeVisible();

  expect(errors).toEqual([]);
});

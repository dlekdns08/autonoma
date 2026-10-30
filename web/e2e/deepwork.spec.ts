import path from "node:path";
import { expect, test } from "@playwright/test";
import { collectPageErrors, mockBackend, seedRandom } from "./support/mockBackend";

/**
 * Deep work (Claude Agent SDK delegation) as the stage sees it: lifecycle
 * toasts, the working pose, the ``tool: detail`` pill on the OBS feed,
 * files landing in the tree, and a refusal still reaching the viewer.
 * Everything is driven through the mock socket (``backend.send``).
 */

const HIDE_CANVAS = path.join(__dirname, "support", "hide-canvas.css");

test.beforeEach(async ({ page }) => {
  await seedRandom(page);
});

test("dashboard: lifecycle toasts, working state, new file, refusal", async ({ page }) => {
  const errors = collectPageErrors(page);
  const backend = await mockBackend(page);
  await page.goto("/");
  await expect(page.getByText("Bear").first()).toBeVisible({ timeout: 20_000 });

  // Bear starts "thinking" in the snapshot; deep work flips it to working.
  const bearTile = page.getByRole("button", { name: /^Pin Bear to the spotlight/ });
  await expect(bearTile.locator('[title="working"]')).toHaveCount(0);

  const toasts = page.getByRole("status", { name: "Notifications" });
  backend.send("deepwork.started", { agent: "Bear", task_id: "t3", title: "Write tests" });
  await expect(toasts.getByText('Bear is building "Write tests" for real...')).toBeVisible();
  await expect(bearTile.locator('[title="working"]')).toHaveCount(1);

  // Tool calls are intentionally not toasted (too chatty): they land in
  // the event log and keep the agent in the working pose.
  for (const [tool, detail] of [
    ["Read", "src/app.py"],
    ["Bash", "pytest -q tests/"],
    ["Write", "tests/test_bookmarks.py"],
  ] as const) {
    backend.send("agent.tool_use", { agent: "Bear", tool, detail });
  }
  await expect(page.getByText(/agent\.tool_use: .*pytest -q tests\//)).toBeVisible();
  await expect(toasts.getByText("pytest -q tests/")).toHaveCount(0);
  await expect(bearTile.locator('[title="working"]')).toHaveCount(1);

  backend.send("file.created", {
    agent: "Bear",
    path: "tests/test_bookmarks.py",
    size: 2048,
    description: "bookmark API tests",
  });
  await expect(page.getByText("tests/test_bookmarks.py", { exact: true })).toBeVisible();

  backend.send("deepwork.finished", {
    agent: "Bear",
    task_id: "t3",
    ok: true,
    files: 2,
    cost_usd: 0.12,
  });
  await expect(toasts.getByText("Deep Work Done")).toBeVisible();
  await expect(toasts.getByText("Bear: 2 files, $0.12")).toBeVisible();

  // Failure variant: error text + file count, no cost.
  backend.send("deepwork.started", { agent: "Midori", task_id: "t2", title: "Implement routes" });
  backend.send("deepwork.finished", {
    agent: "Midori",
    task_id: "t2",
    ok: false,
    files: 1,
    cost_usd: 0,
    error: "max turns reached",
  });
  await expect(toasts.getByText("Deep Work Incomplete")).toBeVisible();
  await expect(toasts.getByText("Midori: max turns reached (1 file)")).toBeVisible();

  // A refusal after all that still surfaces.
  backend.send("agent.refusal", { agent: "Bear", category: "cyber" });
  await expect(toasts.getByText("Bear declined that turn (cyber)")).toBeVisible();

  expect(errors).toEqual([]);
});

test("OBS: tool pill appears under the name tag and expires after ~3s", async ({ page }) => {
  const backend = await mockBackend(page);
  await page.goto("/obs?bg=green&chat=0&ticker=0");
  await expect(page.locator("canvas").first()).toBeVisible();
  // The spotlight defaults to the first agent (Director) until someone speaks.
  await expect(page.getByText("Director").first()).toBeVisible();

  backend.send("agent.tool_use", { agent: "Director", tool: "Bash", detail: "pytest -q tests/" });
  const pill = page.getByTitle("Bash pytest -q tests/");
  await expect(pill).toBeVisible();
  await expect(pill).toHaveText("⌘ Bash: pytest -q tests/");
  const shownAt = Date.now();

  // Gone after the 3s TTL — but not before ~2.5s.
  await expect(pill).toBeHidden({ timeout: 6_000 });
  expect(Date.now() - shownAt).toBeGreaterThan(2_000);

  // A tool call for an agent that isn't on stage doesn't show a pill.
  backend.send("agent.tool_use", { agent: "Bear", tool: "Read", detail: "src/app.py" });
  await page.waitForTimeout(500);
  await expect(page.getByText(/⌘ Read/)).toHaveCount(0);
});

test("OBS overlay with the tool pill matches baseline", async ({ page }) => {
  const backend = await mockBackend(page);
  await page.goto("/obs?bg=green&chat=0&ticker=0");
  await expect(page.locator("canvas").first()).toBeVisible();
  await expect(page.getByText("Director").first()).toBeVisible();

  // Keep the pill alive for the whole screenshot (it expires after 3s
  // and toHaveScreenshot waits for two identical frames). Re-sending the
  // same call renders identical pixels.
  const ping = () =>
    backend.send("agent.tool_use", {
      agent: "Director",
      tool: "Write",
      detail: "src/routes/bookmarks.py",
    });
  ping();
  const keepAlive = setInterval(ping, 1000);
  try {
    await expect(page.getByTitle("Write src/routes/bookmarks.py")).toBeVisible();
    await expect(page).toHaveScreenshot("obs-tool-pill.png", {
      stylePath: HIDE_CANVAS,
      maxDiffPixels: 150,
    });
  } finally {
    clearInterval(keepAlive);
  }
});

test("multiagent sub-threads appear as temporary helpers and leave", async ({ page }) => {
  const backend = await mockBackend(page);
  await page.goto("/");
  await expect(page.getByText("Midori").first()).toBeVisible({ timeout: 20_000 });
  backend.send("deepwork.subagent_started", {
    agent: "Midori", helper: "Test runner", thread_id: "th_0001", task_id: "t2",
  });
  // The helper shows up everywhere a cast member does: VTuber gallery
  // tile, pixel-stage sprite, and agent card.
  const helper = page.getByRole("button", { name: /Midori·Test runner·0001/ });
  await expect(helper).toHaveCount(3);
  await expect(page.getByRole("button", { name: /^Pin Midori·Test runner·0001/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Midori·Test runner·0001 — working/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /🧪 Midori·Test runner·0001/ })).toBeVisible();
  backend.send("deepwork.subagent_finished", {
    agent: "Midori", helper: "Test runner", thread_id: "th_0001", task_id: "t2",
  });
  await expect(page.getByRole("button", { name: /Midori·Test runner·0001/ })).toHaveCount(0);
});

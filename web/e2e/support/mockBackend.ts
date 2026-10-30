import type { Page, WebSocketRoute } from "@playwright/test";

/** The backend the app talks to by default (see ``useSwarm.getWsUrl``). */
const API_ORIGIN = "http://localhost:3479";
const WS_URL = "ws://localhost:3479/api/ws";

export interface MockAgent {
  name: string;
  emoji: string;
  role: string;
  color: string;
  mood: string;
  state: string;
  speech?: string;
  level?: number;
  position?: { x: number; y: number };
}

/** A fixed cast. Names are chosen so ``vrmFileForAgent`` spreads them
 *  across different catalog models. */
export const CAST: MockAgent[] = [
  { name: "Director", emoji: "👑", role: "director", color: "yellow", mood: "focused", state: "working" },
  { name: "Midori", emoji: "🦊", role: "coder", color: "cyan", mood: "happy", state: "talking", speech: "API 라우터 다 만들었어요!" },
  { name: "Bear", emoji: "🐻", role: "tester", color: "red", mood: "determined", state: "thinking" },
];

function agentPayload(a: MockAgent, i: number) {
  return {
    name: a.name,
    emoji: a.emoji,
    role: a.role,
    color: a.color,
    position: a.position ?? { x: 12 + i * 18, y: 8 },
    state: a.state,
    mood: a.mood,
    level: a.level ?? 3,
    xp: 40,
    xp_to_next: 100,
    species: "fox",
    species_emoji: a.emoji,
    rarity: "rare",
    catchphrase: "let's go",
    traits: ["bold", "creative"],
    stats: { focus: 7, speed: 5, creativity: 8, teamwork: 6 },
    speech: a.speech ?? "",
  };
}

export function snapshot(cast: MockAgent[] = CAST) {
  return {
    status: "running",
    project_name: "todo-api",
    goal: "A REST API for managing bookmarks with tags and search",
    round: 7,
    agents: cast.map(agentPayload),
    tasks: [
      { id: "t1", title: "Design schema", status: "done", assigned_to: "Midori" },
      { id: "t2", title: "Implement routes", status: "in_progress", assigned_to: "Midori" },
      { id: "t3", title: "Write tests", status: "assigned", assigned_to: "Bear" },
    ],
    files: [
      { path: "src/app.py", size: 1820, description: "FastAPI app", created_by: "Midori" },
      { path: "tests/test_app.py", size: 940, description: "tests", created_by: "Bear" },
    ],
    sky: "day",
    relationships: [{ from: "Midori", to: "Bear", trust: 0.8 }],
    final_answer: "",
    boss: null,
    cookies: [],
  };
}

export interface MockBackend {
  /** Push a server event to every connected client. */
  send(event: string, data: Record<string, unknown>): void;
  /** Commands the page sent over the socket. */
  received: Array<Record<string, unknown>>;
}

/**
 * Install HTTP + WebSocket mocks on ``page``. Call before ``page.goto``.
 * ``authenticated`` controls whether the socket greets with
 * ``auth.success`` (stage visible) or ``auth.status`` (login prompt).
 */
export async function mockBackend(
  page: Page,
  opts: { authenticated?: boolean; cast?: MockAgent[] } = {},
): Promise<MockBackend> {
  const authenticated = opts.authenticated ?? true;
  const sockets: WebSocketRoute[] = [];
  const received: Array<Record<string, unknown>> = [];

  // HTTP API: nothing is persisted, so empty collections are the honest
  // answer. ``/api/auth/me`` reports an anonymous viewer.
  await page.route(`${API_ORIGIN}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      if (!authenticated) {
        return route.fulfill({ status: 401, json: { detail: "not_authenticated" } });
      }
      return route.fulfill({
        json: {
          user: {
            id: "u-e2e",
            username: "viewer",
            role: "user",
            status: "active",
            created_at: "2026-10-01T00:00:00Z",
            updated_at: "2026-10-01T00:00:00Z",
          },
        },
      });
    }
    if (url.pathname === "/api/mocap/triggers") {
      return route.fulfill({
        json: { kinds: [], mood: [], emote: [], state: [], max_clip_duration_s: 60 },
      });
    }
    if (url.pathname === "/api/mocap-bindings") return route.fulfill({ json: { bindings: [] } });
    // Object endpoints for things that don't exist yet answer 404, like a
    // fresh backend does (the pages handle that path explicitly).
    if (/^\/api\/agents\/[^/]+\/profile$/.test(url.pathname)) {
      return route.fulfill({ status: 404, json: { detail: "character_not_found" } });
    }
    return route.fulfill({ json: [] });
  });

  // Same-origin font/analytics noise is fine; external CDNs are not
  // reachable in CI, so fail them fast instead of waiting on timeouts.
  await page.route(/^https:\/\/(?!127\.0\.0\.1).*/, (route) => route.abort());

  await page.routeWebSocket(WS_URL, (ws) => {
    sockets.push(ws);
    ws.onMessage((raw) => {
      try {
        received.push(JSON.parse(String(raw)));
      } catch {
        /* ignore non-JSON */
      }
    });
    if (authenticated) {
      ws.send(
        JSON.stringify({
          event: "auth.success",
          data: { is_admin: false, provider: "anthropic", model: "claude-sonnet-5-5" },
        }),
      );
      ws.send(JSON.stringify({ event: "snapshot", data: snapshot(opts.cast) }));
    } else {
      ws.send(
        JSON.stringify({
          event: "auth.status",
          data: { has_admin: true, server_provider: "anthropic", server_model: "claude-sonnet-5-5" },
        }),
      );
    }
  });

  return {
    received,
    send(event, data) {
      for (const ws of sockets) ws.send(JSON.stringify({ event, data }));
    },
  };
}

/** Make ``Math.random`` deterministic so gesture/blink schedulers and
 *  procedural placements repeat across runs. */
export async function seedRandom(page: Page, seed = 42): Promise<void> {
  await page.addInitScript((s) => {
    let state = s >>> 0;
    Math.random = () => {
      state = (state + 0x6d2b79f5) >>> 0;
      let t = state;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }, seed);
}

/** Collect uncaught page errors (React crashes, failed dynamic imports). */
export function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  return errors;
}

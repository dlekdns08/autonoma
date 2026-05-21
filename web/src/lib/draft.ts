/**
 * Viewer Fantasy Draft — typed bindings for the backend draft endpoints.
 *
 *   GET  /api/sessions/{session_id}/draft/agents
 *   POST /api/sessions/{session_id}/draft     { picks: [name, name, name] }
 *   GET  /api/sessions/{session_id}/draft/scoreboard
 *
 * The helpers all send ``credentials: "include"`` so the auth cookie
 * travels with the request. Each helper is defensive about response
 * shape: the backend's documented payload may be wrapped in
 * ``{rows: …}`` / ``{agents: …}`` but we also tolerate a bare array
 * shape in case a future revision flattens it.
 */

import { API_BASE_URL } from "@/hooks/useSwarm";

export interface DraftAgent {
  name: string;
  emoji: string;
  role: string;
  mood: string;
}

export interface ScoreboardRow {
  viewer_name: string;
  picks: string[];
  score: number;
}

export interface ScoreboardResponse {
  rows: ScoreboardRow[];
  my_rank: number | null;
  /** The caller's own currently-submitted picks, if any. ``null`` when
   *  they haven't drafted yet — the modal uses this to pre-check
   *  checkboxes on re-open without an extra fetch. */
  my_picks: string[] | null;
}

export class DraftApiError extends Error {
  status: number;
  code: string;
  constructor(message: string, status: number, code = "") {
    super(message);
    this.status = status;
    this.code = code;
    this.name = "DraftApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    headers: init?.body
      ? { "Content-Type": "application/json", ...(init?.headers ?? {}) }
      : init?.headers,
    ...init,
  });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      // body wasn't JSON
    }
    let message = `HTTP ${res.status}`;
    let code = "";
    if (detail && typeof detail === "object" && "detail" in detail) {
      const d = (detail as { detail: unknown }).detail;
      if (typeof d === "string") {
        message = d;
      } else if (d && typeof d === "object") {
        const obj = d as { code?: unknown; message?: unknown };
        if (typeof obj.message === "string") message = obj.message;
        if (typeof obj.code === "string") code = obj.code;
      }
    }
    throw new DraftApiError(message, res.status, code);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

/** Defensive picker for the ``agents`` field — accepts a wrapper, a
 *  bare array, or anything weirder by falling back to an empty list.
 *  Every field on a row is coerced to a string so a malformed row
 *  doesn't crash the picker UI. */
function coerceAgents(raw: unknown): DraftAgent[] {
  let arr: unknown = null;
  if (Array.isArray(raw)) {
    arr = raw;
  } else if (raw && typeof raw === "object") {
    const w = raw as { agents?: unknown };
    if (Array.isArray(w.agents)) arr = w.agents;
  }
  if (!Array.isArray(arr)) return [];
  const out: DraftAgent[] = [];
  for (const entry of arr) {
    if (!entry || typeof entry !== "object") continue;
    const o = entry as Record<string, unknown>;
    const name = typeof o.name === "string" ? o.name : "";
    if (!name) continue;
    out.push({
      name,
      emoji: typeof o.emoji === "string" ? o.emoji : "",
      role: typeof o.role === "string" ? o.role : "",
      mood: typeof o.mood === "string" ? o.mood : "",
    });
  }
  return out;
}

function coerceRows(raw: unknown): ScoreboardRow[] {
  let arr: unknown = null;
  if (Array.isArray(raw)) {
    arr = raw;
  } else if (raw && typeof raw === "object") {
    const w = raw as { rows?: unknown };
    if (Array.isArray(w.rows)) arr = w.rows;
  }
  if (!Array.isArray(arr)) return [];
  const out: ScoreboardRow[] = [];
  for (const entry of arr) {
    if (!entry || typeof entry !== "object") continue;
    const o = entry as Record<string, unknown>;
    const picksRaw = o.picks;
    const picks = Array.isArray(picksRaw)
      ? picksRaw.filter((x): x is string => typeof x === "string")
      : [];
    out.push({
      viewer_name: typeof o.viewer_name === "string" ? o.viewer_name : "viewer",
      picks,
      score: typeof o.score === "number" && Number.isFinite(o.score) ? o.score : 0,
    });
  }
  return out;
}

export async function fetchDraftAgents(sessionId: number): Promise<DraftAgent[]> {
  const body = await request<unknown>(
    `/api/sessions/${encodeURIComponent(String(sessionId))}/draft/agents`,
  );
  return coerceAgents(body);
}

export async function submitDraft(
  sessionId: number,
  picks: string[],
): Promise<{ picks: string[] }> {
  const body = await request<unknown>(
    `/api/sessions/${encodeURIComponent(String(sessionId))}/draft`,
    {
      method: "POST",
      body: JSON.stringify({ picks }),
    },
  );
  // The backend wraps the row in ``{status, draft: {picks, …}}``. Fall
  // back to whatever the caller passed in if the response shape drifts.
  if (body && typeof body === "object" && "draft" in body) {
    const draft = (body as { draft?: unknown }).draft;
    if (draft && typeof draft === "object") {
      const o = draft as { picks?: unknown };
      if (Array.isArray(o.picks)) {
        return {
          picks: o.picks.filter((x): x is string => typeof x === "string"),
        };
      }
    }
  }
  return { picks: Array.isArray(picks) ? [...picks] : [] };
}

export async function fetchScoreboard(sessionId: number): Promise<ScoreboardResponse> {
  const body = await request<unknown>(
    `/api/sessions/${encodeURIComponent(String(sessionId))}/draft/scoreboard`,
  );
  const rows = coerceRows(body);
  let myRank: number | null = null;
  let myPicks: string[] | null = null;
  if (body && typeof body === "object") {
    const w = body as { my_rank?: unknown; my_picks?: unknown };
    if (typeof w.my_rank === "number" && Number.isFinite(w.my_rank)) {
      myRank = w.my_rank;
    }
    if (Array.isArray(w.my_picks)) {
      myPicks = w.my_picks.filter((x): x is string => typeof x === "string");
    }
  }
  return { rows, my_rank: myRank, my_picks: myPicks };
}

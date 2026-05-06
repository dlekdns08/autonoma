/**
 * Live-share — typed bindings for the public discovery + visibility
 * endpoints mounted under ``/api/live-share/*``.
 *
 *   GET  /api/live-share/sessions              -> {count, sessions: LiveSession[]}
 *   GET  /api/live-share/sessions/{code}       -> {session: LiveSession} | 404
 *   POST /api/live-share/visibility            -> {session: LiveSession}
 *
 * Convention mirrors ``lib/quests.ts`` and ``lib/viewerBettingApi.ts``:
 *  - Every request is sent with ``credentials: "include"`` so the
 *    cookie session travels along (only the visibility POST actually
 *    needs auth — the GET endpoints are public — but keeping the
 *    behaviour uniform avoids one-off footguns).
 *  - Backend ``detail`` strings are surfaced as ``LiveShareApiError``
 *    so the UI can render the server's message verbatim.
 */

import { API_BASE_URL } from "@/hooks/useSwarm";

/** Wire shape returned by the live-share endpoints. Keep this in lock-
 *  step with the backend's ``LiveSession`` payload — the frontend reads
 *  every field directly without remapping. */
export interface LiveSession {
  room_code: string;
  room_id: number;
  title: string;
  description: string;
  goal: string;
  host_display_name: string;
  host_user_id: string;
  viewer_count: number;
  agent_count: number;
  round_number: number;
  /** Unix seconds — convert to ms before passing to ``Date``. */
  started_at: number;
  agents: { name: string; emoji: string; role: string; mood: string }[];
  is_public: boolean;
}

/** Body for ``POST /api/live-share/visibility``. ``title`` and
 *  ``description`` are optional in the contract; when omitted the
 *  backend keeps whatever it has on file. */
export interface VisibilityPayload {
  public: boolean;
  title?: string;
  description?: string;
}

/** Thrown for any non-2xx response from a live-share endpoint.
 *  Modelled after ``BettingApiError`` so callers can branch on the
 *  ``status`` code (e.g. swallow 404 from sessions/{code}). */
export class LiveShareApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "LiveShareApiError";
    this.status = status;
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
      // Body wasn't JSON — fall through to status text below.
    }
    let message = `HTTP ${res.status}`;
    if (
      detail &&
      typeof detail === "object" &&
      "detail" in detail &&
      typeof (detail as { detail?: unknown }).detail === "string"
    ) {
      message = (detail as { detail: string }).detail;
    }
    throw new LiveShareApiError(message, res.status);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

/** List every public live session. Sorted/served by the backend; the
 *  client doesn't re-sort. */
export async function fetchLiveSessions(): Promise<LiveSession[]> {
  const body = await request<unknown>(`/api/live-share/sessions`);
  if (Array.isArray(body)) return body as LiveSession[];
  if (
    body &&
    typeof body === "object" &&
    Array.isArray((body as { sessions?: unknown }).sessions)
  ) {
    return (body as { sessions: LiveSession[] }).sessions;
  }
  return [];
}

/** Resolve a single session by its short room code. Returns null on
 *  404 — the backend deliberately conflates "unknown code" with
 *  "private room", so callers that just want to render a landing page
 *  can fall back to a generic empty state without distinguishing. */
export async function fetchSessionByCode(
  code: string,
): Promise<LiveSession | null> {
  try {
    const body = await request<{ session: LiveSession }>(
      `/api/live-share/sessions/${encodeURIComponent(code)}`,
    );
    return body.session;
  } catch (err) {
    if (err instanceof LiveShareApiError && err.status === 404) {
      return null;
    }
    throw err;
  }
}

/** Toggle the caller's currently-hosted room between public/private,
 *  optionally updating the title/description in the same call. The
 *  backend resolves "the caller's room" from the cookie session, so
 *  there's no room id in the URL. 404 here means the caller doesn't
 *  currently own a live room. */
export async function setVisibility(
  payload: VisibilityPayload,
): Promise<LiveSession> {
  const body = await request<{ session: LiveSession }>(
    `/api/live-share/visibility`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
  return body.session;
}

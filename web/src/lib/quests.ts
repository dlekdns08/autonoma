/**
 * Feature #14 — Live quest designer.
 *
 * Typed bindings for the backend quest endpoints + WS event payloads.
 * The HTTP routes are documented on the backend contract:
 *
 *   POST   /api/quests/propose          { session_id, text } -> 201 {quest_id}
 *   POST   /api/quests/{id}/vote        -> {votes}            (409 if already voted)
 *   GET    /api/quests?session_id=&status=
 *   POST   /api/quests/{id}/activate    (admin)
 *   POST   /api/quests/{id}/complete    (admin)
 *
 * All helpers send ``credentials: "include"`` so the auth cookie travels
 * with the request — the dashboard pages already mount under the same
 * origin as the API in dev (and the cookie domain spans the prod hosts).
 */

import { API_BASE_URL } from "@/hooks/useSwarm";

/** Lifecycle states tracked on the server. ``proposed`` quests are open
 *  for voting; ``active`` quests are the round-bound goal; ``completed``
 *  is terminal. ``rejected`` is reserved (admin moderation) — included so
 *  the union matches whatever the API may emit without a runtime cast. */
export type QuestStatus =
  | "proposed"
  | "active"
  | "completed"
  | "rejected";

/** Shape returned by ``GET /api/quests``. ``activated_round`` and
 *  ``completed_round`` are only populated once the corresponding admin
 *  action fires, so they are nullable on proposed rows. */
export interface Quest {
  id: number;
  session_id: number;
  text: string;
  votes: number;
  status: QuestStatus;
  created_at: string;
  activated_round: number | null;
  completed_round: number | null;
}

/** Discrete WS events surfaced from the swarm bus that mutate quest
 *  state. The backend dispatches additional bookkeeping events (e.g.
 *  ``quest.voted``); we model only the three the panel actually consumes
 *  so the type stays narrow and exhaustive ``switch`` blocks light up
 *  unhandled cases at compile time. */
export type QuestEvent =
  | {
      type: "quest.proposed";
      data: {
        quest_id: number;
        session_id: number;
        text: string;
        votes?: number;
        status?: QuestStatus;
        created_at?: string;
      };
    }
  | {
      type: "quest.activated";
      data: {
        quest_id: number;
        session_id?: number;
        activated_round?: number;
      };
    }
  | {
      type: "quest.completed";
      data: {
        quest_id: number;
        session_id?: number;
        completed_round?: number;
      };
    };

/** Common fetch wrapper — keeps cookie credentials on every request and
 *  surfaces backend ``detail`` payloads as Error messages so callers can
 *  forward them to the UI without re-parsing. ``status`` is preserved on
 *  the thrown error so vote() can detect 409 (already voted). */
export class QuestApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "QuestApiError";
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
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
      // Body wasn't JSON — fall back to status text below.
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
    throw new QuestApiError(message, res.status);
  }
  // 204 has no body — caller should pass ``T = void`` in that case.
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export async function proposeQuest(
  sessionId: number,
  text: string,
): Promise<{ quest_id: number }> {
  return request<{ quest_id: number }>(`/api/quests/propose`, {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, text }),
  });
}

export async function voteQuest(
  questId: number,
): Promise<{ votes: number }> {
  return request<{ votes: number }>(`/api/quests/${questId}/vote`, {
    method: "POST",
  });
}

export async function listQuests(
  sessionId: number,
  status?: QuestStatus,
): Promise<Quest[]> {
  const params = new URLSearchParams({ session_id: String(sessionId) });
  if (status) params.set("status", status);
  return request<Quest[]>(`/api/quests?${params.toString()}`);
}

export async function activateQuest(questId: number): Promise<void> {
  await request<void>(`/api/quests/${questId}/activate`, {
    method: "POST",
  });
}

export async function completeQuest(questId: number): Promise<void> {
  await request<void>(`/api/quests/${questId}/complete`, {
    method: "POST",
  });
}

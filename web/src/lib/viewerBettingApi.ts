/**
 * Feature #4 — Viewer betting backend client.
 *
 * Sibling of `viewerBetting.ts` (the localStorage demo). Same domain
 * vocabulary, but every mutation hits the FastAPI router at
 * `/api/betting/*`. Use this module — and the `useViewerBettingLive`
 * hook — once the operator has flipped `AUTONOMA_VIEWER_BETTING_ENABLED`.
 *
 * Disabled-flag handling: every endpoint returns 503 with
 * `{detail: {code: "betting_disabled"}}` when the feature is off. This
 * client surfaces that as the typed error `{code: "betting_disabled"}`
 * so the UI can render a "feature off" state without a generic toast.
 */

const API_BASE_URL =
  (typeof process !== "undefined" && process.env?.NEXT_PUBLIC_API_URL) || "";

// ---------------------------------------------------------------------------
// Wire types — mirror the FastAPI response payloads in
// src/autonoma/routers/viewer_betting.py.
// ---------------------------------------------------------------------------

export interface ApiMarket {
  id: number;
  session_id: number;
  market_id: string;
  question: string;
  status: "open" | "locked" | "resolved" | "cancelled";
  closes_at_round: number;
  opened_at: string;
  resolved_at: string | null;
  winning_option: string;
}

export interface ApiEntry {
  id: number;
  market_id: string;
  session_id: number;
  viewer_id: string;
  display_name: string;
  option: string;
  stake: number;
  placed_at: string;
  payout: number;
}

export interface ApiResolveSummary {
  market_id: string;
  winning_option: string;
  total_stake: number;
  total_payout: number;
  winners: number;
  losers: number;
}

export interface ApiLeaderboardRow {
  viewer_id: string;
  display_name: string;
  net: number;
  bets: number;
}

export class BettingApiError extends Error {
  readonly code: string;
  readonly status: number;
  constructor(code: string, status: number, message: string) {
    super(message);
    this.name = "BettingApiError";
    this.code = code;
    this.status = status;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const res = await fetch(url, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    ...init,
  });
  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
  let code = `http_${res.status}`;
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as { detail?: { code?: string; message?: string } };
    if (body?.detail?.code) code = body.detail.code;
    if (body?.detail?.message) message = body.detail.message;
  } catch {
    // body wasn't JSON — keep the defaults
  }
  throw new BettingApiError(code, res.status, message);
}

// ---------------------------------------------------------------------------
// Public client surface
// ---------------------------------------------------------------------------

export async function listOpenMarkets(sessionId: number): Promise<ApiMarket[]> {
  const body = await request<{ markets: ApiMarket[] }>(
    `/api/betting/markets?session_id=${sessionId}`,
  );
  return body.markets ?? [];
}

export async function placeBet(
  marketId: string,
  sessionId: number,
  option: string,
  stake: number,
): Promise<ApiEntry> {
  const body = await request<{ entry: ApiEntry }>(
    `/api/betting/markets/${encodeURIComponent(marketId)}/bet?session_id=${sessionId}`,
    { method: "POST", body: JSON.stringify({ option, stake }) },
  );
  return body.entry;
}

export async function fetchBalance(
  sessionId: number,
): Promise<{ viewerId: string; balance: number }> {
  const body = await request<{ viewer_id: string; balance: number }>(
    `/api/betting/balance?session_id=${sessionId}`,
  );
  return { viewerId: body.viewer_id, balance: body.balance };
}

export async function fetchLeaderboard(
  sessionId: number,
  limit = 20,
): Promise<ApiLeaderboardRow[]> {
  const body = await request<{ leaderboard: ApiLeaderboardRow[] }>(
    `/api/betting/leaderboard?session_id=${sessionId}&limit=${limit}`,
  );
  return body.leaderboard ?? [];
}

export async function adminOpenMarket(
  sessionId: number,
  marketId: string,
  question: string,
  closesAtRound: number,
): Promise<ApiMarket> {
  const body = await request<{ market: ApiMarket }>(
    `/api/betting/markets`,
    {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        market_id: marketId,
        question,
        closes_at_round: closesAtRound,
      }),
    },
  );
  return body.market;
}

export async function adminResolveMarket(
  sessionId: number,
  marketId: string,
  winningOption: string,
): Promise<ApiResolveSummary> {
  const body = await request<{ summary: ApiResolveSummary }>(
    `/api/betting/markets/${encodeURIComponent(marketId)}/resolve?session_id=${sessionId}`,
    {
      method: "POST",
      body: JSON.stringify({ winning_option: winningOption }),
    },
  );
  return body.summary;
}

export async function adminLockMarket(
  sessionId: number,
  marketId: string,
): Promise<void> {
  await request(
    `/api/betting/markets/${encodeURIComponent(marketId)}/lock?session_id=${sessionId}`,
    { method: "POST" },
  );
}

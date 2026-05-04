/**
 * Typed fetch helpers for Feature #19 — A/B preset comparison.
 *
 * Wraps the two backend endpoints used by the /admin/ab-compare page:
 *   - GET  /api/ab/recent-runs?limit=N
 *   - POST /api/ab/compare  body { session_a, session_b }
 *
 * Both endpoints rely on the cookie session, so every request uses
 * `credentials: "include"`. Errors are surfaced with informative messages
 * (HTTP status + best-effort body excerpt) so the page can render them
 * verbatim.
 */

import { API_BASE_URL } from "@/hooks/useSwarm";

// ── Wire types ──────────────────────────────────────────────────────────────

/** One row from /api/ab/recent-runs. */
export interface RunSummaryRow {
  id: number;
  session_id: number;
  goal: string;
  completed_at: string;
  agent_count: number;
  task_count: number;
  tasks_done: number;
  total_rounds: number;
  llm_calls: number;
  preset_id: string | null;
  policy_hash: string | null;
}

/** Per-session aggregate inside an ABReport. */
export interface ABRunSummary {
  session_id: number;
  goal: string;
  completed_at: string;
  agent_count: number;
  task_count: number;
  tasks_done: number;
  total_rounds: number;
  llm_calls: number;
  preset_id: string | null;
  policy_hash: string | null;
  /** Backend may include extra derived metrics; keep them addressable. */
  [key: string]: unknown;
}

/** Numeric deltas (B − A or B / A — defined by backend). */
export interface ABDeltas {
  tasks_done_pct: number;
  rounds_to_goal: number;
  llm_calls_per_round: number;
  /** Backend may add more delta keys over time. */
  [key: string]: number;
}

/** Per-anomaly-kind counts for each session. */
export interface ABAnomalyCounts {
  /** key = anomaly kind, value = { a: count, b: count } */
  [kind: string]: { a: number; b: number };
}

/** The full report returned by POST /api/ab/compare. */
export interface ABReport {
  session_a: number;
  session_b: number;
  summary_a: ABRunSummary;
  summary_b: ABRunSummary;
  deltas: ABDeltas;
  anomaly_counts: ABAnomalyCounts;
  winner: "a" | "b" | "tie" | string;
}

// ── Response shapes ─────────────────────────────────────────────────────────

interface RecentRunsResponse {
  count: number;
  runs: RunSummaryRow[];
}

// ── Helpers ─────────────────────────────────────────────────────────────────

async function readErrorMessage(res: Response): Promise<string> {
  let detail = "";
  try {
    const text = await res.text();
    if (text) {
      // Prefer JSON `detail` / `error` fields; fall back to raw text.
      try {
        const parsed = JSON.parse(text) as Record<string, unknown>;
        const candidate = parsed.detail ?? parsed.error ?? parsed.message;
        detail =
          typeof candidate === "string" ? candidate : JSON.stringify(parsed);
      } catch {
        detail = text;
      }
    }
  } catch {
    // ignore — we'll just report the status
  }
  const trimmed = detail.length > 200 ? `${detail.slice(0, 200)}…` : detail;
  return trimmed
    ? `HTTP ${res.status} ${res.statusText}: ${trimmed}`
    : `HTTP ${res.status} ${res.statusText}`;
}

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Fetch recent completed runs for the A/B picker.
 *
 * @param limit  Optional row cap (backend default applies when omitted).
 * @returns      Array of run summaries (already sorted by the backend).
 * @throws       Error with HTTP status + body excerpt on non-2xx responses.
 */
export async function fetchRecentRuns(
  limit?: number,
): Promise<RunSummaryRow[]> {
  const qs =
    typeof limit === "number" && Number.isFinite(limit)
      ? `?limit=${encodeURIComponent(String(limit))}`
      : "";
  const url = `${API_BASE_URL}/api/ab/recent-runs${qs}`;

  let res: Response;
  try {
    res = await fetch(url, {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(`recent-runs 요청 실패 (network): ${msg}`);
  }

  if (!res.ok) {
    throw new Error(await readErrorMessage(res));
  }

  const data = (await res.json()) as RecentRunsResponse;
  return Array.isArray(data?.runs) ? data.runs : [];
}

/**
 * POST two session ids to /api/ab/compare and return the full report.
 *
 * @param a  session_id of run A
 * @param b  session_id of run B
 * @throws   Error with HTTP status + body excerpt on non-2xx responses.
 */
export async function compareRuns(a: number, b: number): Promise<ABReport> {
  const url = `${API_BASE_URL}/api/ab/compare`;

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ session_a: a, session_b: b }),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(`compare 요청 실패 (network): ${msg}`);
  }

  if (!res.ok) {
    throw new Error(await readErrorMessage(res));
  }

  return (await res.json()) as ABReport;
}

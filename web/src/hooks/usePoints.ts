"use client";

/**
 * Channel-points economy — viewer wallet hook.
 *
 * Mirrors the backend ``/api/points/*`` endpoints with a tiny state
 * model:
 *
 *   - ``balance``     — last-known integer balance, polled every 15s.
 *   - ``heartbeat``   — sends ``POST /api/points/heartbeat`` once every
 *                       60s while the page is *visible* (Page Visibility
 *                       API). The server rate-limits anyway, but skipping
 *                       hidden tabs saves a network round-trip every
 *                       minute the viewer is away.
 *   - ``spendCookie`` — calls ``POST /api/points/spend/cookie`` and
 *                       returns the new balance on success. On 402
 *                       (``insufficient_balance``) we throw an Error
 *                       whose ``code`` field is set so the picker UI
 *                       can show an inline message without parsing the
 *                       message string.
 *
 * The hook accepts a nullable ``sessionId`` so it can mount on pages
 * (``/watch/[code]``) where the session id only resolves after the WS
 * hello — we no-op every network action until it's a positive integer.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

const POLL_INTERVAL_MS = 15_000;
const HEARTBEAT_INTERVAL_MS = 60_000;

export class PointsApiError extends Error {
  status: number;
  code: string;
  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = "PointsApiError";
    this.status = status;
    this.code = code;
  }
}

async function jsonRequest<T>(
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
    let code = `http_${res.status}`;
    let message = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as {
        detail?: { code?: string; message?: string } | string;
      };
      if (typeof body.detail === "object" && body.detail !== null) {
        if (typeof body.detail.code === "string") code = body.detail.code;
        if (typeof body.detail.message === "string") message = body.detail.message;
      } else if (typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      // non-JSON body — keep the http_<status> placeholder.
    }
    throw new PointsApiError(message, res.status, code);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export interface UsePointsResult {
  balance: number | null;
  refresh: () => Promise<void>;
  spendCookie: (agentName: string) => Promise<number>;
  /** Last error string for surface UI (cleared on next successful call). */
  error: string | null;
}

export interface UsePointsOptions {
  /** When false, polling + heartbeat are paused. Default: true. */
  enabled?: boolean;
}

export function usePoints(
  sessionId: number | null,
  opts?: UsePointsOptions,
): UsePointsResult {
  const enabled = opts?.enabled ?? true;
  const [balance, setBalance] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sessionRef = useRef<number | null>(sessionId);
  useEffect(() => {
    sessionRef.current = sessionId;
  }, [sessionId]);

  const refresh = useCallback(async () => {
    try {
      const data = await jsonRequest<{ balance: number }>("/api/points/balance");
      setBalance(typeof data.balance === "number" ? data.balance : 0);
      setError(null);
    } catch (err) {
      // Soft-fail: keep the last known balance on screen; only clear on
      // next success. This avoids the chip blinking to "—" every poll
      // during a transient network blip.
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // ── Polling balance every 15s ─────────────────────────────────────
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void refresh();
    const id = window.setInterval(() => {
      if (!cancelled) void refresh();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [enabled, refresh]);

  // ── Heartbeat every 60s while visible ─────────────────────────────
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const fireHeartbeat = async () => {
      const sid = sessionRef.current;
      if (typeof sid !== "number" || sid <= 0) return;
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const data = await jsonRequest<{ balance: number; granted: boolean }>(
          "/api/points/heartbeat",
          {
            method: "POST",
            body: JSON.stringify({ session_id: sid }),
          },
        );
        if (cancelled) return;
        if (typeof data.balance === "number") setBalance(data.balance);
      } catch (err) {
        if (cancelled) return;
        // Don't surface a heartbeat failure to the user-visible error
        // state — it's noisy and recovers on the next tick.
        // eslint-disable-next-line no-console
        console.debug("[points] heartbeat failed", err);
      }
    };

    // Kick one immediately so the first 60s of viewing isn't a freebie
    // window for the user but also isn't a 60s wait for any feedback.
    void fireHeartbeat();

    const id = window.setInterval(() => {
      if (!cancelled) void fireHeartbeat();
    }, HEARTBEAT_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [enabled]);

  const spendCookie = useCallback(
    async (agentName: string): Promise<number> => {
      const sid = sessionRef.current;
      if (typeof sid !== "number" || sid <= 0) {
        throw new PointsApiError("no active session", 409, "no_session");
      }
      const trimmed = agentName.trim();
      if (!trimmed) {
        throw new PointsApiError("agent_name required", 400, "agent_required");
      }
      try {
        const data = await jsonRequest<{ balance: number; agent: string }>(
          "/api/points/spend/cookie",
          {
            method: "POST",
            body: JSON.stringify({ session_id: sid, agent_name: trimmed }),
          },
        );
        if (typeof data.balance === "number") setBalance(data.balance);
        setError(null);
        return data.balance ?? 0;
      } catch (err) {
        const msg =
          err instanceof PointsApiError ? err.message : String(err);
        setError(msg);
        throw err;
      }
    },
    [],
  );

  return { balance, refresh, spendCookie, error };
}

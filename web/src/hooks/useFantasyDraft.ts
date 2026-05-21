"use client";

/**
 * Viewer Fantasy Draft hook.
 *
 * Wraps the three backend endpoints with a single state model that the
 * draft modal (and the watch-page rank chip) can drive. Owns:
 *
 *   1. The cached agent roster + scoreboard for the active session.
 *   2. A 5-second polling loop for the scoreboard so the rank chip on
 *      the kiosk page stays current without a WS subscription.
 *   3. A ``submit`` helper that POSTs the 3-agent roster and optimistically
 *      seeds the scoreboard with the caller's picks so the modal can
 *      close immediately.
 *
 * The hook is defensive in two places that historically bite this app:
 *   - every fetch result is normalised in ``@/lib/draft`` with explicit
 *     ``Array.isArray`` guards so a backend revision returning a bare
 *     array (or no array at all) can't blow up render;
 *   - the polling timer is restarted on ``sessionId`` change so cached
 *     rows from a previous session can never leak into the new one.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DraftApiError,
  fetchDraftAgents,
  fetchScoreboard,
  submitDraft,
  type DraftAgent,
  type ScoreboardResponse,
  type ScoreboardRow,
} from "@/lib/draft";

const SCOREBOARD_POLL_MS = 5_000;

export interface UseFantasyDraftResult {
  /** Agents available for picking in this session. Empty array until
   *  the first ``refreshAgents`` returns. */
  agents: DraftAgent[];
  /** Scoreboard rows ordered by score descending. */
  rows: ScoreboardRow[];
  /** 1-indexed rank of the calling viewer, or ``null`` if they haven't
   *  submitted a draft yet. */
  myRank: number | null;
  /** The caller's own picks, if they've submitted a roster. */
  myPicks: string[] | null;
  /** Last error message, if any. Cleared by every successful action. */
  error: string | null;
  /** ``true`` while a submit is in flight. */
  submitting: boolean;
  /** Manually trigger an agent-roster refresh. */
  refreshAgents: () => Promise<void>;
  /** Manually trigger a scoreboard refresh. */
  refreshScoreboard: () => Promise<void>;
  /** POST a 3-agent roster. Throws on validation/server error so the
   *  modal can decide whether to close. */
  submit: (picks: string[]) => Promise<void>;
}

export interface UseFantasyDraftOptions {
  /** When ``false`` the polling loop is suspended. The hook still
   *  exposes its cached state so the rank chip on the kiosk page can
   *  read it after an explicit refresh. */
  enabled?: boolean;
}

export function useFantasyDraft(
  sessionId: number | null,
  opts?: UseFantasyDraftOptions,
): UseFantasyDraftResult {
  const enabled = opts?.enabled ?? true;
  const [agents, setAgents] = useState<DraftAgent[]>([]);
  const [board, setBoard] = useState<ScoreboardResponse>({
    rows: [],
    my_rank: null,
    my_picks: null,
  });
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Latch session id into a ref so callbacks (which the consumer may
  // pass to children that mount once) always see the live value
  // without forcing a re-render. The ref is updated from an effect so
  // we don't write during render — required by React 19's hooks rules.
  const sessionRef = useRef<number | null>(sessionId);
  useEffect(() => {
    sessionRef.current = sessionId;
  }, [sessionId]);

  const refreshAgents = useCallback(async () => {
    const sid = sessionRef.current;
    if (sid === null || sid <= 0) {
      setAgents([]);
      return;
    }
    try {
      const next = await fetchDraftAgents(sid);
      setAgents(Array.isArray(next) ? next : []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const refreshScoreboard = useCallback(async () => {
    const sid = sessionRef.current;
    if (sid === null || sid <= 0) {
      setBoard({ rows: [], my_rank: null, my_picks: null });
      return;
    }
    try {
      const next = await fetchScoreboard(sid);
      setBoard({
        rows: Array.isArray(next.rows) ? next.rows : [],
        my_rank:
          typeof next.my_rank === "number" && Number.isFinite(next.my_rank)
            ? next.my_rank
            : null,
        my_picks: Array.isArray(next.my_picks) ? next.my_picks : null,
      });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // Initial scoreboard fetch + 5s polling. We restart the loop when
  // ``sessionId`` or ``enabled`` flips so cached rows can't outlive a
  // session change.
  useEffect(() => {
    if (!enabled) return;
    if (sessionId === null || sessionId <= 0) return;
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await refreshScoreboard();
    })();
    const id = window.setInterval(() => {
      if (!cancelled) void refreshScoreboard();
    }, SCOREBOARD_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [enabled, sessionId, refreshScoreboard]);

  const submit = useCallback(
    async (picks: string[]) => {
      const sid = sessionRef.current;
      if (sid === null || sid <= 0) {
        const msg = "No live session — wait for the host to start.";
        setError(msg);
        throw new DraftApiError(msg, 0, "no_session");
      }
      const safe = Array.isArray(picks) ? picks.slice() : [];
      setSubmitting(true);
      try {
        const { picks: confirmed } = await submitDraft(sid, safe);
        // Optimistically reflect the picks in local state so the modal
        // can close immediately. The next scoreboard poll will append
        // the row if it wasn't already there.
        setBoard((prev) => ({
          rows: prev.rows,
          my_rank: prev.my_rank,
          my_picks: confirmed,
        }));
        setError(null);
        // Kick a fresh scoreboard fetch so the rank chip updates
        // before the next 5s tick — viewers expect a snappy confirm.
        void refreshScoreboard();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
        throw err;
      } finally {
        setSubmitting(false);
      }
    },
    [refreshScoreboard],
  );

  return useMemo<UseFantasyDraftResult>(
    () => ({
      agents,
      rows: board.rows,
      myRank: board.my_rank,
      myPicks: board.my_picks,
      error,
      submitting,
      refreshAgents,
      refreshScoreboard,
      submit,
    }),
    [agents, board, error, submitting, refreshAgents, refreshScoreboard, submit],
  );
}

"use client";

/**
 * Feature #14 — Live quest designer hook.
 *
 * Wraps the four backend endpoints with a single state model that the
 * panel component (or any other consumer) can drive. The hook owns:
 *
 * 1. The cached list of quests for the active session.
 * 2. A 5-second polling loop (the panel may also feed WS events
 *    directly into the component for sub-second updates — polling is
 *    the floor, not the ceiling).
 * 3. Mutation helpers that optimistically refresh after success so the
 *    UI doesn't have to wait a full poll cycle to see a new row.
 *
 * The hook is intentionally orthogonal to ``useSwarm``; the dashboard
 * may already be paying for a WS connection but this hook is also used
 * on lightweight viewer pages (where opening a second WS just for
 * quests would be wasteful).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  listQuests,
  proposeQuest,
  voteQuest,
  type Quest,
} from "@/lib/quests";

const POLL_INTERVAL_MS = 5_000;

export interface UseLiveQuestsResult {
  quests: Quest[];
  propose: (text: string) => Promise<void>;
  vote: (questId: number) => Promise<void>;
  refresh: () => Promise<void>;
  error: string | null;
}

export interface UseLiveQuestsOptions {
  /** When ``false`` the hook stops polling and clears its cached error.
   *  Useful for unmounted/hidden panels — keeps the network quiet. */
  enabled?: boolean;
}

export function useLiveQuests(
  sessionId: number,
  opts?: UseLiveQuestsOptions,
): UseLiveQuestsResult {
  const enabled = opts?.enabled ?? true;
  const [quests, setQuests] = useState<Quest[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Latch the latest sessionId into a ref so the polling effect captures
  // a stable read without recreating its timer when the parent rerenders
  // for other reasons. The ref is updated from an effect (per React 19's
  // ``react-hooks/refs`` rule which forbids ref writes during render).
  const sessionRef = useRef(sessionId);
  useEffect(() => {
    sessionRef.current = sessionId;
  }, [sessionId]);

  const refresh = useCallback(async () => {
    try {
      const next = await listQuests(sessionRef.current);
      setQuests(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  // Initial fetch + 5s polling loop. We restart the timer when
  // ``sessionId`` or ``enabled`` changes so the cached quests don't
  // belong to a stale session.
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void (async () => {
      if (cancelled) return;
      await refresh();
    })();
    const id = window.setInterval(() => {
      if (!cancelled) void refresh();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [enabled, sessionId, refresh]);

  const propose = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) {
        setError("퀘스트 내용을 입력하세요.");
        return;
      }
      try {
        await proposeQuest(sessionRef.current, trimmed);
        setError(null);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [refresh],
  );

  const vote = useCallback(
    async (questId: number) => {
      try {
        const { votes } = await voteQuest(questId);
        // Optimistic patch — bump the row immediately while the next
        // poll catches up. Avoids a flash of the old count.
        setQuests((prev) =>
          prev.map((q) => (q.id === questId ? { ...q, votes } : q)),
        );
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        throw err; // panel needs the error to grey-out on 409.
      }
    },
    [],
  );

  return useMemo(
    () => ({ quests, propose, vote, refresh, error }),
    [quests, propose, vote, refresh, error],
  );
}

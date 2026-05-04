"use client";

/**
 * Live-share — discovery hook.
 *
 * Polls ``GET /api/live-share/sessions`` every 5 s and exposes the
 * decoded list to the consumer. Optionally, the dashboard can poke a
 * ``liveDeltaTrigger`` counter whenever a ``live_share.visibility_changed``
 * event lands on the swarm WS — bumping the counter forces a refresh
 * outside the polling cadence so a host's "go public" toggle reflects
 * within ~100 ms instead of the next poll tick.
 *
 * The hook is intentionally orthogonal to ``useSwarm`` because
 * ``/live`` (the public discovery page) is mounted without any of the
 * swarm WS scaffolding — opening a second WS just to discover a list
 * of public rooms would be wasteful.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchLiveSessions, type LiveSession } from "@/lib/liveShare";

const DEFAULT_POLL_INTERVAL_MS = 5_000;

export interface UseLiveSessionsOptions {
  /** Override the 5 s default (e.g. lower for an admin dashboard,
   *  higher when the tab is backgrounded). */
  pollIntervalMs?: number;
  /** Bump to force an out-of-band refresh. The hook ignores the
   *  actual value — only changes are observed. ``0`` is fine for the
   *  initial render (no nudge yet). */
  liveDeltaTrigger?: number;
}

export interface UseLiveSessionsResult {
  sessions: LiveSession[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useLiveSessions(
  opts?: UseLiveSessionsOptions,
): UseLiveSessionsResult {
  const pollIntervalMs = opts?.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const liveDeltaTrigger = opts?.liveDeltaTrigger ?? 0;

  const [sessions, setSessions] = useState<LiveSession[]>([]);
  // ``loading`` is sticky for the very first fetch only — subsequent
  // polls update silently so the grid doesn't flicker every 5 s.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Latch the in-flight cancellation flag so a slow fetch finishing
  // after unmount doesn't try to setState on a dead component.
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchLiveSessions();
      if (!aliveRef.current) return;
      setSessions(next);
      setError(null);
    } catch (err) {
      if (!aliveRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (aliveRef.current) setLoading(false);
    }
  }, []);

  // Initial fetch + steady-state polling. Restarts when the interval
  // changes (e.g. tab visibility throttling could flip it).
  //
  // ``document.hidden`` guard: when the tab is backgrounded we skip
  // the periodic fetch. This avoids burning the operator's API budget
  // on a tab the user can't see, and matches the browser's own
  // throttling of timers in hidden tabs (~1 fire/min in Chrome). On
  // ``visibilitychange`` returning to ``visible`` we kick a refresh
  // immediately so the grid is up-to-date by the time the user looks
  // at it — the next polling tick may otherwise be ~5 s away.
  //
  // SSR safety: ``document`` is the obvious gate, but ``window`` is
  // the more common SSR sentinel; we use the former since the rest of
  // this effect only references ``window`` indirectly via the timer
  // API and the effect itself only runs after mount.
  //
  // NOTE on the WS-driven live-delta path (I7): ``useLiveSessions`` is
  // mounted by ``/live`` which is a public discovery page that doesn't
  // (and shouldn't) open a swarm WS. The host's dashboard is the right
  // place to bump ``liveDeltaTrigger`` when ``live_share.visibility_changed``
  // fires — it already has a swarm WS via ``useSwarm``. Keeping this
  // hook ignorant of the WS keeps ``/live`` from spinning up an extra
  // socket just for discovery. The 5 s polling cadence on the public
  // page is good enough for a directory listing.
  useEffect(() => {
    const docAvailable = typeof document !== "undefined";
    const isHidden = () => docAvailable && document.hidden;

    const tick = () => {
      if (isHidden()) return;
      void refresh();
    };

    // Initial fetch only when the tab is visible. If the user opens
    // the page in a backgrounded tab, the next ``visibilitychange``
    // will trigger the first fetch on focus.
    tick();

    const id = window.setInterval(tick, pollIntervalMs);

    let onVisibility: (() => void) | null = null;
    if (docAvailable) {
      onVisibility = () => {
        if (!document.hidden) void refresh();
      };
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      window.clearInterval(id);
      if (docAvailable && onVisibility) {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, [refresh, pollIntervalMs]);

  // Out-of-band refresh whenever the parent bumps the delta trigger.
  // The first render's value (0) is intentionally re-fetched here too
  // — that's fine; the polling effect already ran one fetch and the
  // duplicate is cheap.
  useEffect(() => {
    if (liveDeltaTrigger === 0) return;
    void refresh();
  }, [liveDeltaTrigger, refresh]);

  return useMemo(
    () => ({ sessions, loading, error, refresh }),
    [sessions, loading, error, refresh],
  );
}

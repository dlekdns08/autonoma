"use client";

/**
 * Feature #4 — viewer-betting React hook (backend-backed variant).
 *
 * Sibling of `useViewerBetting` (which is the localStorage MVP). This
 * version polls the API for open markets + balance + leaderboard, and
 * exposes a `placeBet` mutator that hits `/api/betting/...`. Resolutions
 * fan in via the swarm WebSocket — the host page passes the latest
 * `betting.market_resolved` event in `liveResolution` and the hook
 * refreshes balance + leaderboard automatically.
 *
 * Disabled-feature handling: when the API responds 503 with
 * `code=betting_disabled`, the hook surfaces `disabled=true` and stops
 * polling so we don't pound an off feature flag.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  type ApiEntry,
  type ApiLeaderboardRow,
  type ApiMarket,
  type ApiResolveSummary,
  BettingApiError,
  fetchBalance,
  fetchLeaderboard,
  listOpenMarkets,
  placeBet as apiPlaceBet,
} from "@/lib/viewerBettingApi";

export interface UseViewerBettingLiveOpts {
  sessionId: number;
  pollIntervalMs?: number;
  liveResolution?: ApiResolveSummary | null;
  enabled?: boolean;
}

export interface UseViewerBettingLive {
  markets: ApiMarket[];
  balance: number;
  leaderboard: ApiLeaderboardRow[];
  disabled: boolean;
  error: string | null;
  loading: boolean;
  placeBet: (marketId: string, option: string, stake: number) => Promise<ApiEntry | null>;
  refresh: () => Promise<void>;
}

export function useViewerBettingLive(
  opts: UseViewerBettingLiveOpts,
): UseViewerBettingLive {
  const { sessionId, pollIntervalMs = 7000, liveResolution = null, enabled = true } = opts;
  const [markets, setMarkets] = useState<ApiMarket[]>([]);
  const [balance, setBalance] = useState<number>(0);
  const [leaderboard, setLeaderboard] = useState<ApiLeaderboardRow[]>([]);
  const [disabled, setDisabled] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const cancelRef = useRef<boolean>(false);

  const refresh = useCallback(async () => {
    if (!enabled || sessionId <= 0) return;
    setLoading(true);
    try {
      const [m, b, lb] = await Promise.all([
        listOpenMarkets(sessionId),
        fetchBalance(sessionId),
        fetchLeaderboard(sessionId, 5),
      ]);
      if (cancelRef.current) return;
      setMarkets(m);
      setBalance(b.balance);
      setLeaderboard(lb);
      setDisabled(false);
      setError(null);
    } catch (err) {
      if (err instanceof BettingApiError && err.code === "betting_disabled") {
        setDisabled(true);
        setError(null);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError(String(err));
      }
    } finally {
      setLoading(false);
    }
  }, [enabled, sessionId]);

  // Initial + polling. Stops polling once `disabled` is sticky to avoid
  // hammering an off feature.
  useEffect(() => {
    cancelRef.current = false;
    void refresh();
    const tick = window.setInterval(() => {
      if (disabled) return;
      void refresh();
    }, pollIntervalMs);
    return () => {
      cancelRef.current = true;
      window.clearInterval(tick);
    };
  }, [refresh, pollIntervalMs, disabled]);

  // Fan in resolutions: when the parent forwards a `betting.market_resolved`
  // bus event, immediately re-fetch — the user's balance just changed.
  useEffect(() => {
    if (!liveResolution) return;
    void refresh();
  }, [liveResolution, refresh]);

  const placeBet = useCallback<UseViewerBettingLive["placeBet"]>(
    async (marketId, option, stake) => {
      try {
        const entry = await apiPlaceBet(marketId, sessionId, option, stake);
        await refresh();
        return entry;
      } catch (err) {
        if (err instanceof BettingApiError) {
          setError(err.code === "already_bet" ? "You already bet on this market." : err.message);
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError(String(err));
        }
        return null;
      }
    },
    [sessionId, refresh],
  );

  return { markets, balance, leaderboard, disabled, error, loading, placeBet, refresh };
}

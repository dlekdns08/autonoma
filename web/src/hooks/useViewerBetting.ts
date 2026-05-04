"use client";

/**
 * Feature #4 — viewer-betting React hook.
 *
 * Wraps `@/lib/viewerBetting` with React state so components re-render
 * when the wallet, leaderboard, or bet log change. The hook owns *no*
 * domain logic — every mutation goes back through ``placeBet`` /
 * ``resolveBet`` in the lib so the storage swap (localStorage ->
 * fetch) only touches one file.
 */

import { useCallback, useEffect, useState } from "react";
import {
  type Balance,
  type BetEntry,
  type BetResolution,
  type LeaderboardRow,
  type PlaceBetResult,
  type SettledEntry,
  getBalance,
  getBets,
  getLeaderboard,
  placeBet as placeBetLib,
  resolveBet as resolveBetLib,
  STORAGE_NS,
} from "@/lib/viewerBetting";

export interface UseViewerBetting {
  balance: Balance;
  bets: BetEntry[];
  leaderboard: LeaderboardRow[];
  /** Place a bet for the bound ``viewerId``. Returns the lib's discriminated result. */
  placeBet: (input: Omit<BetEntry, "placedAtMs" | "result" | "payout" | "viewerId">) => PlaceBetResult;
  /** Apply a host resolution; returns per-entry settlement so the caller can toast. */
  resolveBet: (resolution: BetResolution) => SettledEntry[];
  /** Pull fresh state from storage (used by listeners + after mutations). */
  refresh: () => void;
}

export function useViewerBetting(viewerId: string): UseViewerBetting {
  const [balance, setBalance] = useState<Balance>(() => ({ viewerId, amount: 0 }));
  const [bets, setBets] = useState<BetEntry[]>([]);
  const [leaderboard, setLeaderboard] = useState<LeaderboardRow[]>([]);

  const refresh = useCallback(() => {
    setBalance(getBalance(viewerId));
    setBets(getBets());
    setLeaderboard(getLeaderboard());
  }, [viewerId]);

  // Initial hydrate. We do this in an effect rather than during the
  // initial useState call so SSR doesn't read window.localStorage and
  // the first client render matches the server-rendered "empty" state.
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Cross-tab / cross-component sync: anything writing under the
  // ``autonoma:vb:`` namespace triggers a re-read. ``storage`` events
  // only fire across tabs, so components in the same tab still rely
  // on the post-mutation refresh inside placeBet/resolveBet below.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onStorage = (e: StorageEvent) => {
      if (e.key && e.key.startsWith(STORAGE_NS)) refresh();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [refresh]);

  const placeBet = useCallback<UseViewerBetting["placeBet"]>(
    (input) => {
      const result = placeBetLib({ ...input, viewerId });
      if (result.ok) refresh();
      return result;
    },
    [viewerId, refresh],
  );

  const resolveBet = useCallback<UseViewerBetting["resolveBet"]>(
    (resolution) => {
      const settled = resolveBetLib(resolution);
      if (settled.length > 0) refresh();
      return settled;
    },
    [refresh],
  );

  return { balance, bets, leaderboard, placeBet, resolveBet, refresh };
}

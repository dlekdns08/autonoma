"use client";

/**
 * Channel-points balance chip.
 *
 * Tiny floating pill that shows the viewer's current points wallet.
 * Polls the backend on a 15s cadence via :func:`usePoints` and ticks
 * heartbeats every 60s while the tab is visible — those side-effects
 * are owned by the hook, this component is just the surface.
 *
 * Designed to overlay on top of the watch / live pages without
 * competing with the chat overlay: positioning is the caller's
 * problem (we just render the pill itself).
 */

import { useMemo } from "react";
import { usePoints, type UsePointsResult } from "@/hooks/usePoints";

export interface PointsChipProps {
  sessionId: number | null;
  /** Optional pre-bound hook result. When supplied, the chip is purely
   *  presentational and the parent can share one hook instance with
   *  child components (e.g. the cookie picker). */
  bound?: UsePointsResult;
  className?: string;
}

export default function PointsChip({
  sessionId,
  bound,
  className,
}: PointsChipProps) {
  // The hook itself is a no-op when ``sessionId`` is null — we still
  // mount it so balance polling kicks in as soon as the WS hands us a
  // session id. When the parent supplied a ``bound`` result we skip
  // running our own hook to keep network traffic singleton.
  const ownState = usePoints(sessionId, { enabled: !bound });
  const state = bound ?? ownState;
  const balance = state.balance;

  const label = useMemo(() => {
    if (balance === null) return "…";
    if (balance >= 10_000) return `${Math.floor(balance / 1000)}k`;
    return String(balance);
  }, [balance]);

  return (
    <div
      className={
        "pointer-events-none inline-flex items-center gap-1 rounded-full " +
        "border border-amber-300/40 bg-amber-500/10 px-2.5 py-1 " +
        "font-mono text-[11px] tracking-wider text-amber-200 " +
        "shadow-sm backdrop-blur " +
        (className ?? "")
      }
      title="Channel points — earn by watching + voting"
      data-testid="points-chip"
    >
      <span aria-hidden>★</span>
      <span>{label}</span>
      <span className="opacity-60">pts</span>
    </div>
  );
}

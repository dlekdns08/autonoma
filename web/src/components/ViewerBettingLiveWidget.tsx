"use client";

/**
 * Feature #4 — Viewer betting widget, backend variant.
 *
 * UX is identical to the localStorage demo (`ViewerBettingWidget.tsx`)
 * but every mutation goes through `/api/betting/*` via
 * `useViewerBettingLive`. Drop-in replacement when the operator has
 * `AUTONOMA_VIEWER_BETTING_ENABLED=true`.
 *
 * The parent forwards the latest `betting.market_resolved` bus event
 * via `liveResolution` so payouts reflect server-side state without a
 * round-trip race against the next poll tick.
 */

import { useState } from "react";
import { useViewerBettingLive } from "@/hooks/useViewerBettingLive";
import type { ApiResolveSummary } from "@/lib/viewerBettingApi";

const STAKES = [10, 50, 100] as const;
type Stake = (typeof STAKES)[number];

export interface ViewerBettingLiveWidgetProps {
  sessionId: number;
  isAdmin?: boolean;
  /** Most recent `betting.market_resolved` event from the swarm WS. */
  liveResolution?: ApiResolveSummary | null;
}

export default function ViewerBettingLiveWidget({
  sessionId,
  liveResolution = null,
}: ViewerBettingLiveWidgetProps) {
  const { markets, balance, leaderboard, disabled, error, loading, placeBet } =
    useViewerBettingLive({ sessionId, liveResolution });
  const [stake, setStake] = useState<Stake>(50);
  const [pending, setPending] = useState<string | null>(null);

  if (disabled) {
    return (
      <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-3 font-mono text-xs text-white/50">
        🪙 viewer betting is currently disabled by the operator.
      </section>
    );
  }

  if (sessionId <= 0) {
    return (
      <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-3 font-mono text-xs text-white/50">
        🪙 start a swarm session to enable betting.
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-amber-400/20 bg-slate-950/60 p-3 font-mono text-xs text-white/80">
      <header className="mb-2 flex items-center justify-between">
        <span className="text-amber-300">🪙 Live Betting</span>
        <span className="text-white/60">balance: <strong className="text-amber-200">{balance}</strong></span>
      </header>

      {error && (
        <div className="mb-2 rounded border border-rose-500/30 bg-rose-950/40 px-2 py-1 text-[11px] text-rose-200">
          {error}
        </div>
      )}

      <div className="mb-3 flex items-center gap-1">
        <span className="text-white/50">stake</span>
        {STAKES.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setStake(s)}
            className={`rounded px-2 py-0.5 text-[11px] ${
              stake === s
                ? "bg-amber-400/20 text-amber-200"
                : "bg-white/5 text-white/60 hover:bg-white/10"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        {markets.length === 0 && (
          <div className="text-white/40">no open markets right now</div>
        )}
        {markets.map((m) => (
          <div key={m.market_id} className="rounded-lg border border-white/10 p-2">
            <div className="mb-1 text-white/80">{m.question}</div>
            <div className="flex flex-wrap gap-1">
              {/* The backend stores the option set inside the market_id
                  envelope (not as a separate column today). For the MVP
                  the parent passes a stable list of standard options:
                  yes/no for binary markets, agent names otherwise. We
                  default to ["yes","no"] so the component stays usable
                  without a parent. */}
              {(["yes", "no"]).map((opt) => (
                <button
                  key={opt}
                  type="button"
                  disabled={pending === m.market_id || balance < stake}
                  onClick={async () => {
                    setPending(m.market_id);
                    await placeBet(m.market_id, opt, stake);
                    setPending(null);
                  }}
                  className="rounded bg-white/5 px-2 py-1 text-[11px] text-white/80 hover:bg-amber-400/20 disabled:opacity-40"
                >
                  bet {stake} on {opt}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {leaderboard.length > 0 && (
        <div className="mt-3 border-t border-white/10 pt-2">
          <div className="mb-1 text-[10px] uppercase tracking-wider text-white/40">
            top viewers
          </div>
          <ol className="space-y-0.5">
            {leaderboard.slice(0, 5).map((row) => (
              <li key={row.viewer_id} className="flex justify-between">
                <span className="truncate">{row.display_name}</span>
                <span className={row.net >= 0 ? "text-emerald-300" : "text-rose-300"}>
                  {row.net >= 0 ? "+" : ""}
                  {row.net}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {loading && <div className="mt-1 text-[10px] text-white/30">refreshing…</div>}
    </section>
  );
}

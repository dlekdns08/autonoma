"use client";

/**
 * Feature #4 — Viewer betting widget (channel-points style).
 *
 * Self-contained card that drops into the dashboard. Markets and
 * resolutions are passed in by the parent (eventually fed from the
 * harness bus); everything else — wallet, bet log, leaderboard — is
 * persisted to ``localStorage`` via ``@/lib/viewerBetting``.
 *
 * UX flow:
 *   1. Open markets render with one button per option + a stake selector
 *      (10 / 50 / 100). Clicking an option places the bet at the
 *      selected stake.
 *   2. Each successful placement deducts the wallet and pushes a
 *      "🪙 You bet 50 on Midori" toast.
 *   3. When ``resolutions`` gains a new entry, the widget settles every
 *      matching bet and pops a win/loss toast for the bound viewer.
 *   4. The leaderboard panel shows the top 5 viewers by lifetime
 *      winnings, drawn from ``autonoma:vb:lb``.
 *
 * The toast layer here is intentionally local (a small fixed list at
 * the top-right of the card) so the widget can be embedded anywhere
 * without depending on the global ``Toast`` container.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useViewerBetting } from "@/hooks/useViewerBetting";
import type { BetResolution, Market } from "@/lib/viewerBetting";

const STAKES = [10, 50, 100] as const;
type Stake = (typeof STAKES)[number];

export interface ViewerBettingWidgetProps {
  sessionId: number;
  viewerId: string;
  displayName: string;
  /** Markets the host has opened for the current session. */
  markets: Market[];
  /** Host-side settlements. Each new entry triggers payout + toast. */
  resolutions: BetResolution[];
}

interface LocalToast {
  id: number;
  tone: "info" | "win" | "loss" | "error";
  text: string;
}

let toastIdCounter = 0;

export default function ViewerBettingWidget({
  sessionId,
  viewerId,
  displayName,
  markets,
  resolutions,
}: ViewerBettingWidgetProps) {
  const { balance, bets, leaderboard, placeBet, resolveBet } = useViewerBetting(viewerId);
  const [stake, setStake] = useState<Stake>(50);
  const [toasts, setToasts] = useState<LocalToast[]>([]);

  // Track which resolutions we've already piped through resolveBet so a
  // parent re-render with the same array doesn't double-pay or double-toast.
  // resolveBet itself is idempotent in the lib, but we still want to avoid
  // re-toasting on every render.
  const seenResolutions = useRef<Set<string>>(new Set());

  const pushToast = useCallback((tone: LocalToast["tone"], text: string) => {
    const id = ++toastIdCounter;
    setToasts((prev) => [...prev, { id, tone, text }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3500);
  }, []);

  // Apply new resolutions whenever the prop array changes.
  useEffect(() => {
    for (const r of resolutions) {
      if (seenResolutions.current.has(r.marketId)) continue;
      seenResolutions.current.add(r.marketId);
      const settled = resolveBet(r);
      // Find the entry belonging to *this* viewer (if any) and toast it.
      const mine = settled.find((s) => s.entry.viewerId === viewerId);
      if (mine) {
        if (mine.entry.result === "won") {
          pushToast("win", `🎉 You won ${mine.delta} on "${shortQuestion(markets, r.marketId)}"`);
        } else {
          pushToast("loss", `💸 You lost ${mine.entry.stake} on "${shortQuestion(markets, r.marketId)}"`);
        }
      }
    }
  }, [resolutions, resolveBet, viewerId, markets, pushToast]);

  const myBetsByMarket = useMemo(() => {
    const out: Record<string, (typeof bets)[number]> = {};
    for (const b of bets) {
      if (b.viewerId === viewerId) out[b.marketId] = b;
    }
    return out;
  }, [bets, viewerId]);

  const onPlace = useCallback(
    (market: Market, option: string) => {
      if (market.status !== "open") {
        pushToast("error", "🔒 Market is locked");
        return;
      }
      const result = placeBet({
        marketId: market.id,
        displayName,
        option,
        stake,
      });
      if (!result.ok) {
        const reasonText: Record<typeof result.reason, string> = {
          insufficient_balance: "❌ Not enough channel points",
          invalid_stake: "❌ Invalid stake",
          duplicate: "⚠️ You already bet on this market",
        };
        pushToast("error", reasonText[result.reason]);
        return;
      }
      pushToast("info", `🪙 You bet ${stake} on ${option}`);
    },
    [placeBet, displayName, stake, pushToast],
  );

  const top5 = leaderboard.slice(0, 5);
  const openMarkets = markets.filter((m) => m.status === "open" || m.status === "locked");

  return (
    <div className="relative flex flex-col gap-3 rounded-2xl border border-white/10 bg-slate-950/60 p-3 text-white">
      <header className="flex items-center justify-between">
        <h3 className="font-mono text-xs font-semibold uppercase tracking-wider text-white/70">
          🎰 Viewer Bets
        </h3>
        <div className="flex items-center gap-2 font-mono text-[10px]">
          <span className="rounded bg-amber-500/20 px-2 py-0.5 text-amber-200 tabular-nums">
            🪙 {balance.amount}
          </span>
          <span className="rounded bg-white/5 px-2 py-0.5 text-white/40">
            s{sessionId}
          </span>
        </div>
      </header>

      {/* Stake selector */}
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] uppercase tracking-wider text-white/40">
          Stake
        </span>
        <div className="flex gap-1">
          {STAKES.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setStake(s)}
              className={`rounded border px-2 py-0.5 font-mono text-[11px] tabular-nums transition ${
                stake === s
                  ? "border-fuchsia-400/60 bg-fuchsia-500/20 text-fuchsia-100"
                  : "border-white/10 bg-white/5 text-white/60 hover:bg-white/10"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Markets */}
      {openMarkets.length === 0 ? (
        <p className="rounded border border-white/5 bg-white/5 px-3 py-4 text-center font-mono text-[11px] text-white/40">
          No open markets — wait for the host to spin one up.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {openMarkets.map((m) => {
            const mine = myBetsByMarket[m.id];
            const locked = m.status === "locked" || mine !== undefined;
            return (
              <li
                key={m.id}
                className="flex flex-col gap-1.5 rounded-lg border border-white/5 bg-slate-900/40 p-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="font-mono text-xs text-white/85">{m.question}</span>
                  {m.status === "locked" ? (
                    <span className="shrink-0 rounded bg-rose-500/20 px-1.5 py-0.5 font-mono text-[9px] uppercase text-rose-200">
                      locked
                    </span>
                  ) : null}
                </div>
                <div className="flex flex-wrap gap-1">
                  {m.options.map((opt) => {
                    const isMine = mine?.option === opt;
                    return (
                      <button
                        key={opt}
                        type="button"
                        disabled={locked}
                        onClick={() => onPlace(m, opt)}
                        className={`rounded border px-2 py-1 font-mono text-[11px] transition ${
                          isMine
                            ? "border-emerald-400/60 bg-emerald-500/20 text-emerald-100"
                            : locked
                              ? "cursor-not-allowed border-white/10 bg-white/5 text-white/30"
                              : "border-cyan-400/40 bg-cyan-500/10 text-cyan-100 hover:bg-cyan-500/25"
                        }`}
                      >
                        {opt}
                        {isMine ? ` · ${mine.stake}` : ""}
                      </button>
                    );
                  })}
                </div>
                {mine ? (
                  <p className="font-mono text-[10px] text-white/40">
                    Your stake: {mine.stake} on <span className="text-white/70">{mine.option}</span>
                    {mine.result === "won"
                      ? ` · won ${mine.payout}`
                      : mine.result === "lost"
                        ? " · lost"
                        : ""}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      {/* Leaderboard */}
      <section className="flex flex-col gap-1 border-t border-white/5 pt-2">
        <h4 className="font-mono text-[10px] uppercase tracking-wider text-white/40">
          🏆 Top 5 lifetime winnings
        </h4>
        {top5.length === 0 ? (
          <p className="font-mono text-[10px] text-white/30">No winners yet.</p>
        ) : (
          <ol className="flex flex-col gap-0.5">
            {top5.map((row, i) => (
              <li
                key={row.viewerId}
                className={`flex items-center justify-between font-mono text-[11px] ${
                  row.viewerId === viewerId ? "text-fuchsia-200" : "text-white/70"
                }`}
              >
                <span className="truncate">
                  <span className="text-white/40 tabular-nums">{i + 1}.</span> {row.displayName}
                </span>
                <span className="tabular-nums">🪙 {row.winnings}</span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* Local toast stack */}
      {toasts.length > 0 ? (
        <div className="pointer-events-none absolute right-2 top-2 flex flex-col gap-1">
          {toasts.map((t) => (
            <div
              key={t.id}
              className={`rounded-md border px-2 py-1 font-mono text-[10px] backdrop-blur-sm ${
                t.tone === "win"
                  ? "border-emerald-400/50 bg-emerald-950/80 text-emerald-100"
                  : t.tone === "loss"
                    ? "border-rose-400/50 bg-rose-950/80 text-rose-100"
                    : t.tone === "error"
                      ? "border-amber-400/50 bg-amber-950/80 text-amber-100"
                      : "border-cyan-400/50 bg-cyan-950/80 text-cyan-100"
              }`}
            >
              {t.text}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function shortQuestion(markets: Market[], marketId: string): string {
  const m = markets.find((x) => x.id === marketId);
  if (!m) return marketId;
  return m.question.length > 40 ? `${m.question.slice(0, 37)}…` : m.question;
}

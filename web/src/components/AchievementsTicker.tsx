"use client";

/**
 * <AchievementsTicker /> — horizontal "newest first" feed for the swarm's
 * recent achievements (Feature #12).
 *
 * Inputs:
 *   - ``recent``    : initial list (typically from GET /api/achievements/recent)
 *   - ``liveEvent`` : optional latest WS event ``character.achievement_earned``
 *                     fed from the parent. Each *new* reference is appended to
 *                     the right side and flashes for 8 seconds before fading.
 *
 * Animation budget: Tailwind transitions only; a single ``useEffect`` runs
 * the 8-second fade timers per entry. No external animation libraries.
 */

import { useEffect, useMemo, useRef, useState } from "react";

export interface TickerEntry {
  achievement_id: string;
  title: string;
  tier: "bronze" | "silver" | "gold" | string;
  character_name: string;
  species_emoji?: string;
  earned_at: string;
  /** Synthetic flag for entries that arrived via WS during this session. */
  isLive?: boolean;
}

interface AchievementsTickerProps {
  recent: TickerEntry[];
  liveEvent?: TickerEntry | null;
}

const FADE_MS = 8000;

const TIER_TEXT: Record<string, string> = {
  bronze: "text-amber-700",
  silver: "text-slate-300",
  gold: "text-yellow-300",
};

const TIER_BORDER: Record<string, string> = {
  bronze: "border-amber-700/50",
  silver: "border-slate-300/50",
  gold: "border-yellow-300/60",
};

interface InternalEntry extends TickerEntry {
  /** Stable client key — handles dupes across (id, character, ts). */
  key: string;
  /** ms timestamp the entry was pushed into the live queue. */
  pushedAt: number;
}

function makeKey(e: TickerEntry, salt: number): string {
  return `${e.achievement_id}::${e.character_name}::${e.earned_at}::${salt}`;
}

export default function AchievementsTicker({
  recent,
  liveEvent,
}: AchievementsTickerProps) {
  // Fading queue — only entries here are subject to the 8-second timer.
  // Initial ``recent`` list is rendered as a static base so the strip is
  // not empty on first paint.
  const [liveQueue, setLiveQueue] = useState<InternalEntry[]>([]);
  const seenLiveRef = useRef<TickerEntry | null>(null);
  const saltRef = useRef(0);

  // Append new live events as they arrive. We compare by reference *and*
  // by composite key so that the parent feeding the same object twice
  // does not double-push, but two distinct events with the same id-from-
  // different-characters still both appear.
  useEffect(() => {
    if (!liveEvent) return;
    if (seenLiveRef.current === liveEvent) return;
    seenLiveRef.current = liveEvent;
    saltRef.current += 1;
    const next: InternalEntry = {
      ...liveEvent,
      isLive: true,
      key: makeKey(liveEvent, saltRef.current),
      pushedAt: Date.now(),
    };
    setLiveQueue((prev) => [...prev, next]);
  }, [liveEvent]);

  // Single timer that fades+evicts the oldest live entries after FADE_MS.
  useEffect(() => {
    if (liveQueue.length === 0) return;
    const oldest = liveQueue[0];
    const remaining = Math.max(0, FADE_MS - (Date.now() - oldest.pushedAt));
    const t = setTimeout(() => {
      setLiveQueue((prev) => prev.slice(1));
    }, remaining);
    return () => clearTimeout(t);
  }, [liveQueue]);

  // Static base — the server-fetched ``recent`` list. Newest at the right
  // matches the WS append direction.
  const baseEntries = useMemo<InternalEntry[]>(() => {
    return [...recent]
      .sort((a, b) => a.earned_at.localeCompare(b.earned_at))
      .map((e, i) => ({
        ...e,
        key: makeKey(e, -i - 1),
        pushedAt: 0,
      }));
  }, [recent]);

  const allEntries: InternalEntry[] = [...baseEntries, ...liveQueue];

  if (allEntries.length === 0) {
    return (
      <div className="w-full overflow-hidden rounded-lg border border-white/10 bg-slate-950/50 px-3 py-2 font-mono text-[11px] text-white/40">
        no achievements yet
      </div>
    );
  }

  return (
    <div className="relative w-full overflow-hidden rounded-lg border border-white/10 bg-slate-950/50">
      {/* Left fade gradient — purely cosmetic */}
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-12 bg-gradient-to-r from-slate-950 to-transparent" />
      <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-12 bg-gradient-to-l from-slate-950/40 to-transparent" />
      <div
        className="flex flex-row-reverse items-center gap-2 overflow-x-auto whitespace-nowrap px-3 py-2"
        // ``flex-row-reverse`` keeps the newest (last-appended) entry on
        // the right while the natural scroll/overflow falls off to the left.
      >
        {allEntries
          .slice()
          .reverse()
          .map((entry) => {
            const tierKey = (entry.tier ?? "").toLowerCase();
            const textCls = TIER_TEXT[tierKey] ?? "text-slate-300";
            const borderCls = TIER_BORDER[tierKey] ?? "border-slate-500/40";
            // Live entries fade their opacity over their FADE_MS lifetime
            // via a CSS transition; static base entries stay at full
            // opacity. We compute a target opacity from age.
            const isLive = entry.pushedAt > 0;
            const age = isLive ? Date.now() - entry.pushedAt : 0;
            const opacity = isLive
              ? Math.max(0, 1 - age / FADE_MS)
              : 0.85;
            return (
              <div
                key={entry.key}
                className={`flex shrink-0 items-center gap-1.5 rounded-full border bg-slate-900/70 px-2.5 py-1 font-mono text-[11px] transition-opacity duration-700 ${borderCls} ${
                  isLive ? "ring-1 ring-fuchsia-400/40" : ""
                }`}
                style={{ opacity }}
                title={`${entry.title} · ${entry.character_name} · ${entry.earned_at}`}
              >
                <span aria-hidden>{entry.species_emoji ?? "✨"}</span>
                <span className="text-white/80">{entry.character_name}</span>
                <span className="text-white/30">·</span>
                <span className={textCls}>{entry.title}</span>
              </div>
            );
          })}
      </div>
    </div>
  );
}

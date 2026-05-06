"use client";

/**
 * Live-share — discovery card.
 *
 * Pure presentational. Wrapped in a Next.js ``<Link>`` so the whole
 * card behaves like a single CTA into the kiosk-style ``/watch/<code>``
 * viewer page. Styling matches the dark slate-950 aesthetic used by
 * ``LiveQuestPanel`` and the agent grid in the dashboard.
 *
 * Time-since helper is inlined here (no ``time-ago`` dependency
 * allowed). The bands are chosen so the chip stays under 8 chars:
 *
 *   < 60 s         -> "just now"
 *   < 60 m         -> "Xm ago"
 *   < 24 h         -> "Xh Ym ago" (or "Xh ago" if minutes is 0)
 *   otherwise      -> "Xd ago"
 */

import Link from "next/link";
import type { LiveSession } from "@/lib/liveShare";

export interface LiveSessionCardProps {
  session: LiveSession;
}

export function formatTimeSince(unixSeconds: number, nowMs?: number): string {
  if (!Number.isFinite(unixSeconds) || unixSeconds <= 0) return "—";
  const reference = nowMs ?? Date.now();
  const diffSec = Math.max(0, Math.floor(reference / 1000 - unixSeconds));
  if (diffSec < 60) return "just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const hours = Math.floor(diffMin / 60);
  const minutes = diffMin % 60;
  if (hours < 24) {
    return minutes === 0 ? `${hours}h ago` : `${hours}h ${minutes}m ago`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function LiveSessionCard({ session }: LiveSessionCardProps) {
  const headline =
    session.title?.trim() ||
    session.goal?.trim() ||
    "Autonoma live show";
  const description = session.description?.trim() || "";
  const agents = (session.agents ?? []).slice(0, 6);

  return (
    <Link
      href={`/watch/${session.room_code}`}
      className="group flex flex-col gap-3 rounded-2xl border border-white/10 bg-slate-950/70 p-4 text-white transition-colors hover:border-fuchsia-400/40 hover:bg-slate-900/80"
      aria-label={`Watch ${headline}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5 rounded-full border border-rose-400/40 bg-rose-500/15 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-rose-200">
          <span
            aria-hidden="true"
            className="inline-block h-1.5 w-1.5 rounded-full bg-rose-400 animate-pulse"
          />
          Live
        </div>
        <span className="font-mono text-[10px] tabular-nums text-white/40">
          {formatTimeSince(session.started_at)}
        </span>
      </div>

      <div className="flex flex-col gap-1">
        <h3 className="font-mono text-sm font-semibold leading-snug text-white/95 line-clamp-2">
          {headline}
        </h3>
        {description ? (
          <p className="font-mono text-[11px] leading-relaxed text-white/60 line-clamp-3">
            {description}
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-2 font-mono text-[10px] text-white/50">
        <span className="truncate text-white/70">
          🎙 {session.host_display_name || "anon host"}
        </span>
        <span className="text-white/20">·</span>
        <span className="tabular-nums">round {session.round_number}</span>
      </div>

      {agents.length > 0 ? (
        <div className="flex items-center gap-1 text-base">
          {agents.map((a) => (
            <span
              key={a.name}
              title={`${a.name} (${a.role}, ${a.mood})`}
              className="leading-none"
              aria-hidden="true"
            >
              {a.emoji || "🤖"}
            </span>
          ))}
          {session.agent_count > agents.length ? (
            <span className="ml-1 font-mono text-[10px] text-white/40">
              +{session.agent_count - agents.length}
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="mt-auto flex items-center justify-between gap-2 pt-1">
        <div className="flex items-center gap-3 font-mono text-[10px] text-white/50">
          <span className="tabular-nums">
            👁 {session.viewer_count}
          </span>
          <span className="tabular-nums">
            🤖 {session.agent_count}
          </span>
        </div>
        <span className="rounded-lg border border-fuchsia-400/40 bg-fuchsia-500/15 px-2.5 py-1 font-mono text-[11px] text-fuchsia-100 transition-colors group-hover:bg-fuchsia-500/30">
          ▶ Watch
        </span>
      </div>
    </Link>
  );
}

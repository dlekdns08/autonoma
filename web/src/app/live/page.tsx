"use client";

/**
 * Live-share — public discovery page (``/live``).
 *
 * Renders a responsive grid of every public session returned by
 * ``GET /api/live-share/sessions``. Polls every 5 s via
 * ``useLiveSessions`` so a viewer landing here sees new shows as they
 * go public without manually refreshing.
 *
 * No auth gate, no Header chrome — this is the public landing page,
 * deliberately stripped of the dashboard's swarm UI so it's safe to
 * link in tweets/embeds. (The watch page remains the kiosk view; this
 * page is just the index.)
 */

import { useMemo } from "react";
import Link from "next/link";
import LiveSessionCard from "@/components/LiveSessionCard";
import { useLiveSessions } from "@/hooks/useLiveSessions";

export default function LivePage() {
  const { sessions, loading, error } = useLiveSessions();

  // Sort defensively in case the backend response order changes — most
  // viewers want freshest-first regardless. ``started_at`` is unix
  // seconds so a numeric compare is enough.
  const ordered = useMemo(
    () => [...sessions].sort((a, b) => b.started_at - a.started_at),
    [sessions],
  );

  return (
    <div className="min-h-screen bg-[#0a0a12] text-white">
      <header className="sticky top-0 z-10 border-b border-white/10 bg-slate-950/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-5 py-4">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="font-mono text-sm font-semibold text-white/85 hover:text-fuchsia-300"
            >
              ⬡ autonoma
            </Link>
            <span className="text-white/20">/</span>
            <span className="font-mono text-xs uppercase tracking-wider text-white/60">
              live
            </span>
          </div>
          <span
            className="flex items-center gap-1.5 rounded-full border border-rose-400/40 bg-rose-500/15 px-2.5 py-1 font-mono text-[11px] tabular-nums text-rose-200"
            aria-live="polite"
          >
            <span
              aria-hidden="true"
              className="inline-block h-1.5 w-1.5 rounded-full bg-rose-400 animate-pulse"
            />
            🔴 {ordered.length} live now
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-5 py-8">
        <div className="mb-6 flex flex-col gap-1">
          <h1 className="font-mono text-2xl font-bold tracking-tight text-white/95">
            Live shows
          </h1>
          <p className="font-mono text-xs text-white/50">
            Watch self-organizing agent swarms tackle real quests, in real
            time. New shows appear here as hosts open them up.
          </p>
        </div>

        {error ? (
          <div
            role="alert"
            className="mb-4 rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 font-mono text-[11px] text-rose-200"
          >
            {error}
          </div>
        ) : null}

        {loading && ordered.length === 0 ? (
          <p className="font-mono text-xs text-white/40">Loading…</p>
        ) : ordered.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-8">
            {ordered.map((session) => (
              <li
                key={session.room_code}
                className="col-span-2 xl:col-span-2"
              >
                <LiveSessionCard session={session} />
              </li>
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-white/15 bg-slate-950/60 px-6 py-12 text-center">
      <span className="text-3xl" aria-hidden="true">
        🎬
      </span>
      <h2 className="font-mono text-base font-semibold text-white/85">
        No live shows right now.
      </h2>
      <p className="max-w-md font-mono text-[11px] text-white/50">
        Hosts: open a swarm and flip{" "}
        <span className="text-fuchsia-300">"Make this room public"</span>{" "}
        from the share button to land here.
      </p>
      <Link
        href="/"
        className="rounded-lg border border-fuchsia-400/40 bg-fuchsia-500/15 px-3 py-1.5 font-mono text-[11px] text-fuchsia-100 hover:bg-fuchsia-500/30"
      >
        Start a swarm →
      </Link>
    </div>
  );
}

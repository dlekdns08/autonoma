"use client";

/**
 * Cross-session character leaderboard (``/leaderboard``).
 *
 * Three tabs (XP / Runs Survived / Achievements) re-fetch from
 * ``GET /api/leaderboard/characters?metric=...`` on click. Each row
 * deep-links into ``/agent/{uuid}`` so a viewer can drill into the
 * character's profile from any ranking.
 *
 * Style mirrors ``/live``: dark slate, sticky header, mono font, dashed
 * empty state, rose accent on the live badge swapped for fuchsia on the
 * active tab. No swarm WS — this is a read-only directory page.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { API_BASE_URL } from "@/hooks/useSwarm";

type Metric = "xp" | "runs_survived" | "achievements";

interface LeaderboardRow {
  uuid: string;
  name: string;
  species_emoji: string;
  role: string;
  level: number;
  xp: number;
  runs_survived: number;
  achievement_count: number;
}

const TABS: { id: Metric; label: string }[] = [
  { id: "xp", label: "XP" },
  { id: "runs_survived", label: "Runs Survived" },
  { id: "achievements", label: "Achievements" },
];

// Defensive coercion: the API may evolve to return a bare ``[...]``
// instead of ``{rows: [...]}``. Accept both, guard with Array.isArray
// before iterating, and reject anything that isn't an object shaped
// like a row.
function coerceRows(body: unknown): LeaderboardRow[] {
  let raw: unknown[] = [];
  if (Array.isArray(body)) {
    raw = body;
  } else if (
    body &&
    typeof body === "object" &&
    Array.isArray((body as { rows?: unknown }).rows)
  ) {
    raw = (body as { rows: unknown[] }).rows;
  }
  if (!Array.isArray(raw)) return [];
  const out: LeaderboardRow[] = [];
  for (const r of raw) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    if (typeof o.uuid !== "string" || typeof o.name !== "string") continue;
    out.push({
      uuid: o.uuid,
      name: o.name,
      species_emoji: typeof o.species_emoji === "string" ? o.species_emoji : "",
      role: typeof o.role === "string" ? o.role : "",
      level: Number(o.level ?? 0) || 0,
      xp: Number(o.xp ?? 0) || 0,
      runs_survived: Number(o.runs_survived ?? 0) || 0,
      achievement_count: Number(o.achievement_count ?? 0) || 0,
    });
  }
  return out;
}

function valueFor(row: LeaderboardRow, metric: Metric): number {
  if (metric === "runs_survived") return row.runs_survived;
  if (metric === "achievements") return row.achievement_count;
  return row.xp;
}

function valueLabel(metric: Metric): string {
  if (metric === "runs_survived") return "runs";
  if (metric === "achievements") return "badges";
  return "xp";
}

export default function LeaderboardPage() {
  const [metric, setMetric] = useState<Metric>("xp");
  const [rows, setRows] = useState<LeaderboardRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (m: Metric, signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/leaderboard/characters?metric=${encodeURIComponent(m)}&limit=50`,
          { credentials: "include", signal },
        );
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const body: unknown = await res.json();
        if (signal.aborted) return;
        setRows(coerceRows(body));
      } catch (err) {
        if (signal.aborted) return;
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
        setRows([]);
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    void load(metric, ctrl.signal);
    return () => ctrl.abort();
  }, [metric, load]);

  // Always iterate via Array.isArray-guarded ``ordered`` — if the API
  // ever returns a non-array shape that slipped past coerceRows, this
  // is the last line of defense.
  const ordered = useMemo(
    () => (Array.isArray(rows) ? rows : []),
    [rows],
  );

  return (
    <div className="min-h-screen bg-[#0a0a12] text-white">
      <header className="sticky top-0 z-10 border-b border-white/10 bg-slate-950/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-5 py-4">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="font-mono text-sm font-semibold text-white/85 hover:text-fuchsia-300"
            >
              ⬡ autonoma
            </Link>
            <span className="text-white/20">/</span>
            <span className="font-mono text-xs uppercase tracking-wider text-white/60">
              leaderboard
            </span>
          </div>
          <span
            className="font-mono text-[11px] tabular-nums text-white/50"
            aria-live="polite"
          >
            {ordered.length} ranked
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-5 py-8">
        <div className="mb-6 flex flex-col gap-1">
          <h1 className="font-mono text-2xl font-bold tracking-tight text-white/95">
            Character leaderboard
          </h1>
          <p className="font-mono text-xs text-white/50">
            Cross-session ranking of every agent who has ever taken the
            stage. Tap a row to drill into the character.
          </p>
        </div>

        <div
          role="tablist"
          aria-label="Sort metric"
          className="mb-5 flex flex-wrap gap-2"
        >
          {TABS.map((t) => {
            const active = t.id === metric;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setMetric(t.id)}
                className={
                  active
                    ? "rounded-lg border border-fuchsia-400/50 bg-fuchsia-500/20 px-3 py-1.5 font-mono text-[11px] text-fuchsia-100"
                    : "rounded-lg border border-white/10 bg-slate-900/60 px-3 py-1.5 font-mono text-[11px] text-white/65 hover:border-fuchsia-400/30 hover:text-fuchsia-200"
                }
              >
                {t.label}
              </button>
            );
          })}
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
          <ol className="flex flex-col gap-2">
            {ordered.map((row, idx) => (
              <li key={row.uuid}>
                <Link
                  href={`/agent/${encodeURIComponent(row.uuid)}`}
                  className="flex items-center gap-3 rounded-xl border border-white/10 bg-slate-900/60 px-4 py-3 transition hover:border-fuchsia-400/40 hover:bg-slate-900"
                >
                  <span className="w-8 shrink-0 font-mono text-xs tabular-nums text-white/40">
                    #{idx + 1}
                  </span>
                  <span
                    aria-hidden="true"
                    className="text-2xl"
                  >
                    {row.species_emoji || "👤"}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-mono text-sm font-semibold text-white/90">
                      {row.name}
                    </div>
                    <div className="truncate font-mono text-[11px] text-white/45">
                      {row.role || "—"} · lvl {row.level}
                    </div>
                  </div>
                  <span className="shrink-0 rounded-md border border-fuchsia-400/30 bg-fuchsia-500/10 px-2.5 py-1 text-right font-mono text-[12px] tabular-nums text-fuchsia-100">
                    {valueFor(row, metric).toLocaleString()}{" "}
                    <span className="text-fuchsia-200/60">
                      {valueLabel(metric)}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        )}
      </main>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-white/15 bg-slate-950/60 px-6 py-12 text-center">
      <span className="text-3xl" aria-hidden="true">
        🏆
      </span>
      <h2 className="font-mono text-base font-semibold text-white/85">
        No ranked characters yet.
      </h2>
      <p className="max-w-md font-mono text-[11px] text-white/50">
        Run a swarm to spawn characters — they&apos;ll show up here as
        soon as they earn XP, survive a run, or unlock a badge.
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

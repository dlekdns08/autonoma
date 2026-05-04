"use client";

/**
 * Admin page — Feature #19 A/B preset comparison.
 *
 * Lets an operator pick two recent completed sessions from a dropdown
 * pair and POST them to /api/ab/compare, then renders the resulting
 * report as side-by-side summaries, a deltas table, an anomaly bar
 * chart, and a winner banner.
 *
 * Visual hierarchy (top → bottom):
 *   1. Header        — title, subtitle, refresh button
 *   2. Picker row    — two <select> dropdowns + "Compare" button
 *   3. Error banner  — only when a fetch fails
 *   4. Result panel  — appears after a successful compare:
 *        a. Winner banner
 *        b. Side-by-side summary cards (A | B)
 *        c. Deltas table
 *        d. Anomaly stacked-bar chart
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { STRINGS } from "@/lib/strings";
import {
  fetchRecentRuns,
  compareRuns,
  type RunSummaryRow,
  type ABReport,
} from "@/lib/abCompare";

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatDate(value: string): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function runLabel(r: RunSummaryRow): string {
  const goal = (r.goal ?? "").slice(0, 40);
  const preset = r.preset_id || "default";
  return `#${r.session_id} – ${goal} (${preset})`;
}

function formatNumber(n: unknown): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 100) return n.toFixed(0);
  if (Math.abs(n) >= 10) return n.toFixed(1);
  return n.toFixed(2);
}

function formatDelta(n: number): { text: string; tone: "up" | "down" | "flat" } {
  if (!Number.isFinite(n) || n === 0) return { text: "0", tone: "flat" };
  const sign = n > 0 ? "+" : "";
  const text = `${sign}${formatNumber(n)}`;
  return { text, tone: n > 0 ? "up" : "down" };
}

const DELTA_LABELS: Record<string, string> = {
  tasks_done_pct: "tasks_done_pct",
  rounds_to_goal: "rounds_to_goal",
  llm_calls_per_round: "llm_calls_per_round",
};

const PREFERRED_DELTA_ORDER = [
  "tasks_done_pct",
  "rounds_to_goal",
  "llm_calls_per_round",
];

// ── Page ────────────────────────────────────────────────────────────────────

export default function AdminABComparePage() {
  const { user, loading: authLoading } = useAuth();

  const [runs, setRuns] = useState<RunSummaryRow[] | null>(null);
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [sessionA, setSessionA] = useState<number | "">("");
  const [sessionB, setSessionB] = useState<number | "">("");
  const [report, setReport] = useState<ABReport | null>(null);
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Newest-first ordering. Backend already does this, but defend in case
  // an older deployment returns ascending order.
  const sortedRuns = useMemo(() => {
    if (!runs) return [];
    return [...runs].sort((x, y) => {
      const tx = Date.parse(x.completed_at) || 0;
      const ty = Date.parse(y.completed_at) || 0;
      if (ty !== tx) return ty - tx;
      return y.session_id - x.session_id;
    });
  }, [runs]);

  const loadRuns = useCallback(async () => {
    setLoadingRuns(true);
    setError(null);
    try {
      const rows = await fetchRecentRuns(20);
      setRuns(rows);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`recent-runs 불러오기 실패: ${msg}`);
      setRuns([]);
    } finally {
      setLoadingRuns(false);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    void loadRuns();
  }, [authLoading, loadRuns]);

  const onCompare = useCallback(async () => {
    if (sessionA === "" || sessionB === "") return;
    if (sessionA === sessionB) {
      setError("같은 세션을 두 번 선택할 수 없습니다.");
      return;
    }
    setComparing(true);
    setError(null);
    setReport(null);
    try {
      const result = await compareRuns(Number(sessionA), Number(sessionB));
      setReport(result);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`compare 실패: ${msg}`);
    } finally {
      setComparing(false);
    }
  }, [sessionA, sessionB]);

  // ── Auth gate ────────────────────────────────────────────────────────────

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/40">
        loading...
      </div>
    );
  }

  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12]">
        <div className="max-w-md rounded-2xl border border-red-500/30 bg-slate-950/95 p-8 text-center shadow-2xl shadow-red-500/10">
          <div className="mb-3 text-4xl">⛔</div>
          <h1 className="text-2xl font-bold font-mono text-red-300">401</h1>
          <p className="mt-2 text-sm font-mono text-white/60">
            {STRINGS.admin.adminRequired}
          </p>
        </div>
      </div>
    );
  }

  // ── Render ───────────────────────────────────────────────────────────────

  const canCompare =
    sessionA !== "" &&
    sessionB !== "" &&
    sessionA !== sessionB &&
    !comparing;

  return (
    <div className="min-h-screen bg-[#0a0a12] p-6 text-white">
      <div className="mx-auto max-w-6xl">
        {/* 1. Header */}
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold font-mono text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 to-cyan-400">
              A/B Preset Comparison
            </h1>
            <p className="mt-1 text-xs font-mono text-white/40">
              Pick two recent runs to compare — /api/ab/compare
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={loadRuns}
              disabled={loadingRuns}
              className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-white/60 hover:bg-white/10 disabled:opacity-30 transition-all"
            >
              {loadingRuns ? STRINGS.common.refreshing : STRINGS.common.refresh}
            </button>
          </div>
        </header>

        {/* 2. Picker */}
        <section className="mb-4 rounded-xl border border-white/10 bg-slate-900/60 p-4">
          <div className="grid grid-cols-1 md:grid-cols-[1fr_1fr_auto] gap-3 items-end">
            <RunPicker
              label="Session A"
              accent="cyan"
              value={sessionA}
              onChange={setSessionA}
              runs={sortedRuns}
              disabled={loadingRuns}
            />
            <RunPicker
              label="Session B"
              accent="fuchsia"
              value={sessionB}
              onChange={setSessionB}
              runs={sortedRuns}
              disabled={loadingRuns}
            />
            <button
              type="button"
              onClick={onCompare}
              disabled={!canCompare}
              className="rounded-xl border border-fuchsia-500/50 bg-fuchsia-500/15 px-5 py-2 text-xs font-mono text-fuchsia-200 hover:bg-fuchsia-500/25 disabled:opacity-30 transition-all"
            >
              {comparing ? "Comparing..." : "Compare"}
            </button>
          </div>

          {sortedRuns.length === 0 && !loadingRuns && (
            <p className="mt-3 text-xs font-mono text-white/40">
              완료된 실행이 없습니다.
            </p>
          )}
        </section>

        {/* 3. Error banner */}
        {error && (
          <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-xs font-mono text-red-300">
            {error}
          </div>
        )}

        {/* 4. Result panel */}
        {report && <ResultPanel report={report} />}
      </div>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

interface RunPickerProps {
  label: string;
  accent: "cyan" | "fuchsia";
  value: number | "";
  onChange: (v: number | "") => void;
  runs: RunSummaryRow[];
  disabled?: boolean;
}

function RunPicker({
  label,
  accent,
  value,
  onChange,
  runs,
  disabled,
}: RunPickerProps) {
  const accentText = accent === "cyan" ? "text-cyan-300" : "text-fuchsia-300";
  const accentBorder =
    accent === "cyan" ? "focus:border-cyan-400/60" : "focus:border-fuchsia-400/60";
  return (
    <label className="block">
      <span className={`mb-1 block text-[11px] font-mono ${accentText}`}>
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => {
          const raw = e.target.value;
          onChange(raw === "" ? "" : Number(raw));
        }}
        disabled={disabled || runs.length === 0}
        className={`w-full rounded-lg border border-white/10 bg-slate-950/80 px-3 py-2 text-xs font-mono text-white/80 outline-none transition-colors hover:bg-slate-900/80 disabled:opacity-30 ${accentBorder}`}
      >
        <option value="">— select run —</option>
        {runs.map((r) => (
          <option key={r.session_id} value={r.session_id}>
            {runLabel(r)}
          </option>
        ))}
      </select>
    </label>
  );
}

function ResultPanel({ report }: { report: ABReport }) {
  return (
    <div className="space-y-4">
      <WinnerBanner winner={report.winner} a={report.session_a} b={report.session_b} />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <SummaryCard title="Session A" accent="cyan" summary={report.summary_a} />
        <SummaryCard title="Session B" accent="fuchsia" summary={report.summary_b} />
      </div>

      <DeltasTable deltas={report.deltas} />

      <AnomalyChart counts={report.anomaly_counts} />
    </div>
  );
}

function WinnerBanner({
  winner,
  a,
  b,
}: {
  winner: ABReport["winner"];
  a: number;
  b: number;
}) {
  let label: string;
  let cls: string;
  if (winner === "a") {
    label = `Winner: Session A (#${a})`;
    cls =
      "border-cyan-400/40 bg-cyan-500/10 text-cyan-200 shadow-cyan-500/10";
  } else if (winner === "b") {
    label = `Winner: Session B (#${b})`;
    cls =
      "border-fuchsia-400/40 bg-fuchsia-500/10 text-fuchsia-200 shadow-fuchsia-500/10";
  } else if (winner === "tie") {
    label = "Tie — no clear winner";
    cls = "border-white/15 bg-white/5 text-white/70";
  } else {
    label = `Winner: ${String(winner)}`;
    cls = "border-white/15 bg-white/5 text-white/70";
  }
  return (
    <div
      className={`rounded-xl border px-5 py-3 font-mono text-sm font-bold shadow-lg ${cls}`}
    >
      {label}
    </div>
  );
}

interface SummaryCardProps {
  title: string;
  accent: "cyan" | "fuchsia";
  summary: ABReport["summary_a"];
}

function SummaryCard({ title, accent, summary }: SummaryCardProps) {
  const accentText = accent === "cyan" ? "text-cyan-300" : "text-fuchsia-300";
  const accentBorder =
    accent === "cyan" ? "border-cyan-500/20" : "border-fuchsia-500/20";

  const rows: Array<[string, string]> = [
    ["session_id", `#${summary.session_id}`],
    ["preset_id", summary.preset_id || "default"],
    [
      "policy_hash",
      summary.policy_hash ? summary.policy_hash.slice(0, 12) : "—",
    ],
    ["completed_at", formatDate(summary.completed_at)],
    ["agents", String(summary.agent_count)],
    ["tasks", `${summary.tasks_done}/${summary.task_count}`],
    ["rounds", String(summary.total_rounds)],
    ["llm_calls", String(summary.llm_calls)],
  ];

  return (
    <div className={`rounded-xl border ${accentBorder} bg-slate-900/60 p-4`}>
      <div className="mb-2 flex items-center justify-between">
        <h3 className={`text-xs font-bold font-mono ${accentText}`}>{title}</h3>
        <span className="text-[10px] font-mono text-white/30">summary</span>
      </div>
      <p
        className="mb-3 truncate text-xs font-mono text-white/70"
        title={summary.goal}
      >
        {summary.goal || "(no goal)"}
      </p>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] font-mono">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between border-b border-white/5 py-1">
            <dt className="text-white/40">{k}</dt>
            <dd className="text-white/80">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function DeltasTable({ deltas }: { deltas: ABReport["deltas"] }) {
  const keys = Object.keys(deltas);
  const ordered = [
    ...PREFERRED_DELTA_ORDER.filter((k) => k in deltas),
    ...keys.filter((k) => !PREFERRED_DELTA_ORDER.includes(k)),
  ];

  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/60 overflow-hidden">
      <div className="border-b border-white/10 px-4 py-2">
        <h3 className="text-xs font-bold font-mono text-white/70">
          Deltas <span className="text-white/30">(B vs A)</span>
        </h3>
      </div>
      {ordered.length === 0 ? (
        <p className="p-4 text-xs font-mono text-white/40">No deltas reported.</p>
      ) : (
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="border-b border-white/10 text-white/40">
              <th className="px-4 py-2 text-left">metric</th>
              <th className="px-4 py-2 text-right">delta</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((k, i) => {
              const raw = deltas[k];
              const { text, tone } = formatDelta(raw);
              const toneCls =
                tone === "up"
                  ? "text-green-300"
                  : tone === "down"
                    ? "text-red-300"
                    : "text-white/50";
              return (
                <tr
                  key={k}
                  className={`border-b border-white/5 ${
                    i % 2 === 0 ? "bg-transparent" : "bg-white/[0.02]"
                  }`}
                >
                  <td className="px-4 py-2 text-white/70">
                    {DELTA_LABELS[k] ?? k}
                  </td>
                  <td className={`px-4 py-2 text-right font-bold ${toneCls}`}>
                    {text}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AnomalyChart({ counts }: { counts: ABReport["anomaly_counts"] }) {
  const kinds = Object.keys(counts).sort();

  // Find the max so bars share a scale.
  let max = 0;
  for (const k of kinds) {
    const v = counts[k];
    if (v) {
      max = Math.max(max, v.a ?? 0, v.b ?? 0);
    }
  }
  // Avoid divide-by-zero when there are no anomalies.
  const denom = max > 0 ? max : 1;

  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/60 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-bold font-mono text-white/70">
          Anomalies by kind
        </h3>
        <div className="flex gap-3 text-[10px] font-mono">
          <span className="flex items-center gap-1 text-cyan-300">
            <span className="inline-block h-2 w-3 rounded-sm bg-cyan-400/70" />
            A
          </span>
          <span className="flex items-center gap-1 text-fuchsia-300">
            <span className="inline-block h-2 w-3 rounded-sm bg-fuchsia-400/70" />
            B
          </span>
        </div>
      </div>

      {kinds.length === 0 ? (
        <p className="text-xs font-mono text-white/40">
          No anomalies recorded for either session.
        </p>
      ) : (
        <ul className="space-y-2">
          {kinds.map((k) => {
            const v = counts[k] ?? { a: 0, b: 0 };
            const a = v.a ?? 0;
            const b = v.b ?? 0;
            const aPct = (a / denom) * 100;
            const bPct = (b / denom) * 100;
            return (
              <li key={k} className="font-mono text-[11px]">
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-white/70">{k}</span>
                  <span className="text-white/40">
                    A {a} · B {b}
                  </span>
                </div>
                <div className="space-y-1">
                  <div className="h-2 w-full rounded bg-white/[0.04]">
                    <div
                      className="h-2 rounded bg-cyan-400/70"
                      style={{ width: `${aPct}%` }}
                      aria-label={`A ${a}`}
                    />
                  </div>
                  <div className="h-2 w-full rounded bg-white/[0.04]">
                    <div
                      className="h-2 rounded bg-fuchsia-400/70"
                      style={{ width: `${bPct}%` }}
                      aria-label={`B ${b}`}
                    />
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

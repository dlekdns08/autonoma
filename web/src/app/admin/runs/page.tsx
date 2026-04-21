"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { API_BASE_URL } from "@/hooks/useSwarm";

interface RunSummary {
  id: string;
  goal: string;
  created_at: string;
  agents: number;
  tasks_done: number;
  tasks_total: number;
  rounds: number;
  duration_seconds: number;
}

interface CompareResult {
  run_a: RunSummary;
  run_b: RunSummary;
  improvements: string[];
  regressions: string[];
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function formatDate(value: string): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function AdminRunsPage() {
  const { user, loading: authLoading } = useAuth();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<CompareResult | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);

  const isAdmin = user?.role === "admin";

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/runs`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (res.status === 401 || res.status === 403) {
        setError("관리자 권한이 필요합니다.");
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { runs: RunSummary[] };
      setRuns(data.runs ?? []);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`불러오기 실패: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authLoading && isAdmin) {
      void fetchRuns();
    }
  }, [authLoading, isAdmin, fetchRuns]);

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 2) return [prev[1], id];
      return [...prev, id];
    });
    setComparison(null);
  };

  const runCompare = useCallback(async () => {
    if (selectedIds.length !== 2) return;
    const [a, b] = selectedIds;
    setCompareLoading(true);
    setComparison(null);
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/runs/${a}/compare?with=${b}`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as CompareResult;
      setComparison(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`비교 실패: ${msg}`);
    } finally {
      setCompareLoading(false);
    }
  }, [selectedIds]);

  // ── Auth guards ────────────────────────────────────────────────────────

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/40">
        loading...
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12]">
        <div className="max-w-md rounded-2xl border border-red-500/30 bg-slate-950/95 p-8 text-center shadow-2xl shadow-red-500/10">
          <div className="mb-3 text-4xl">⛔</div>
          <h1 className="text-2xl font-bold font-mono text-red-300">403</h1>
          <p className="mt-2 text-sm font-mono text-white/60">
            관리자만 접근할 수 있습니다.
          </p>
        </div>
      </div>
    );
  }

  // ── Runs table ─────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[#0a0a12] p-6 text-white">
      <div className="mx-auto max-w-6xl">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold font-mono text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 to-cyan-400">
              Run History
            </h1>
            <p className="mt-1 text-xs font-mono text-white/40">
              Select two runs to compare — /api/runs
            </p>
          </div>
          <div className="flex gap-2">
            {selectedIds.length === 2 && (
              <button
                type="button"
                onClick={runCompare}
                disabled={compareLoading}
                className="rounded-xl border border-fuchsia-500/50 bg-fuchsia-500/15 px-4 py-2 text-xs font-mono text-fuchsia-200 hover:bg-fuchsia-500/25 disabled:opacity-40 transition-all"
              >
                {compareLoading ? "Comparing..." : "Compare selected"}
              </button>
            )}
            <button
              type="button"
              onClick={fetchRuns}
              disabled={loading}
              className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-white/60 hover:bg-white/10 disabled:opacity-30 transition-all"
            >
              {loading ? "새로고침 중..." : "새로고침 ⟳"}
            </button>
          </div>
        </header>

        {error && (
          <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-xs font-mono text-red-300">
            {error}
          </div>
        )}

        {/* Runs table */}
        <div className="rounded-xl border border-white/10 bg-slate-900/60 overflow-hidden">
          {runs === null || loading ? (
            <div className="p-8 text-center text-sm font-mono text-white/30">
              {loading ? "불러오는 중..." : "데이터 없음"}
            </div>
          ) : runs.length === 0 ? (
            <div className="p-8 text-center text-sm font-mono text-white/30">
              아직 완료된 실행이 없습니다.
            </div>
          ) : (
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-white/10 text-white/40">
                  <th className="px-3 py-2 text-left w-8">Sel</th>
                  <th className="px-3 py-2 text-left">Goal</th>
                  <th className="px-3 py-2 text-left">Date</th>
                  <th className="px-3 py-2 text-center">Agents</th>
                  <th className="px-3 py-2 text-center">Tasks</th>
                  <th className="px-3 py-2 text-center">Rounds</th>
                  <th className="px-3 py-2 text-center">Duration</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run, i) => {
                  const isSelected = selectedIds.includes(run.id);
                  return (
                    <tr
                      key={run.id}
                      className={`border-b border-white/5 transition-colors hover:bg-white/5 ${
                        isSelected ? "bg-fuchsia-500/10" : i % 2 === 0 ? "bg-transparent" : "bg-white/[0.02]"
                      }`}
                    >
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleSelect(run.id)}
                          className="accent-fuchsia-500 cursor-pointer"
                        />
                      </td>
                      <td className="px-3 py-2 max-w-[240px]">
                        <span
                          className="block truncate text-white/80"
                          title={run.goal}
                        >
                          {run.goal || "(no goal)"}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-white/50 whitespace-nowrap">
                        {formatDate(run.created_at)}
                      </td>
                      <td className="px-3 py-2 text-center text-cyan-300">
                        {run.agents}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <span
                          className={
                            run.tasks_done === run.tasks_total && run.tasks_total > 0
                              ? "text-green-300"
                              : "text-amber-300"
                          }
                        >
                          {run.tasks_done}/{run.tasks_total}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-center text-white/60">
                        {run.rounds}
                      </td>
                      <td className="px-3 py-2 text-center text-white/50">
                        {formatDuration(run.duration_seconds)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Comparison panel */}
        {comparison && (
          <div className="mt-6 rounded-xl border border-fuchsia-500/20 bg-slate-900/80 p-6">
            <h2 className="mb-4 text-sm font-bold font-mono text-fuchsia-300">
              Comparison Result
            </h2>
            <div className="grid grid-cols-2 gap-6 text-xs font-mono">
              {/* Run A */}
              <div>
                <h3 className="mb-2 font-semibold text-cyan-300">
                  Run A — {formatDate(comparison.run_a.created_at)}
                </h3>
                <p className="text-white/60 truncate">{comparison.run_a.goal}</p>
                <p className="mt-1 text-white/40">
                  {comparison.run_a.tasks_done}/{comparison.run_a.tasks_total} tasks ·{" "}
                  {comparison.run_a.rounds} rounds ·{" "}
                  {formatDuration(comparison.run_a.duration_seconds)}
                </p>
              </div>
              {/* Run B */}
              <div>
                <h3 className="mb-2 font-semibold text-cyan-300">
                  Run B — {formatDate(comparison.run_b.created_at)}
                </h3>
                <p className="text-white/60 truncate">{comparison.run_b.goal}</p>
                <p className="mt-1 text-white/40">
                  {comparison.run_b.tasks_done}/{comparison.run_b.tasks_total} tasks ·{" "}
                  {comparison.run_b.rounds} rounds ·{" "}
                  {formatDuration(comparison.run_b.duration_seconds)}
                </p>
              </div>
            </div>

            {/* Improvements */}
            {comparison.improvements.length > 0 && (
              <div className="mt-4">
                <h4 className="mb-2 text-xs font-semibold text-green-300 font-mono">
                  Improvements (B vs A)
                </h4>
                <ul className="space-y-1">
                  {comparison.improvements.map((item, i) => (
                    <li
                      key={i}
                      className="rounded bg-green-500/10 px-3 py-1 text-xs font-mono text-green-200"
                    >
                      + {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Regressions */}
            {comparison.regressions.length > 0 && (
              <div className="mt-4">
                <h4 className="mb-2 text-xs font-semibold text-red-300 font-mono">
                  Regressions (B vs A)
                </h4>
                <ul className="space-y-1">
                  {comparison.regressions.map((item, i) => (
                    <li
                      key={i}
                      className="rounded bg-red-500/10 px-3 py-1 text-xs font-mono text-red-200"
                    >
                      - {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {comparison.improvements.length === 0 && comparison.regressions.length === 0 && (
              <p className="mt-4 text-xs font-mono text-white/40">
                No significant differences found between the two runs.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

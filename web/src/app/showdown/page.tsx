"use client";

/**
 * Phase 3-#5 — A/B harness showdown.
 *
 *   /showdown?a=<sessionA>&b=<sessionB>
 *
 * Replays two finished runs side-by-side so the operator can compare
 * how the same goal played out under different harness presets. Each
 * column reuses ``useReplay`` so the timeline cursors are independent
 * and either side can be scrubbed without affecting the other. A
 * ``Sync`` toggle keeps both cursors in lockstep when the operator
 * wants frame-aligned comparison.
 *
 * Live (parallel) A/B is the obvious next step — the existing
 * infrastructure already supports multiple concurrent swarms per user,
 * so the page can be upgraded to ``useSwarm × 2`` without touching
 * this layout. For now we focus on post-hoc comparison since both
 * runs already record everything we need.
 */

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useReplay } from "@/hooks/useReplay";
import Stage from "@/components/Stage";
import TaskPanel from "@/components/TaskPanel";

export default function ShowdownPage() {
  return (
    <Suspense fallback={<div className="h-screen w-screen bg-[#0a0a12]" />}>
      <ShowdownContent />
    </Suspense>
  );
}

function ShowdownContent() {
  const params = useSearchParams();
  const aIdParam = params.get("a");
  const bIdParam = params.get("b");
  const aId = aIdParam ? Number(aIdParam) : null;
  const bId = bIdParam ? Number(bIdParam) : null;

  if (!aId || !bId) {
    return <Picker />;
  }
  return <Comparison sessionA={aId} sessionB={bId} />;
}

function Picker() {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  return (
    <div className="flex h-screen flex-col items-center justify-center gap-4 bg-[#0a0a12] p-4 text-white">
      <h1 className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-2xl font-bold text-transparent">
        🥊 A/B Harness Showdown
      </h1>
      <p className="max-w-md text-center font-mono text-xs text-white/50">
        두 세션의 ID를 입력하면 좌/우로 동시에 재생합니다. 같은 goal로 서로 다른
        harness 프리셋을 돌렸을 때 결과가 어떻게 갈리는지 한눈에 보세요.
      </p>
      <div className="flex flex-col gap-3">
        <label className="flex items-center gap-3 font-mono text-xs text-white/60">
          A
          <input
            value={a}
            onChange={(e) => setA(e.target.value)}
            placeholder="session id"
            className="rounded border border-white/10 bg-slate-900/60 px-3 py-1.5 font-mono text-sm text-white"
          />
        </label>
        <label className="flex items-center gap-3 font-mono text-xs text-white/60">
          B
          <input
            value={b}
            onChange={(e) => setB(e.target.value)}
            placeholder="session id"
            className="rounded border border-white/10 bg-slate-900/60 px-3 py-1.5 font-mono text-sm text-white"
          />
        </label>
        <Link
          href={a && b ? `/showdown?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}` : "#"}
          className={`rounded-xl border px-4 py-2 text-center font-mono text-xs transition ${
            a && b
              ? "border-fuchsia-400/50 bg-fuchsia-500/15 text-fuchsia-100 hover:bg-fuchsia-500/30"
              : "pointer-events-none border-white/10 bg-white/5 text-white/30"
          }`}
        >
          ▶ 시작
        </Link>
      </div>
    </div>
  );
}

interface ComparisonProps {
  sessionA: number;
  sessionB: number;
}

function Comparison({ sessionA, sessionB }: ComparisonProps) {
  const left = useReplay(sessionA);
  const right = useReplay(sessionB);
  const [sync, setSync] = useState(true);

  // ── Sync: when toggled on, scrubbing either column moves the other.
  // We compare *normalised* progress so two runs of different lengths
  // still align at the same phase ("about a quarter through").
  const normalize = useCallback((round: number, first: number, last: number) => {
    if (last <= first) return 0;
    return Math.max(0, Math.min(1, (round - first) / (last - first)));
  }, []);

  const denormalize = useCallback((p: number, first: number, last: number) => {
    return Math.round(first + p * (last - first));
  }, []);

  // Cross-couple the two cursors when ``sync`` is enabled.
  useEffect(() => {
    if (!sync || !left.bundle || !right.bundle) return;
    const p = normalize(left.round, left.bundle.first_round, left.bundle.last_round);
    const target = denormalize(p, right.bundle.first_round, right.bundle.last_round);
    if (target !== right.round) right.setRound(target);
    // Only respond to ``left.round`` changes; right's reciprocal effect
    // would loop forever otherwise. The asymmetry mirrors classic
    // master/slave timeline coupling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [left.round, sync]);

  const summaryFor = useCallback(
    (state: ReturnType<typeof useReplay>["state"]) => {
      if (!state) return { tasksDone: 0, tasksTotal: 0, agents: 0 };
      return {
        tasksDone: state.tasks.filter((t) => t.status === "done").length,
        tasksTotal: state.tasks.length,
        agents: state.agents.length,
      };
    },
    [],
  );

  const sa = useMemo(() => summaryFor(left.state), [left.state, summaryFor]);
  const sb = useMemo(() => summaryFor(right.state), [right.state, summaryFor]);

  const winnerLabel = useMemo(() => {
    if (!left.bundle || !right.bundle) return null;
    if (sa.tasksDone > sb.tasksDone) return "A";
    if (sb.tasksDone > sa.tasksDone) return "B";
    if (left.bundle.last_round < right.bundle.last_round) return "A"; // faster
    if (right.bundle.last_round < left.bundle.last_round) return "B";
    return "tie";
  }, [left.bundle, right.bundle, sa, sb]);

  return (
    <div className="min-h-screen bg-[#0a0a12] p-4 text-white">
      <div className="mx-auto flex max-w-7xl flex-col gap-3">
        <header className="flex items-center justify-between">
          <Link
            href="/admin/runs"
            className="rounded-xl border border-white/10 bg-white/5 px-3 py-1.5 font-mono text-xs text-white/60 hover:bg-white/10"
          >
            ← 런 목록
          </Link>
          <h1 className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-lg font-bold text-transparent">
            🥊 A/B Showdown · {sessionA} vs {sessionB}
          </h1>
          <label className="flex items-center gap-2 font-mono text-xs text-white/60">
            <input
              type="checkbox"
              checked={sync}
              onChange={(e) => setSync(e.target.checked)}
              className="accent-fuchsia-400"
            />
            sync
          </label>
        </header>

        {winnerLabel ? (
          <div className="rounded-xl border border-white/10 bg-slate-950/60 p-3 text-center font-mono text-xs text-white/70">
            현재 선두 ·{" "}
            <span className="font-bold text-fuchsia-300">
              {winnerLabel === "tie" ? "tie" : winnerLabel}
            </span>{" "}
            (A: {sa.tasksDone}/{sa.tasksTotal} · B: {sb.tasksDone}/{sb.tasksTotal})
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <SidePanel
            label="A"
            replay={left}
            summary={sa}
            sessionId={sessionA}
            onScrub={(r) => {
              left.setRound(r);
              if (sync && right.bundle && left.bundle) {
                const p = normalize(
                  r,
                  left.bundle.first_round,
                  left.bundle.last_round,
                );
                right.setRound(
                  denormalize(p, right.bundle.first_round, right.bundle.last_round),
                );
              }
            }}
          />
          <SidePanel
            label="B"
            replay={right}
            summary={sb}
            sessionId={sessionB}
            onScrub={(r) => {
              right.setRound(r);
              if (sync && right.bundle && left.bundle) {
                const p = normalize(
                  r,
                  right.bundle.first_round,
                  right.bundle.last_round,
                );
                left.setRound(
                  denormalize(p, left.bundle.first_round, left.bundle.last_round),
                );
              }
            }}
          />
        </div>
      </div>
    </div>
  );
}

interface SidePanelProps {
  label: string;
  replay: ReturnType<typeof useReplay>;
  summary: { tasksDone: number; tasksTotal: number; agents: number };
  sessionId: number;
  onScrub: (round: number) => void;
}

function SidePanel({ label, replay, summary, sessionId, onScrub }: SidePanelProps) {
  const { bundle, state, round, loading, error } = replay;
  return (
    <section className="flex flex-col gap-2 rounded-2xl border border-white/10 bg-slate-950/60 p-3">
      <div className="flex items-baseline justify-between">
        <h2 className="font-mono text-sm font-semibold uppercase tracking-wider text-white/80">
          {label} · session {sessionId}
        </h2>
        <span className="font-mono text-[10px] text-white/40">
          tasks {summary.tasksDone}/{summary.tasksTotal} · agents {summary.agents}
        </span>
      </div>

      {loading ? (
        <p className="font-mono text-xs text-white/40">불러오는 중…</p>
      ) : error ? (
        <p className="font-mono text-xs text-rose-300">{error}</p>
      ) : !bundle || !state ? (
        <p className="font-mono text-xs text-white/40">데이터 없음</p>
      ) : (
        <>
          <div className="relative aspect-video overflow-hidden rounded-lg border border-white/5">
            <Stage
              agents={state.agents}
              sky={state.sky}
              boss={state.boss}
              cookies={state.cookies}
            />
          </div>
          <input
            type="range"
            min={bundle.first_round}
            max={bundle.last_round}
            value={round}
            onChange={(e) => onScrub(Number(e.target.value))}
            className="h-1 w-full cursor-pointer accent-fuchsia-400"
          />
          <div className="flex items-center justify-between font-mono text-[10px] text-white/50">
            <span>round {round} / {bundle.last_round}</span>
            <span>{state.completed ? "✓ completed" : "× incomplete"}</span>
          </div>
          <details className="rounded border border-white/5 bg-slate-900/40 p-2">
            <summary className="cursor-pointer font-mono text-[10px] text-white/40">
              tasks
            </summary>
            <div className="mt-2 max-h-40 overflow-y-auto">
              <TaskPanel tasks={state.tasks} />
            </div>
          </details>
        </>
      )}
    </section>
  );
}

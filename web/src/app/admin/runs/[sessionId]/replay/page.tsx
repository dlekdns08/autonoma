"use client";

/**
 * Phase 1-#1 — session replay player.
 *
 *   /admin/runs/<sessionId>/replay
 *
 * Walks every persisted ProjectState checkpoint of a finished run as a
 * timeline. The scrubber picks a round, the page reconstructs that
 * round's SwarmState, and the existing Stage / TaskPanel / FileTree
 * components render it read-only.
 *
 * Auto-play is a vanilla setInterval — no MediaSource APIs, no audio
 * decoder, just frame-by-frame state swaps. That keeps replay logic
 * trivial: each frame is a self-contained snapshot, identical to what
 * the WebSocket would have streamed live.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { useReplay } from "@/hooks/useReplay";
import Stage from "@/components/Stage";
import TaskPanel from "@/components/TaskPanel";
import FileTree from "@/components/FileTree";

const PLAY_INTERVAL_MS = 1500;

export default function ReplayPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params?.sessionId ? Number(params.sessionId) : null;
  const { user, loading: authLoading } = useAuth();
  const { bundle, loading, error, round, setRound, step, state } =
    useReplay(sessionId);
  const [playing, setPlaying] = useState(false);

  // Auto-play: advance one round per tick. Stops at the last frame so
  // viewers don't sit on a frozen final state wondering if it crashed.
  useEffect(() => {
    if (!playing || !bundle) return;
    const id = window.setInterval(() => {
      setRound(round + 1);
      if (round >= bundle.last_round) setPlaying(false);
    }, PLAY_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [playing, bundle, round, setRound]);

  const onSliderChange = useCallback(
    (value: number) => {
      setRound(value);
      setPlaying(false);
    },
    [setRound],
  );

  const progressPct = useMemo(() => {
    if (!bundle) return 0;
    const span = Math.max(1, bundle.last_round - bundle.first_round);
    return ((round - bundle.first_round) / span) * 100;
  }, [bundle, round]);

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/40">
        loading…
      </div>
    );
  }
  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/60">
        관리자 권한이 필요합니다.
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0a12] p-4 text-white">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <Link
            href="/admin/runs"
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/60 hover:bg-white/10"
          >
            ← 런 목록
          </Link>
          <div className="font-mono text-xs uppercase tracking-wider text-white/40">
            replay · session {sessionId ?? "?"}
          </div>
        </header>

        {loading ? (
          <div className="rounded-2xl border border-white/10 bg-slate-950/60 p-6 font-mono text-sm text-white/40">
            체크포인트를 불러오는 중…
          </div>
        ) : error ? (
          <div className="rounded-2xl border border-rose-500/30 bg-rose-950/40 p-6 font-mono text-sm text-rose-200">
            {error}
          </div>
        ) : !bundle || !state ? (
          <div className="rounded-2xl border border-white/10 bg-slate-950/60 p-6 font-mono text-sm text-white/40">
            표시할 데이터가 없습니다.
          </div>
        ) : (
          <>
            {/* ── Header / metadata strip ───────────────────────── */}
            <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <h1 className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-xl font-bold text-transparent">
                  {state.project_name || "(이름 없는 프로젝트)"}
                </h1>
                <div className="font-mono text-xs text-white/50">
                  round {round} / {bundle.last_round} · {bundle.frame_count}{" "}
                  체크포인트
                </div>
              </div>
              {state.goal ? (
                <p className="mt-1 font-mono text-xs text-white/50">{state.goal}</p>
              ) : null}
            </section>

            {/* ── Player controls + scrubber ────────────────────── */}
            <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-3">
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => step(-1)}
                  className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-xs text-white/70 hover:bg-white/10"
                  aria-label="이전 라운드"
                >
                  ⏮
                </button>
                <button
                  type="button"
                  onClick={() => setPlaying((p) => !p)}
                  className="rounded-lg border border-fuchsia-400/40 bg-fuchsia-500/15 px-3 py-1 font-mono text-xs text-fuchsia-100 hover:bg-fuchsia-500/30"
                >
                  {playing ? "⏸ 일시정지" : "▶ 재생"}
                </button>
                <button
                  type="button"
                  onClick={() => step(+1)}
                  className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-xs text-white/70 hover:bg-white/10"
                  aria-label="다음 라운드"
                >
                  ⏭
                </button>
                <div className="ml-2 flex flex-1 items-center gap-2">
                  <input
                    type="range"
                    min={bundle.first_round}
                    max={bundle.last_round}
                    value={round}
                    onChange={(e) => onSliderChange(Number(e.target.value))}
                    className="h-1.5 flex-1 cursor-pointer accent-fuchsia-400"
                    aria-label="타임라인 스크러버"
                  />
                  <span className="w-16 text-right font-mono text-xs tabular-nums text-white/60">
                    {round}
                  </span>
                </div>
              </div>
              <div className="mt-2 h-1 w-full overflow-hidden rounded bg-white/10">
                <div
                  className="h-full bg-gradient-to-r from-fuchsia-400 to-cyan-400 transition-[width] duration-200"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            </section>

            {/* ── Stage + side panels ───────────────────────────── */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <section className="overflow-hidden rounded-2xl border border-white/10 bg-slate-950/60 lg:col-span-2">
                <div className="relative aspect-video">
                  <Stage
                    agents={state.agents}
                    sky={state.sky}
                    boss={state.boss}
                    cookies={state.cookies}
                  />
                </div>
              </section>

              <section className="flex flex-col gap-4">
                <div className="rounded-2xl border border-white/10 bg-slate-950/60 p-3">
                  <h2 className="mb-2 font-mono text-xs font-semibold uppercase tracking-wider text-white/60">
                    Tasks ({state.tasks.length})
                  </h2>
                  <TaskPanel tasks={state.tasks} />
                </div>
                <div className="rounded-2xl border border-white/10 bg-slate-950/60 p-3">
                  <h2 className="mb-2 font-mono text-xs font-semibold uppercase tracking-wider text-white/60">
                    Files ({state.files.length})
                  </h2>
                  <FileTree files={state.files} sessionId={sessionId} />
                </div>
              </section>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

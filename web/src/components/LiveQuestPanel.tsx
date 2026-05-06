"use client";

/**
 * Feature #14 — Live quest designer panel.
 *
 * Viewers propose quests (free-text, capped at 256 chars), upvote each
 * other's proposals, and watch the "🌟 ACTIVE" banner light up when a
 * host promotes one to the round goal. Admins (host dashboard) get
 * Activate/Complete buttons inline.
 *
 * Update sources:
 *
 * 1. ``useLiveQuests`` polls ``GET /api/quests`` every 5 seconds.
 * 2. The parent may pass ``liveQuestEvent`` — a single WS-bus payload
 *    drained from ``useSwarm`` — for sub-poll-interval updates. We
 *    apply those events optimistically into the local cache and let
 *    the next poll reconcile.
 *
 * Style mirrors ``LiveChatPollPanel`` so the two panels can sit side
 * by side in the dashboard sidebar without visual drift.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  activateQuest,
  completeQuest,
  QuestApiError,
  type Quest,
  type QuestEvent,
  type QuestStatus,
} from "@/lib/quests";
import { useLiveQuests } from "@/hooks/useLiveQuests";

const TEXT_MAX = 256;
const VOTE_COOLDOWN_MS = 6_000;

export interface LiveQuestPanelProps {
  sessionId: number;
  isAdmin: boolean;
  /** Optional sub-poll-interval push from the swarm WS. Each new event
   *  reference triggers an apply pass. The parent should hand us the
   *  *latest* relevant event (not a queue) — the polling loop will
   *  reconcile any drops on the next tick. */
  liveQuestEvent?: QuestEvent | null;
}

interface RowGroup {
  proposed: Quest[];
  active: Quest[];
  completed: Quest[];
}

function bucket(quests: Quest[]): RowGroup {
  const proposed: Quest[] = [];
  const active: Quest[] = [];
  const completed: Quest[] = [];
  const list = Array.isArray(quests) ? quests : [];
  for (const q of list) {
    if (q.status === "active") active.push(q);
    else if (q.status === "completed") completed.push(q);
    else if (q.status === "proposed") proposed.push(q);
    // ``rejected`` rows are intentionally hidden from the viewer panel.
  }
  // Highest-voted proposals first; completed listed newest-first.
  proposed.sort((a, b) => b.votes - a.votes || a.id - b.id);
  completed.sort((a, b) => b.id - a.id);
  return { proposed, active, completed };
}

function statusLabel(status: QuestStatus): string {
  switch (status) {
    case "proposed":
      return "투표 중";
    case "active":
      return "진행 중";
    case "completed":
      return "완료";
    case "rejected":
      return "기각";
  }
}

export default function LiveQuestPanel({
  sessionId,
  isAdmin,
  liveQuestEvent,
}: LiveQuestPanelProps) {
  const { quests, propose, vote, refresh, error } = useLiveQuests(sessionId);

  // ----- Composer state ----------------------------------------------------
  const [draft, setDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // ----- Vote-cooldown bookkeeping ----------------------------------------
  // Per-quest unix-ms when the vote button becomes interactive again.
  const [cooldowns, setCooldowns] = useState<Record<number, number>>({});
  const [now, setNow] = useState(() => Date.now());
  const tickPending = useMemo(
    () => Object.values(cooldowns).some((t) => t > now),
    [cooldowns, now],
  );
  useEffect(() => {
    if (!tickPending) return;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [tickPending]);

  const cooldownFor = useCallback(
    (questId: number): number => {
      const until = cooldowns[questId];
      if (!until) return 0;
      return Math.max(0, until - now);
    },
    [cooldowns, now],
  );

  // ----- WS event ingest ---------------------------------------------------
  // Track which event references we've already applied so re-renders don't
  // double-fire. We use a ref instead of state so the apply pass doesn't
  // schedule a render of its own.
  const lastAppliedEvent = useRef<QuestEvent | null>(null);
  useEffect(() => {
    if (!liveQuestEvent) return;
    if (lastAppliedEvent.current === liveQuestEvent) return;
    lastAppliedEvent.current = liveQuestEvent;
    // The simplest correct policy: any quest event invalidates the
    // cached list. Polling would catch this within 5s, but a refresh
    // here gives the host immediate feedback after activate/complete.
    void refresh();
  }, [liveQuestEvent, refresh]);

  // ----- Actions -----------------------------------------------------------
  const handlePropose = useCallback(async () => {
    if (!draft.trim() || submitting) return;
    setSubmitting(true);
    try {
      await propose(draft.slice(0, TEXT_MAX));
      setDraft("");
    } finally {
      setSubmitting(false);
    }
  }, [draft, propose, submitting]);

  const handleVote = useCallback(
    async (questId: number) => {
      if (cooldownFor(questId) > 0) return;
      // Pre-arm the cooldown so a slow network can't double-submit.
      setCooldowns((prev) => ({
        ...prev,
        [questId]: Date.now() + VOTE_COOLDOWN_MS,
      }));
      try {
        await vote(questId);
      } catch (err) {
        // 409 = already voted — keep cooldown engaged. Other errors
        // release the cooldown so the user can retry.
        if (!(err instanceof QuestApiError) || err.status !== 409) {
          setCooldowns((prev) => {
            const next = { ...prev };
            delete next[questId];
            return next;
          });
        }
      }
    },
    [cooldownFor, vote],
  );

  const handleActivate = useCallback(
    async (questId: number) => {
      try {
        await activateQuest(questId);
        await refresh();
      } catch {
        // ``error`` from the hook will pick up the next refresh failure.
      }
    },
    [refresh],
  );

  const handleComplete = useCallback(
    async (questId: number) => {
      try {
        await completeQuest(questId);
        await refresh();
      } catch {
        // see above
      }
    },
    [refresh],
  );

  // ----- Derived view model -----------------------------------------------
  const groups = useMemo(() => bucket(quests), [quests]);
  const charCount = draft.length;
  const charOverLimit = charCount > TEXT_MAX;

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-white/10 bg-slate-950/60 p-3 text-white">
      <div className="flex items-center justify-between">
        <h3 className="font-mono text-xs font-semibold uppercase tracking-wider text-white/70">
          🗺 Live Quest Designer
        </h3>
        <span className="rounded bg-white/5 px-2 py-0.5 font-mono text-[10px] text-white/40">
          {groups.proposed.length} 제안 · {groups.active.length} 진행
        </span>
      </div>

      {/* Active quest banner ------------------------------------------------ */}
      {groups.active.map((q) => (
        <div
          key={`active-${q.id}`}
          className="rounded-xl border border-amber-300/40 bg-gradient-to-r from-amber-500/20 via-fuchsia-500/15 to-amber-500/20 p-3"
        >
          <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-amber-200">
            🌟 ACTIVE
            {q.activated_round != null ? (
              <span className="text-white/50">round {q.activated_round}</span>
            ) : null}
          </div>
          <p className="mt-1 font-mono text-sm text-white/90">{q.text}</p>
          <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-white/50">
            <span>{q.votes} votes</span>
            {isAdmin ? (
              <button
                type="button"
                onClick={() => handleComplete(q.id)}
                className="rounded border border-emerald-300/40 bg-emerald-500/15 px-2 py-1 text-emerald-100 hover:bg-emerald-500/30"
              >
                ✓ 완료 처리
              </button>
            ) : null}
          </div>
        </div>
      ))}

      {/* Composer ---------------------------------------------------------- */}
      <div className="flex flex-col gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value.slice(0, TEXT_MAX))}
          rows={2}
          maxLength={TEXT_MAX}
          placeholder="새 퀘스트를 제안하세요 (예: 보스에게 사과 편지를 쓰자)"
          className="rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-xs text-white placeholder:text-white/30 focus:border-fuchsia-400/50 focus:outline-none"
        />
        <div className="flex items-center justify-between gap-2">
          <span
            className={`font-mono text-[10px] tabular-nums ${
              charOverLimit ? "text-rose-300" : "text-white/40"
            }`}
          >
            {charCount}/{TEXT_MAX}
          </span>
          <button
            type="button"
            onClick={handlePropose}
            disabled={submitting || !draft.trim() || charOverLimit}
            className="rounded-lg border border-fuchsia-400/40 bg-fuchsia-500/15 px-3 py-1 font-mono text-xs text-fuchsia-100 hover:bg-fuchsia-500/30 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? "제출 중…" : "+ 제안"}
          </button>
        </div>
      </div>

      {/* Proposals --------------------------------------------------------- */}
      <ul className="flex flex-col gap-1.5">
        {groups.proposed.length === 0 ? (
          <li className="rounded border border-dashed border-white/10 px-2 py-3 text-center font-mono text-[10px] text-white/30">
            아직 제안된 퀘스트가 없어요.
          </li>
        ) : (
          groups.proposed.map((q) => {
            const remaining = cooldownFor(q.id);
            const cooling = remaining > 0;
            const remainingSec = Math.ceil(remaining / 1000);
            return (
              <li
                key={q.id}
                className="flex items-center gap-2 rounded border border-white/10 bg-slate-900/40 px-2 py-1.5"
              >
                <span className="w-10 text-right font-mono text-xs tabular-nums text-white/70">
                  {q.votes}
                </span>
                <p className="flex-1 font-mono text-xs text-white/85">
                  {q.text}
                </p>
                <button
                  type="button"
                  onClick={() => handleVote(q.id)}
                  disabled={cooling}
                  aria-label={`vote for quest ${q.id}`}
                  className="rounded border border-cyan-300/40 bg-cyan-500/15 px-2 py-1 font-mono text-[10px] text-cyan-100 hover:bg-cyan-500/30 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/5 disabled:text-white/30"
                >
                  {cooling ? `${remainingSec}s` : "▲ vote"}
                </button>
                {isAdmin ? (
                  <button
                    type="button"
                    onClick={() => handleActivate(q.id)}
                    className="rounded border border-amber-300/40 bg-amber-500/15 px-2 py-1 font-mono text-[10px] text-amber-100 hover:bg-amber-500/30"
                  >
                    ▶ 활성화
                  </button>
                ) : null}
              </li>
            );
          })
        )}
      </ul>

      {/* Recently completed ------------------------------------------------ */}
      {groups.completed.length > 0 ? (
        <details className="rounded border border-white/5 bg-slate-900/30 px-2 py-1">
          <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-wider text-white/40">
            완료된 퀘스트 ({groups.completed.length})
          </summary>
          <ul className="mt-1 flex flex-col gap-1">
            {groups.completed.slice(0, 8).map((q) => (
              <li
                key={q.id}
                className="flex items-center gap-2 font-mono text-[10px] text-white/50"
              >
                <span className="rounded bg-emerald-500/15 px-1 text-emerald-200">
                  {statusLabel(q.status)}
                </span>
                <span className="truncate">{q.text}</span>
                {q.completed_round != null ? (
                  <span className="ml-auto tabular-nums text-white/30">
                    r{q.completed_round}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {error ? (
        <p className="font-mono text-[10px] text-rose-300">{error}</p>
      ) : null}
    </div>
  );
}

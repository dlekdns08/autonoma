"use client";

/**
 * Phase 2-#2 — viewer poll control panel.
 *
 * The host opens a poll with a question + options + duration. While the
 * poll is active, every inbound live-chat message is tallied (instead
 * of injected as feedback) by the ExternalInputRouter. We render the
 * live tally from ``external.vote`` / ``external.poll_*`` bus events
 * surfaced through the WS layer.
 *
 * Designed to be dropped into the dashboard sidebar — fixed width,
 * collapses to a thin "open poll" button when no poll is active.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

export interface PollState {
  pollId: string;
  question: string;
  options: string[];
  closesAtMs: number; // wall-clock estimate (Date.now + duration)
  tallies: Record<string, number>;
  voterCount: number;
}

export interface PollEvent {
  type:
    | "external.poll_opened"
    | "external.poll_closed"
    | "external.vote";
  data: Record<string, unknown>;
}

export interface LiveChatPollPanelProps {
  /** Latest poll-related event from the WS event bus. The dashboard's
   *  ``useSwarm`` hook normally exposes a ``lastBusEvent`` (see Stage 0
   *  bus tap); we accept the discrete event here so this component
   *  doesn't have to subscribe directly. */
  pollEvent?: PollEvent | null;
  /** Hide the panel when offline so it doesn't compete with the
   *  composer. */
  enabled?: boolean;
}

const DEFAULT_DURATION_SEC = 30;

export default function LiveChatPollPanel({
  pollEvent,
  enabled = true,
}: LiveChatPollPanelProps) {
  const [poll, setPoll] = useState<PollState | null>(null);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Composer state
  const [question, setQuestion] = useState("");
  const [optionsText, setOptionsText] = useState("");
  const [durationSec, setDurationSec] = useState(DEFAULT_DURATION_SEC);

  // Wall-clock countdown
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!poll) return;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [poll]);
  const remainingMs = poll ? Math.max(0, poll.closesAtMs - now) : 0;
  const remainingSec = Math.ceil(remainingMs / 1000);

  // Auto-clear when the deadline passes (server also closes; this is a
  // local fallback so the UI doesn't sit on a dead timer).
  useEffect(() => {
    if (poll && remainingMs <= 0) {
      const t = window.setTimeout(() => setPoll(null), 1500);
      return () => window.clearTimeout(t);
    }
  }, [poll, remainingMs]);

  // Apply incoming events.
  useEffect(() => {
    if (!pollEvent) return;
    if (pollEvent.type === "external.poll_opened") {
      const data = pollEvent.data as {
        poll_id: string;
        question: string;
        options: string[];
        closes_at_monotonic?: number;
      };
      // The server uses ``time.monotonic`` — we can't compare it to
      // ``Date.now()``, so instead the panel reuses the duration the
      // host typed. The server clock is authoritative for tally-cutoff.
      setPoll({
        pollId: data.poll_id,
        question: data.question,
        options: data.options,
        closesAtMs: Date.now() + durationSec * 1000,
        tallies: Object.fromEntries(data.options.map((o) => [o, 0])),
        voterCount: 0,
      });
    } else if (pollEvent.type === "external.vote") {
      const data = pollEvent.data as {
        poll_id: string;
        tallies: Record<string, number>;
      };
      setPoll((prev) =>
        prev && prev.pollId === data.poll_id
          ? { ...prev, tallies: { ...prev.tallies, ...data.tallies } }
          : prev,
      );
    } else if (pollEvent.type === "external.poll_closed") {
      const data = pollEvent.data as {
        poll_id: string;
        tallies?: Record<string, number>;
        voter_count?: number;
      };
      setPoll((prev) =>
        prev && prev.pollId === data.poll_id
          ? {
              ...prev,
              tallies: data.tallies ?? prev.tallies,
              voterCount: data.voter_count ?? prev.voterCount,
              closesAtMs: Date.now(), // mark as closed
            }
          : prev,
      );
    }
  }, [pollEvent, durationSec]);

  const open = useCallback(async () => {
    const opts = optionsText
      .split(/\n|,/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!question.trim() || opts.length < 2) {
      setError("질문과 옵션 2개 이상이 필요합니다.");
      return;
    }
    setOpening(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/bridges/livechat/poll/open`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          poll_id: `p${Date.now()}`,
          question: question.trim(),
          options: opts,
          duration_sec: durationSec,
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const msg =
          (detail?.detail?.message as string | undefined) ??
          `HTTP ${res.status}`;
        throw new Error(msg);
      }
      // Server emits ``external.poll_opened`` which our event handler
      // picks up — we don't optimistically set state here so the local
      // view stays consistent with the bus.
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setOpening(false);
    }
  }, [question, optionsText, durationSec]);

  const close = useCallback(async () => {
    try {
      await fetch(`${API_BASE_URL}/api/bridges/livechat/poll/close`, {
        method: "POST",
        credentials: "include",
      });
      setPoll(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const totalVotes = useMemo(
    () =>
      poll
        ? Object.values(poll.tallies).reduce((s, n) => s + (n || 0), 0)
        : 0,
    [poll],
  );

  if (!enabled) return null;

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-white/10 bg-slate-950/60 p-3 text-white">
      <div className="flex items-center justify-between">
        <h3 className="font-mono text-xs font-semibold uppercase tracking-wider text-white/70">
          🗳 Viewer Poll
        </h3>
        {poll ? (
          <span className="rounded bg-amber-500/20 px-2 py-0.5 font-mono text-[10px] text-amber-200">
            {remainingSec}s left
          </span>
        ) : (
          <span className="rounded bg-white/5 px-2 py-0.5 font-mono text-[10px] text-white/40">
            idle
          </span>
        )}
      </div>

      {poll ? (
        <div className="flex flex-col gap-2">
          <div className="font-mono text-sm text-white/85">{poll.question}</div>
          <ul className="flex flex-col gap-1.5">
            {poll.options.map((opt) => {
              const n = poll.tallies[opt] ?? 0;
              const pct =
                totalVotes > 0 ? (n / totalVotes) * 100 : 0;
              return (
                <li key={opt} className="flex items-center gap-2">
                  <div className="relative h-5 flex-1 overflow-hidden rounded bg-white/5">
                    <div
                      className="absolute inset-y-0 left-0 bg-gradient-to-r from-fuchsia-400 to-cyan-400 transition-[width] duration-500"
                      style={{ width: `${pct}%` }}
                    />
                    <span className="absolute inset-0 flex items-center px-2 font-mono text-[11px] text-white/90 mix-blend-difference">
                      {opt}
                    </span>
                  </div>
                  <span className="w-10 text-right font-mono text-xs tabular-nums text-white/70">
                    {n}
                  </span>
                </li>
              );
            })}
          </ul>
          <div className="flex items-center justify-between font-mono text-[10px] text-white/40">
            <span>{poll.voterCount} 명 투표 · 총 {totalVotes} 표</span>
            <button
              type="button"
              onClick={close}
              className="rounded border border-white/10 bg-white/5 px-2 py-1 text-white/60 hover:bg-white/10"
            >
              조기 종료
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="질문을 입력하세요"
            className="rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-xs text-white placeholder:text-white/30 focus:border-fuchsia-400/50 focus:outline-none"
          />
          <textarea
            value={optionsText}
            onChange={(e) => setOptionsText(e.target.value)}
            rows={3}
            placeholder={"옵션을 줄바꿈 또는 쉼표로 구분\n예: blue, red, green"}
            className="rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-xs text-white placeholder:text-white/30 focus:border-fuchsia-400/50 focus:outline-none"
          />
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1 font-mono text-[10px] text-white/40">
              지속(초)
              <input
                type="number"
                min={5}
                max={600}
                value={durationSec}
                onChange={(e) =>
                  setDurationSec(
                    Math.max(5, Math.min(600, Number(e.target.value))),
                  )
                }
                className="w-16 rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-xs text-white"
              />
            </label>
            <button
              type="button"
              onClick={open}
              disabled={opening}
              className="ml-auto rounded-lg border border-fuchsia-400/40 bg-fuchsia-500/15 px-3 py-1 font-mono text-xs text-fuchsia-100 hover:bg-fuchsia-500/30 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {opening ? "여는 중…" : "▶ 폴 시작"}
            </button>
          </div>
          {error ? (
            <p className="font-mono text-[10px] text-rose-300">{error}</p>
          ) : null}
        </div>
      )}
    </div>
  );
}

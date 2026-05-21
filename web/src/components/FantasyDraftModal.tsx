"use client";

/**
 * Viewer Fantasy Draft — modal picker.
 *
 * Lists the agents currently live in the session and lets the viewer
 * tick exactly three to lock in as their fantasy roster. While open we
 * also surface the live scoreboard so the viewer can see where they
 * stand vs. other spectators.
 *
 * Auth-wise this is mounted from ``/watch/<code>`` so the caller may
 * be a guest cookie session — no admin rights required. The submit
 * button stays disabled until the user picks exactly three distinct
 * agents.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useModalA11y } from "@/hooks/useModalA11y";
import { useFantasyDraft } from "@/hooks/useFantasyDraft";

const ROSTER_SIZE = 3;

interface Props {
  sessionId: number;
  onClose: () => void;
}

export default function FantasyDraftModal({ sessionId, onClose }: Props) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const dialogRef = useModalA11y<HTMLDivElement>({ onEscape: onClose });

  const {
    agents,
    rows,
    myRank,
    myPicks,
    error,
    submitting,
    refreshAgents,
    submit,
  } = useFantasyDraft(sessionId);

  // Local picker state. Seeded from ``myPicks`` (the caller's existing
  // submitted roster) on first paint so the modal re-opens with a
  // preselected set. We intentionally don't keep the local state in
  // sync with ``myPicks`` afterwards — once the user starts editing,
  // a poll-induced refresh shouldn't yank their selection.
  const [picks, setPicks] = useState<string[]>(() => myPicks ?? []);
  const seededRef = useRef(false);
  useEffect(() => {
    if (seededRef.current) return;
    if (myPicks && myPicks.length > 0) {
      setPicks(myPicks);
      seededRef.current = true;
    }
  }, [myPicks]);

  // Fetch the agent roster on mount (and on session change). The
  // scoreboard is fetched by the hook's polling loop.
  useEffect(() => {
    void refreshAgents();
  }, [refreshAgents]);

  const togglePick = (name: string) => {
    setPicks((prev) => {
      if (prev.includes(name)) {
        return prev.filter((n) => n !== name);
      }
      if (prev.length >= ROSTER_SIZE) {
        // Already at cap — flash an error message instead of silently
        // ignoring so the viewer understands why nothing happened.
        return prev;
      }
      return [...prev, name];
    });
  };

  const canSubmit = picks.length === ROSTER_SIZE && !submitting;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    try {
      await submit(picks);
      // Close on success — the parent's rank chip will update on the
      // next scoreboard poll. We keep the modal open on failure so the
      // viewer can adjust.
      onClose();
    } catch {
      // The hook already stores the error message in ``error``; no
      // need to re-handle here.
    }
  };

  const agentList = useMemo(
    () => (Array.isArray(agents) ? agents : []),
    [agents],
  );
  const scoreRows = useMemo(
    () => (Array.isArray(rows) ? rows : []),
    [rows],
  );

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fade-in"
      onClick={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="fantasy-draft-title"
        className="w-full max-w-md max-h-[90vh] overflow-y-auto rounded-2xl border border-amber-500/30 bg-gradient-to-b from-slate-900 to-slate-950 shadow-2xl shadow-amber-500/10"
      >
        {/* Header */}
        <div className="relative px-6 py-4 bg-gradient-to-r from-amber-950/40 via-orange-950/30 to-amber-950/40 border-b border-amber-500/20">
          <button
            onClick={onClose}
            aria-label="Close fantasy draft"
            className="absolute top-3 right-3 text-white/40 hover:text-white/80 transition-colors text-lg"
          >
            ✕
          </button>
          <h2
            id="fantasy-draft-title"
            className="font-mono text-base text-white"
          >
            🏆 Fantasy Draft
          </h2>
          <p className="mt-1 text-[11px] text-white/60">
            Pick {ROSTER_SIZE} agents — score = total XP + 10×achievements.
          </p>
          <div className="mt-2 flex items-center gap-2 text-[11px] text-white/70">
            <span className="rounded bg-white/10 px-2 py-0.5 font-mono">
              {picks.length}/{ROSTER_SIZE} picked
            </span>
            {myRank !== null ? (
              <span className="rounded bg-amber-500/20 px-2 py-0.5 font-mono text-amber-200">
                your rank: #{myRank}
              </span>
            ) : null}
          </div>
        </div>

        {/* Roster picker */}
        <div className="px-4 py-3">
          {agentList.length === 0 ? (
            <p className="px-2 py-6 text-center font-mono text-xs text-white/40">
              Waiting for the host to spawn agents…
            </p>
          ) : (
            <ul className="space-y-1.5">
              {agentList.map((agent) => {
                const checked = picks.includes(agent.name);
                const disabled = !checked && picks.length >= ROSTER_SIZE;
                return (
                  <li key={agent.name}>
                    <label
                      className={`flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 transition-colors ${
                        checked
                          ? "border-amber-500/60 bg-amber-500/10"
                          : disabled
                            ? "border-white/5 bg-white/[0.02] opacity-50 cursor-not-allowed"
                            : "border-white/10 bg-white/5 hover:bg-white/10"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={disabled}
                        onChange={() => togglePick(agent.name)}
                        className="h-4 w-4 cursor-pointer accent-amber-500"
                        aria-label={`Pick ${agent.name}`}
                      />
                      <span className="text-xl leading-none">
                        {agent.emoji || "🤖"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-mono text-sm text-white">
                          {agent.name}
                        </div>
                        <div className="truncate text-[10px] text-white/50">
                          {agent.role || "agent"}
                          {agent.mood ? ` · ${agent.mood}` : ""}
                        </div>
                      </div>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Submit */}
        <div className="border-t border-white/10 px-4 py-3">
          {error ? (
            <p className="mb-2 rounded bg-rose-500/10 px-2 py-1 text-[11px] text-rose-300">
              {error}
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={!canSubmit}
            className={`w-full rounded-lg px-3 py-2 font-mono text-sm transition-colors ${
              canSubmit
                ? "bg-amber-500 text-black hover:bg-amber-400"
                : "bg-white/10 text-white/40 cursor-not-allowed"
            }`}
          >
            {submitting ? "Submitting…" : `Lock in ${ROSTER_SIZE}-agent roster`}
          </button>
        </div>

        {/* Scoreboard */}
        <div className="border-t border-white/10 px-4 py-3">
          <h3 className="mb-2 font-mono text-[11px] uppercase tracking-wider text-white/50">
            Scoreboard
          </h3>
          {scoreRows.length === 0 ? (
            <p className="px-1 py-3 text-center font-mono text-[11px] text-white/30">
              No drafts yet — be the first!
            </p>
          ) : (
            <ol className="space-y-1">
              {scoreRows.slice(0, 10).map((row, idx) => (
                <li
                  key={`${row.viewer_name}-${idx}`}
                  className="flex items-center justify-between gap-2 rounded bg-white/5 px-2 py-1 font-mono text-[11px]"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="w-5 text-right text-white/40">
                      #{idx + 1}
                    </span>
                    <span className="truncate text-white/80">
                      {row.viewer_name || "viewer"}
                    </span>
                  </span>
                  <span className="text-amber-300">{row.score}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

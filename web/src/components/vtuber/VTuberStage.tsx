"use client";

/**
 * Twitch-style VTuber panel.
 *
 *   ┌──────────────── spotlight ─────────────────┐
 *   │                                            │
 *   │          [big face of current speaker]     │
 *   │                                            │
 *   │          "speech bubble text here"         │
 *   └────────────────────────────────────────────┘
 *   ┌──────────────── gallery ────────────────────┐
 *   │  [f1]  [f2]  [f3]  [f4]                     │  ← all agents
 *   └─────────────────────────────────────────────┘
 *
 * The spotlight auto-switches to whoever started speaking most
 * recently. Clicking a gallery tile pins the spotlight to that agent
 * until a new utterance takes over.
 *
 * We intentionally avoid a "most active speaker" heuristic based on
 * amplitude — by the time audio starts decoding on the client, the
 * backend has already told us which agent is speaking via the
 * `agent.speech` event. The hook below listens for that directly.
 */

import { useEffect, useRef, useState } from "react";
import type { AgentData } from "@/lib/types";
import VTuberFace from "./VTuberFace";

interface Props {
  agents: AgentData[];
  /** Live amplitude feed for lip-sync. */
  getMouthAmplitude?: (name: string) => number;
  /** Set of names currently producing audio, from useAgentVoice. */
  speakingAgents: Set<string>;
  /** Click → open agent modal in the parent. */
  onSelectAgent?: (name: string) => void;
}

export default function VTuberStage({
  agents,
  getMouthAmplitude,
  speakingAgents,
  onSelectAgent,
}: Props) {
  // ── Spotlight selection ──────────────────────────────────────────
  //
  // We latch onto the first name in `speakingAgents`; when that set
  // empties we keep the previous spotlight so the panel doesn't flicker
  // to an empty state between utterances.
  const [pinned, setPinned] = useState<string | null>(null);
  const [lastSpeaker, setLastSpeaker] = useState<string | null>(null);
  const lastSpeakerRef = useRef<string | null>(null);

  useEffect(() => {
    if (speakingAgents.size === 0) return;
    const next = Array.from(speakingAgents)[0];
    if (next !== lastSpeakerRef.current) {
      lastSpeakerRef.current = next;
      setLastSpeaker(next);
    }
  }, [speakingAgents]);

  const spotlightName =
    pinned ?? lastSpeaker ?? agents[0]?.name ?? null;
  const spotlightAgent =
    spotlightName ? agents.find((a) => a.name === spotlightName) : null;

  if (agents.length === 0) {
    return (
      <div className="flex h-full items-center justify-center rounded-xl border border-fuchsia-500/20 bg-slate-950/60">
        <p className="font-mono text-sm text-white/40">Awaiting cast…</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2 overflow-hidden">
      {/* ── Spotlight ────────────────────────────────────────────── */}
      <div className="relative flex-1 min-h-0 overflow-hidden rounded-xl border border-fuchsia-500/20 bg-gradient-to-b from-slate-950 to-slate-900">
        {/* subtle grid backdrop */}
        <div
          className="absolute inset-0 opacity-[0.08]"
          style={{
            backgroundImage:
              "linear-gradient(rgba(244,114,182,0.6) 1px, transparent 1px), linear-gradient(90deg, rgba(244,114,182,0.6) 1px, transparent 1px)",
            backgroundSize: "40px 40px",
          }}
        />

        {spotlightAgent && (
          <div
            key={spotlightAgent.name}
            className="relative flex h-full flex-col items-center justify-center px-4 animate-[spotlight-in_360ms_ease-out]"
          >
            <div className="h-[78%] max-h-[420px] w-auto aspect-[200/260]">
              <VTuberFace
                agent={spotlightAgent}
                getMouthAmplitude={getMouthAmplitude}
                spotlight
                onClick={onSelectAgent ? () => onSelectAgent(spotlightAgent.name) : undefined}
              />
            </div>

            {/* Speech line — pulled directly from agent.speech so it
             *  tracks the most recent utterance without needing a
             *  separate event queue. */}
            {spotlightAgent.speech && (
              <div className="mt-2 max-w-[80%] rounded-xl border border-fuchsia-500/30 bg-black/70 px-3 py-1.5 text-center font-mono text-sm text-fuchsia-100 shadow-lg">
                {spotlightAgent.speech}
              </div>
            )}

            {/* Pinned indicator — tells the host they're overriding
             *  the auto-follow. */}
            {pinned && (
              <button
                type="button"
                onClick={() => setPinned(null)}
                className="absolute right-3 top-3 rounded border border-amber-400/40 bg-amber-500/10 px-2 py-0.5 font-mono text-[10px] text-amber-300 hover:bg-amber-500/20"
              >
                pinned · unpin
              </button>
            )}
          </div>
        )}
      </div>

      {/* ── Gallery strip ─────────────────────────────────────────── */}
      <div className="flex shrink-0 gap-2 overflow-x-auto rounded-xl border border-cyan-500/15 bg-slate-950/60 px-2 py-2 scrollbar-thin">
        {agents.map((agent) => {
          const isSpeaking = speakingAgents.has(agent.name);
          const isFocus = spotlightName === agent.name;
          return (
            <button
              key={agent.name}
              type="button"
              onClick={() => setPinned(agent.name)}
              className={`relative flex shrink-0 flex-col items-center rounded-lg border p-1 transition-all ${
                isFocus
                  ? "border-fuchsia-500/70 bg-fuchsia-500/10"
                  : "border-white/10 hover:border-white/30"
              }`}
              style={{ width: 72 }}
            >
              <div className="w-full aspect-[200/260]">
                <VTuberFace
                  agent={agent}
                  getMouthAmplitude={getMouthAmplitude}
                />
              </div>
              {isSpeaking && (
                <span className="absolute right-1 top-1 h-2 w-2 animate-pulse rounded-full bg-fuchsia-400 shadow-[0_0_6px_rgba(244,114,182,0.9)]" />
              )}
            </button>
          );
        })}
      </div>

      <style jsx>{`
        @keyframes spotlight-in {
          0% {
            opacity: 0;
            transform: scale(0.96);
          }
          100% {
            opacity: 1;
            transform: scale(1);
          }
        }
      `}</style>
    </div>
  );
}

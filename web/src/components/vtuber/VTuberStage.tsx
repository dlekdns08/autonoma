"use client";

/**
 * Full-body VTuber panel — designed to occupy the whole left column.
 *
 *   ┌─────────────────────────────┐
 *   │                             │
 *   │      [ full-body VRM ]      │  ← spotlight, ~70% of height
 *   │                             │
 *   │                             │
 *   │  ┌───────────────────────┐  │
 *   │  │   speech bubble       │  │  ← overlaid near the model's feet
 *   │  └───────────────────────┘  │
 *   ├─────────────────────────────┤
 *   │ [tile][tile][tile][tile]    │  ← gallery, one per agent
 *   └─────────────────────────────┘
 *
 * The spotlight auto-switches to whoever started speaking most
 * recently (driven by `useAgentVoice.speakingAgents`). When the set
 * empties we hold the previous speaker in the spotlight so it doesn't
 * flicker to an empty frame. Clicking a gallery tile pins the spotlight
 * until the next utterance from someone else takes over.
 */

import { useEffect, useRef, useState } from "react";
import type { AgentData } from "@/lib/types";
import VRMCharacter from "./VRMCharacter";
import { creditForAgent } from "./vrmCredits";

interface Props {
  agents: AgentData[];
  /** Live amplitude feed for lip-sync. */
  getMouthAmplitude?: (name: string) => number;
  /** Set of names currently producing audio, from useAgentVoice. */
  speakingAgents: Set<string>;
  /** Click → open agent modal in the parent. */
  onSelectAgent?: (name: string) => void;
}

const MOOD_COLORS: Record<string, string> = {
  happy: "from-emerald-500/20 to-transparent",
  excited: "from-yellow-500/25 to-transparent",
  proud: "from-fuchsia-500/25 to-transparent",
  frustrated: "from-red-500/25 to-transparent",
  worried: "from-orange-500/20 to-transparent",
  relaxed: "from-cyan-500/20 to-transparent",
  determined: "from-amber-500/25 to-transparent",
  focused: "from-blue-500/20 to-transparent",
};

export default function VTuberStage({
  agents,
  getMouthAmplitude,
  speakingAgents,
  onSelectAgent,
}: Props) {
  const [pinned, setPinned] = useState<string | null>(null);
  const [lastSpeaker, setLastSpeaker] = useState<string | null>(null);
  const lastSpeakerRef = useRef<string | null>(null);
  // Bumped when the user clicks "reset view" — VRMCharacter watches this
  // and snaps the camera back to the default full-body framing.
  const [resetNonce, setResetNonce] = useState(0);
  // Controls hint fades out a few seconds after the spotlight loads so
  // the host isn't staring at an overlay forever.
  const [showHint, setShowHint] = useState(true);

  useEffect(() => {
    if (speakingAgents.size === 0) return;
    const next = Array.from(speakingAgents)[0];
    if (next !== lastSpeakerRef.current) {
      lastSpeakerRef.current = next;
      setLastSpeaker(next);
    }
  }, [speakingAgents]);

  useEffect(() => {
    const t = setTimeout(() => setShowHint(false), 6000);
    return () => clearTimeout(t);
  }, []);

  const spotlightName = pinned ?? lastSpeaker ?? agents[0]?.name ?? null;
  const spotlightAgent = spotlightName
    ? agents.find((a) => a.name === spotlightName)
    : null;

  if (agents.length === 0) {
    return (
      <div className="flex h-full items-center justify-center rounded-xl border border-fuchsia-500/20 bg-slate-950/60">
        <p className="font-mono text-sm text-white/40">Awaiting cast…</p>
      </div>
    );
  }

  const spotlightMood = spotlightAgent
    ? MOOD_COLORS[spotlightAgent.mood] ?? "from-fuchsia-500/15 to-transparent"
    : "from-fuchsia-500/10 to-transparent";

  return (
    <div className="flex h-full flex-col gap-2 overflow-hidden">
      {/* ── Spotlight ────────────────────────────────────────────── */}
      <div className="relative flex-1 min-h-0 overflow-hidden rounded-xl border border-fuchsia-500/25 bg-gradient-to-b from-slate-950 via-slate-950 to-slate-900 shadow-[0_0_30px_rgba(244,114,182,0.08)]">
        {/* Subtle grid backdrop */}
        <div
          className="absolute inset-0 opacity-[0.06]"
          style={{
            backgroundImage:
              "linear-gradient(rgba(244,114,182,0.6) 1px, transparent 1px), linear-gradient(90deg, rgba(244,114,182,0.6) 1px, transparent 1px)",
            backgroundSize: "48px 48px",
          }}
        />

        {/* Mood-tinted radial backlight — follows the spotlighted agent. */}
        <div
          key={`mood-${spotlightAgent?.name}-${spotlightAgent?.mood}`}
          className={`pointer-events-none absolute inset-0 bg-gradient-radial-[at_center_40%] ${spotlightMood} transition-opacity duration-700`}
          style={{
            background: `radial-gradient(ellipse at center 55%, var(--tw-gradient-stops))`,
          }}
        />

        {/* Floor glow — gives the character a sense of grounding. */}
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-32"
          style={{
            background:
              "radial-gradient(ellipse at 50% 100%, rgba(244,114,182,0.18) 0%, transparent 60%)",
          }}
        />

        {spotlightAgent && (
          <div
            key={spotlightAgent.name}
            className="relative flex h-full flex-col items-center justify-center animate-[spotlight-in_420ms_ease-out]"
          >
            <div className="relative h-full w-full">
              <VRMCharacter
                agent={spotlightAgent}
                getMouthAmplitude={getMouthAmplitude}
                spotlight
                cameraResetNonce={resetNonce}
                onClick={
                  onSelectAgent
                    ? () => onSelectAgent(spotlightAgent.name)
                    : undefined
                }
              />
            </div>

            {/* Name tag — top-left */}
            <div className="pointer-events-none absolute top-3 left-3 flex items-center gap-2">
              <div className="rounded-md border border-fuchsia-400/40 bg-black/70 px-2 py-0.5 backdrop-blur-sm">
                <div className="font-mono text-sm font-bold text-fuchsia-100">
                  {spotlightAgent.name}
                </div>
                <div className="font-mono text-[9px] text-white/50">
                  Lv{spotlightAgent.level} · {spotlightAgent.role}
                </div>
              </div>
              {speakingAgents.has(spotlightAgent.name) && (
                <div className="flex items-center gap-1 rounded-md border border-red-400/50 bg-red-500/20 px-1.5 py-0.5 backdrop-blur-sm">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-red-400 shadow-[0_0_6px_rgba(248,113,113,0.9)]" />
                  <span className="font-mono text-[9px] font-bold text-red-200">
                    LIVE
                  </span>
                </div>
              )}
            </div>

            {/* Camera controls — top-right. Stacked vertically so they
             *  don't collide with the pinned chip or the LIVE badge. */}
            <div className="absolute right-3 top-3 flex flex-col items-end gap-1.5">
              {pinned && (
                <button
                  type="button"
                  onClick={() => setPinned(null)}
                  className="rounded border border-amber-400/40 bg-amber-500/15 px-2 py-0.5 font-mono text-[10px] text-amber-300 backdrop-blur-sm hover:bg-amber-500/25"
                >
                  pinned · unpin
                </button>
              )}
              <div className="flex gap-1 rounded-lg border border-white/10 bg-black/60 p-1 backdrop-blur-sm">
                <button
                  type="button"
                  title="View agent details"
                  onClick={() =>
                    onSelectAgent && onSelectAgent(spotlightAgent.name)
                  }
                  className="rounded px-1.5 font-mono text-[10px] text-white/60 hover:bg-white/10 hover:text-white"
                >
                  ℹ︎
                </button>
                <button
                  type="button"
                  title="Reset camera"
                  onClick={() => setResetNonce((n) => n + 1)}
                  className="rounded px-1.5 font-mono text-[10px] text-white/60 hover:bg-white/10 hover:text-white"
                >
                  ⟲
                </button>
              </div>
            </div>

            {/* Controls hint — auto-fades after 6s so it doesn't loiter
             *  on screen once the host knows they can drag/scroll. */}
            <div
              className={`pointer-events-none absolute bottom-2 right-2 rounded bg-black/65 px-2 py-0.5 font-mono text-[9px] text-white/55 backdrop-blur-sm transition-opacity duration-700 ${
                showHint ? "opacity-100" : "opacity-0"
              }`}
            >
              drag · scroll · ⟲ reset
            </div>

            {/* Speech line — bottom overlay near the character's feet. */}
            {spotlightAgent.speech && (
              <div className="pointer-events-none absolute inset-x-3 bottom-10 flex justify-center">
                <div
                  key={`speech-${spotlightAgent.speech}`}
                  className="max-w-full rounded-xl border border-fuchsia-500/40 bg-black/80 px-3 py-2 text-center font-mono text-sm text-fuchsia-100 shadow-[0_4px_20px_rgba(0,0,0,0.5)] backdrop-blur-sm animate-[bubble-in_280ms_ease-out]"
                >
                  {spotlightAgent.speech}
                </div>
              </div>
            )}

            {/* Attribution — bottom-left, tiny */}
            {(() => {
              const credit = creditForAgent(spotlightAgent.name);
              return (
                <a
                  href={credit.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="absolute bottom-2 left-2 rounded bg-black/60 px-1.5 py-0.5 font-mono text-[9px] text-white/40 backdrop-blur-sm hover:text-fuchsia-200"
                  title={`${credit.character} by ${credit.author} — VRoid Hub`}
                >
                  ♥ {credit.character}
                </a>
              );
            })()}
          </div>
        )}
      </div>

      {/* ── Gallery strip ─────────────────────────────────────────── */}
      <div className="flex shrink-0 gap-1.5 overflow-x-auto rounded-xl border border-cyan-500/15 bg-slate-950/70 p-1.5 scrollbar-thin">
        {agents.map((agent) => {
          const isSpeaking = speakingAgents.has(agent.name);
          const isFocus = spotlightName === agent.name;
          return (
            <button
              key={agent.name}
              type="button"
              onClick={() => setPinned(agent.name)}
              className={`group relative flex shrink-0 flex-col items-stretch overflow-hidden rounded-lg border transition-all ${
                isFocus
                  ? "border-fuchsia-400/70 bg-fuchsia-500/10 shadow-[0_0_12px_rgba(244,114,182,0.35)]"
                  : isSpeaking
                    ? "border-red-400/40 bg-red-500/5"
                    : "border-white/10 bg-white/[0.02] hover:border-white/30"
              }`}
              style={{ width: 78, height: 108 }}
              title={`${agent.name} · Lv${agent.level} · ${agent.role}`}
            >
              <div className="flex-1 min-h-0">
                <VRMCharacter agent={agent} getMouthAmplitude={getMouthAmplitude} />
              </div>
              <div className="flex items-center justify-between border-t border-white/10 bg-black/60 px-1 py-0.5">
                <span className="truncate font-mono text-[8px] font-bold text-white/80">
                  {agent.name.slice(0, 7)}
                </span>
                <span className="font-mono text-[7px] text-yellow-400/90">
                  L{agent.level}
                </span>
              </div>
              {isSpeaking && (
                <span className="absolute right-1 top-1 h-2 w-2 animate-pulse rounded-full bg-red-400 shadow-[0_0_6px_rgba(248,113,113,0.9)]" />
              )}
            </button>
          );
        })}
      </div>

      <style jsx>{`
        @keyframes spotlight-in {
          0% {
            opacity: 0;
            transform: scale(0.97);
          }
          100% {
            opacity: 1;
            transform: scale(1);
          }
        }
        @keyframes bubble-in {
          0% {
            opacity: 0;
            transform: translateY(8px) scale(0.96);
          }
          100% {
            opacity: 1;
            transform: translateY(0) scale(1);
          }
        }
      `}</style>
    </div>
  );
}

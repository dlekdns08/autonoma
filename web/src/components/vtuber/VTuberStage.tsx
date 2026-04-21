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

import React, { useEffect, useMemo, useRef, useState } from "react";
import type { AgentData, AgentEmote } from "@/lib/types";
import VRMCharacter from "./VRMCharacter";
import { creditForAgent } from "./vrmCredits";

/** Which backdrop preset paints behind the spotlighted character.
 *
 *   - `default`     — original cyber / game-HUD look (fuchsia grid + pink floor).
 *   - `studio`      — soft photo-studio cyclorama (warm key, cool fill).
 *   - `sunset`      — warm horizon gradient with a low sun glow.
 *   - `night-city`  — navy night sky, distant skyline, window lights.
 *   - `sakura`      — pink gradient with falling petal animation.
 *   - `cyber`       — neon magenta/cyan with a perspective grid floor.
 *   - `none`        — renders nothing, so OBS chromakey / transparent mode works.
 *
 * Exported so the /obs route can type-check its query-param mapping. */
export type BackdropPreset =
  | "default"
  | "studio"
  | "sunset"
  | "night-city"
  | "sakura"
  | "cyber"
  | "none";

interface Props {
  agents: AgentData[];
  /** Live amplitude feed for lip-sync. */
  getMouthAmplitude?: (name: string) => number;
  /** Set of names currently producing audio, from useAgentVoice. */
  speakingAgents: Set<string>;
  /** Click → open agent modal in the parent. */
  onSelectAgent?: (name: string) => void;
  /** Streaming-friendly variant used by the /obs route. Drops the
   *  gallery, border, and camera controls so OBS / chromakey compositing
   *  gets a clean character + name tag + speech bubble on whatever
   *  background the outer page provides. */
  obsMode?: boolean;
  /** Backdrop preset. Defaults to `default` — callers (e.g. the OBS
   *  route) can override per-session. */
  backdrop?: BackdropPreset;
  /** Show a TV-style CC caption bar at the bottom of the spotlight
   *  instead of the in-scene speech bubble. `undefined` → follow
   *  `obsMode` (subtitles are the clip-friendly default for streams). */
  subtitles?: boolean;
  /** External pinning — when set, overrides the internal pinned state
   *  and triggers the reveal flash overlay (pixel → VTuber transition). */
  forcePinnedAgent?: string | null;
  /** Live emote map from the pixel stage — VRMCharacter reacts with a gesture. */
  emotes?: Record<string, AgentEmote>;
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
  obsMode = false,
  backdrop = "default",
  subtitles,
  forcePinnedAgent,
  emotes,
}: Props) {
  // Subtitles default: on in OBS (clip-friendly), off on the main
  // dashboard (the in-scene speech bubble is part of the aesthetic
  // there). Explicit `subtitles={false}` on /obs still wins.
  const useSubtitles = subtitles ?? obsMode;
  const [pinned, setPinned] = useState<string | null>(null);
  const [lastSpeaker, setLastSpeaker] = useState<string | null>(null);
  const lastSpeakerRef = useRef<string | null>(null);
  // Bumped when the user clicks "reset view" — VRMCharacter watches this
  // and snaps the camera back to the default full-body framing.
  const [resetNonce, setResetNonce] = useState(0);
  // Controls hint fades out a few seconds after the spotlight loads so
  // the host isn't staring at an overlay forever.
  const [showHint, setShowHint] = useState(true);
  // Flash overlay for the pixel → VTuber reveal transition.
  const [revealFlash, setRevealFlash] = useState(false);
  const prevForcedRef = useRef<string | null | undefined>(undefined);

  if (speakingAgents.size > 0) {
    const next = Array.from(speakingAgents)[0];
    if (next !== lastSpeakerRef.current) {
      lastSpeakerRef.current = next;
      if (lastSpeaker !== next) setLastSpeaker(next);
    }
  }

  useEffect(() => {
    const t = setTimeout(() => setShowHint(false), 6000);
    return () => clearTimeout(t);
  }, []);

  // Sync external pin + trigger reveal flash when the forced agent changes.
  if (forcePinnedAgent != null && forcePinnedAgent !== prevForcedRef.current) {
    prevForcedRef.current = forcePinnedAgent;
    if (pinned !== forcePinnedAgent) setPinned(forcePinnedAgent);
    if (!revealFlash) setRevealFlash(true);
  }

  useEffect(() => {
    if (!revealFlash) return;
    const t = setTimeout(() => setRevealFlash(false), 700);
    return () => clearTimeout(t);
  }, [revealFlash]);

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
      {/* The outer container intentionally has NO background — the
       *  Backdrop component below owns that, so switching presets (or
       *  rendering `none` for OBS chromakey) doesn't require bending
       *  around a baked-in gradient here. */}
      <div
        className={`relative flex-1 min-h-0 overflow-hidden rounded-xl ${
          obsMode
            ? ""
            : "border border-fuchsia-500/25 shadow-[0_0_30px_rgba(244,114,182,0.08)]"
        }`}
      >
        <Backdrop preset={backdrop} />

        {/* Mood-tinted radial backlight — follows the spotlighted agent.
         *  Stays inline (not part of Backdrop) because it reacts to the
         *  current agent's mood, not the chosen preset. */}
        <div
          key={`mood-${spotlightAgent?.name}-${spotlightAgent?.mood}`}
          className={`pointer-events-none absolute inset-0 bg-gradient-radial-[at_center_40%] ${spotlightMood} transition-opacity duration-700`}
          style={{
            background: `radial-gradient(ellipse at center 55%, var(--tw-gradient-stops))`,
          }}
        />

        {/* Pixel → VTuber reveal flash overlay */}
        {revealFlash && (
          <div
            className="pointer-events-none absolute inset-0 z-30 animate-vtuber-flash"
            style={{
              background: "radial-gradient(ellipse at center, rgba(139,92,246,0.95) 0%, rgba(34,211,238,0.6) 50%, transparent 80%)",
            }}
          />
        )}

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
                state={spotlightAgent.state ?? "idle"}
                cameraResetNonce={resetNonce}
                emote={emotes?.[spotlightAgent.name] ?? null}
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
             *  don't collide with the pinned chip or the LIVE badge.
             *  Hidden in OBS mode (streams don't need UI chrome). */}
            {!obsMode && (
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
            )}

            {/* Controls hint — auto-fades after 6s so it doesn't loiter
             *  on screen once the host knows they can drag/scroll.
             *  Hidden in OBS mode. */}
            {!obsMode && (
              <div
                className={`pointer-events-none absolute bottom-2 right-2 rounded bg-black/65 px-2 py-0.5 font-mono text-[9px] text-white/55 backdrop-blur-sm transition-opacity duration-700 ${
                  showHint ? "opacity-100" : "opacity-0"
                }`}
              >
                drag · scroll · ⟲ reset
              </div>
            )}

            {/* Speech — two presentations depending on `useSubtitles`:
             *   false → in-scene bubble near the character's feet (keeps
             *           the speech visually tied to the speaker).
             *   true  → TV-style CC bar pinned to the bottom edge so
             *           recorded clips/screenshots read like a caption
             *           track. We switch rather than stack so the two
             *           renderings never overlap. */}
            {spotlightAgent.speech && !useSubtitles && (
              <div className="pointer-events-none absolute inset-x-3 bottom-10 flex justify-center">
                <div
                  key={`speech-${spotlightAgent.speech}`}
                  className="max-w-full rounded-xl border border-fuchsia-500/40 bg-black/80 px-3 py-2 text-center font-mono text-sm text-fuchsia-100 shadow-[0_4px_20px_rgba(0,0,0,0.5)] backdrop-blur-sm animate-[bubble-in_280ms_ease-out]"
                >
                  {spotlightAgent.speech}
                </div>
              </div>
            )}
            {spotlightAgent.speech && useSubtitles && (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center px-6 pb-4">
                <div
                  key={`subs-${spotlightAgent.speech}`}
                  className="max-w-[min(720px,92%)] rounded-md bg-black/85 px-5 py-2.5 text-center font-mono text-[15px] leading-snug text-white shadow-[0_4px_24px_rgba(0,0,0,0.6)] backdrop-blur-sm animate-[bubble-in_240ms_ease-out]"
                  style={{
                    // Slight text outline so white captions stay legible
                    // against any backdrop — especially the sakura/sunset
                    // presets where the character may stand against a
                    // near-white area.
                    textShadow:
                      "0 1px 2px rgba(0,0,0,0.9), 0 0 3px rgba(0,0,0,0.9)",
                  }}
                >
                  <span className="mr-2 font-bold text-fuchsia-300">
                    {spotlightAgent.name}:
                  </span>
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
      {/* OBS mode drops the gallery entirely — streams only want the
       *  spotlight character, and every extra Canvas costs WebGL
       *  context slots we don't need to spend.
       *
       *  Each tile is a lightweight 2D card (no WebGL) so we don't blow
       *  through the browser's 16-context limit with 8+ agents. The
       *  spotlight holds the only Canvas; gallery tiles show emoji +
       *  mood tint + state badge + speaking indicator. */}
      {!obsMode && (
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
                  ? "border-fuchsia-400/70 shadow-[0_0_12px_rgba(244,114,182,0.35)]"
                  : isSpeaking
                    ? "border-red-400/40"
                    : "border-white/10 hover:border-white/30"
              }`}
              style={{ width: 78, height: 108 }}
              title={`${agent.name} · Lv${agent.level} · ${agent.role}`}
            >
              <GalleryTile agent={agent} isSpeaking={isSpeaking} isFocus={isFocus} />
              {isSpeaking && (
                <span className="absolute right-1 top-1 h-2 w-2 animate-pulse rounded-full bg-red-400 shadow-[0_0_6px_rgba(248,113,113,0.9)]" />
              )}
            </button>
          );
        })}
      </div>
      )}

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

// ── GalleryTile ───────────────────────────────────────────────────────
//
// Lightweight 2D replacement for the per-agent VRMCharacter tiles in
// the gallery strip. Renders emoji + mood tint + state badge + XP bar
// with zero WebGL cost. The spotlight holds the only Three.js canvas;
// all gallery entries are plain DOM so we stay well under the browser's
// 16-context limit even with 8+ agents loaded.

const STATE_ICONS: Record<string, string> = {
  thinking: "💭",
  working: "⚙️",
  talking: "💬",
  celebrating: "🎉",
  idle: "",
};

function GalleryTile({
  agent,
  isSpeaking,
  isFocus,
}: {
  agent: AgentData;
  isSpeaking: boolean;
  isFocus: boolean;
}) {
  const stateIcon = STATE_ICONS[agent.state] ?? "";
  // Derive a subtle background from the agent's color field (hex/css).
  // We layer it as a very-low-opacity fill so tiles are visually distinct
  // without shouting over the spotlight.
  const bgStyle: React.CSSProperties = agent.color
    ? { background: `color-mix(in srgb, ${agent.color} 18%, transparent)` }
    : {};

  const xpPct =
    agent.xp_to_next > 0
      ? Math.round((agent.xp / agent.xp_to_next) * 100)
      : 0;

  return (
    <div className="flex h-full flex-col" style={bgStyle}>
      {/* Emoji avatar area */}
      <div
        className={`relative flex flex-1 items-center justify-center transition-all ${
          isSpeaking ? "scale-110" : "scale-100"
        }`}
      >
        {/* Speaking ring */}
        {isSpeaking && (
          <span className="absolute inset-1 animate-ping rounded-full border border-red-400/50" />
        )}
        {/* Focus ring */}
        {isFocus && !isSpeaking && (
          <span className="absolute inset-1 rounded-full border border-fuchsia-400/40" />
        )}
        <span
          className="select-none"
          style={{ fontSize: 28, lineHeight: 1, filter: isSpeaking ? "drop-shadow(0 0 6px rgba(248,113,113,0.7))" : undefined }}
          aria-label={agent.name}
        >
          {agent.emoji}
        </span>
        {/* State icon overlay */}
        {stateIcon && (
          <span
            className="absolute bottom-0 right-0 text-[10px] leading-none"
            title={agent.state}
          >
            {stateIcon}
          </span>
        )}
      </div>

      {/* Name + level bar */}
      <div className="flex items-center justify-between border-t border-white/10 bg-black/60 px-1 py-0.5">
        <span className="truncate font-mono text-[8px] font-bold text-white/80">
          {agent.name.slice(0, 7)}
        </span>
        <span className="font-mono text-[7px] text-yellow-400/90">
          L{agent.level}
        </span>
      </div>

      {/* XP progress bar — 1px tall, full width */}
      <div className="h-[2px] w-full bg-white/10">
        <div
          className="h-full bg-fuchsia-400/70 transition-all duration-500"
          style={{ width: `${xpPct}%` }}
        />
      </div>
    </div>
  );
}

// ── Backdrop presets ──────────────────────────────────────────────────
//
// All presets render as absolute-inset layers filling the spotlight
// container. They sit *below* the mood-tinted radial and the character,
// so the mood tint still reads regardless of preset. Adding a new
// preset means extending `BackdropPreset` and adding a branch here.
//
// Kept inline in this file (rather than a separate `backdrops.tsx`)
// because these presets are only ever consumed by VTuberStage, and a
// separate module would split context for a change this small.

function Backdrop({ preset }: { preset: BackdropPreset }) {
  if (preset === "none") return null;

  if (preset === "sunset") {
    return (
      <>
        {/* Deep-purple zenith → warm horizon. The stop-heavy gradient
            mimics actual dusk skies better than a two-color ramp. */}
        <div
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(180deg, #2a0a3e 0%, #5a1a55 20%, #c4395a 45%, #f16f5c 65%, #f4b06b 80%, #3a1a3d 100%)",
          }}
        />
        {/* Low sun glow — sits just below the visual horizon. */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(ellipse 55% 30% at 50% 80%, rgba(255, 200, 130, 0.45), transparent 65%)",
          }}
        />
        {/* Thin horizon haze line — sells the atmosphere layering. */}
        <div
          className="pointer-events-none absolute inset-x-0 top-[72%] h-px bg-white/25"
        />
        {/* Warm floor spill to echo the sun color on the ground. */}
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-40"
          style={{
            background:
              "radial-gradient(ellipse at 50% 100%, rgba(255, 170, 120, 0.22) 0%, transparent 65%)",
          }}
        />
      </>
    );
  }

  if (preset === "night-city") {
    return (
      <>
        {/* Night sky — darker near zenith, faint atmospheric glow near
            the horizon to sell distance. */}
        <div
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(180deg, #060812 0%, #0a1228 50%, #15203d 100%)",
          }}
        />
        {/* Neon atmospheric haze — purple pollution at the horizon. */}
        <div
          className="pointer-events-none absolute inset-x-0 top-1/2 h-32"
          style={{
            background:
              "radial-gradient(ellipse at 50% 0%, rgba(180, 80, 255, 0.18), transparent 70%)",
          }}
        />
        {/* Skyline silhouette — dark gradient mass at the bottom. */}
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-1/3"
          style={{
            background:
              "linear-gradient(180deg, transparent 0%, rgba(0,0,0,0.55) 55%, rgba(0,0,0,0.88) 100%)",
          }}
        />
        {/* Scattered window lights — procedural "buildings" via a stack
            of radial-gradient dots so we don't need a texture asset. */}
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-1/3"
          style={{
            backgroundImage: [
              "radial-gradient(circle at 10% 42%, rgba(255,210,120,0.75) 0, transparent 2px)",
              "radial-gradient(circle at 22% 55%, rgba(255,230,160,0.8) 0, transparent 1.8px)",
              "radial-gradient(circle at 28% 32%, rgba(200,230,255,0.6) 0, transparent 2px)",
              "radial-gradient(circle at 45% 62%, rgba(255,220,140,0.8) 0, transparent 2px)",
              "radial-gradient(circle at 58% 40%, rgba(180,220,255,0.55) 0, transparent 1.6px)",
              "radial-gradient(circle at 72% 50%, rgba(255,200,100,0.7) 0, transparent 2px)",
              "radial-gradient(circle at 85% 65%, rgba(255,230,170,0.8) 0, transparent 1.6px)",
              "radial-gradient(circle at 92% 32%, rgba(200,230,255,0.55) 0, transparent 2px)",
              "radial-gradient(circle at 15% 80%, rgba(255,230,160,0.65) 0, transparent 1.6px)",
              "radial-gradient(circle at 50% 85%, rgba(255,220,140,0.75) 0, transparent 2px)",
              "radial-gradient(circle at 65% 78%, rgba(180,230,255,0.5) 0, transparent 1.5px)",
            ].join(","),
          }}
        />
      </>
    );
  }

  if (preset === "sakura") {
    return (
      <>
        {/* Soft pink wash — deeper at the top, nearly bleached at the
            bottom so the character has contrast against the floor. */}
        <div
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(180deg, #3d1a3d 0%, #6b2e4e 30%, #c67a89 60%, #f4c2c8 100%)",
          }}
        />
        {/* Warm cyclorama glow so the character doesn't float on a
            flat wash. */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(ellipse 60% 45% at 50% 40%, rgba(255, 200, 210, 0.3), transparent 70%)",
          }}
        />
        {/* Floor petal carpet hint — faint pink glow at the base. */}
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-36"
          style={{
            background:
              "radial-gradient(ellipse at 50% 100%, rgba(255, 180, 200, 0.28) 0%, transparent 60%)",
          }}
        />
        <SakuraPetals />
      </>
    );
  }

  if (preset === "cyber") {
    return (
      <>
        {/* Deep purple base. Saturated enough to read as "neon-lit"
            even before the accent lines are drawn on top. */}
        <div
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(180deg, #0a0220 0%, #1a0530 50%, #2d084d 100%)",
          }}
        />
        {/* Horizon scan line — the single brightest magenta accent. */}
        <div
          className="pointer-events-none absolute inset-x-0 top-[68%] h-[2px]"
          style={{
            background: "#f472b6",
            boxShadow:
              "0 0 12px 2px rgba(244,114,182,0.9), 0 0 32px 6px rgba(244,114,182,0.35)",
          }}
        />
        {/* Perspective floor — repeating bands that fade toward the
            horizon line. Fakes ground recession without needing 3D. */}
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-[32%]"
          style={{
            backgroundImage:
              "repeating-linear-gradient(180deg, transparent 0 5.8%, rgba(244,114,182,0.35) 5.8% 6.2%)",
            backgroundSize: "100% 100%",
            maskImage:
              "linear-gradient(180deg, transparent 0%, black 15%, black 100%)",
            WebkitMaskImage:
              "linear-gradient(180deg, transparent 0%, black 15%, black 100%)",
          }}
        />
        {/* Cyan edge bloom — adds the classic synthwave rim. */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(ellipse 90% 70% at 50% 50%, transparent 45%, rgba(34, 211, 238, 0.15) 85%)",
          }}
        />
      </>
    );
  }

  if (preset === "studio") {
    return (
      <>
        {/* Neutral wall gradient — slight warm tint avoids the
            cold-blue sterility of a flat slate wash. Darkens toward
            the bottom so the floor spill below has contrast to read. */}
        <div
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(180deg, #1c1e24 0%, #121318 55%, #0a0b0f 100%)",
          }}
        />
        {/* Key light: warm cream spot from upper-right. "Short"
            lighting (key on the far side of the face) reads more
            cinematic than a dead-center front-light. */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(ellipse 55% 45% at 70% 25%, rgba(255, 228, 196, 0.14), transparent 70%)",
          }}
        />
        {/* Fill light: dim cool cyan from the opposite side — just
            enough to keep the shadow side from going dead black
            without fighting the key light for attention. */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(ellipse 50% 50% at 20% 55%, rgba(170, 210, 245, 0.08), transparent 70%)",
          }}
        />
        {/* Cyclorama — the soft bright arc photographers put behind
            the subject. Positioned at shoulder height so it haloes
            the character rather than framing them symmetrically. */}
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(ellipse 90% 60% at 50% 60%, rgba(255, 255, 255, 0.05), transparent 60%)",
          }}
        />
        {/* Floor spill — warm pool beneath the character sells the
            "standing on a surface" illusion without actual 3D
            geometry, and grounds the silhouette against the wall. */}
        <div
          className="pointer-events-none absolute inset-x-0 bottom-0 h-40"
          style={{
            background:
              "radial-gradient(ellipse at 50% 100%, rgba(255, 223, 186, 0.16) 0%, transparent 65%)",
          }}
        />
      </>
    );
  }

  // Default: the original cyber / game-HUD look — dark slate gradient,
  // fuchsia grid overlay, pink floor glow.
  return (
    <>
      <div
        className="absolute inset-0"
        style={{
          background:
            "linear-gradient(180deg, rgb(2 6 23) 0%, rgb(2 6 23) 50%, rgb(15 23 42) 100%)",
        }}
      />
      <div
        className="absolute inset-0 opacity-[0.06]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(244,114,182,0.6) 1px, transparent 1px), linear-gradient(90deg, rgba(244,114,182,0.6) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
        }}
      />
      <div
        className="pointer-events-none absolute inset-x-0 bottom-0 h-32"
        style={{
          background:
            "radial-gradient(ellipse at 50% 100%, rgba(244,114,182,0.18) 0%, transparent 60%)",
        }}
      />
    </>
  );
}

// ── Sakura petal particles ────────────────────────────────────────────
//
// Pre-computed positions and timings (seeded from each petal's index,
// not Math.random) so the SSR and client render match and hydration
// doesn't yank the petals' starting positions on first paint. 14 petals
// is enough density without piling up paint cost — each is a single
// span, GPU-compositing via the transform animation.

function SakuraPetals() {
  const petals = useMemo(
    () =>
      Array.from({ length: 14 }, (_, i) => ({
        left: (i * 73) % 100,
        delay: (i * 1.7) % 10,
        duration: 9 + (i % 5),
        size: 6 + (i % 3),
        drift: 20 + ((i * 17) % 40),
        tint: i % 2 === 0 ? "#ffc6d0" : "#ffb0c0",
      })),
    [],
  );

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {petals.map((p, i) => (
        <span
          key={i}
          className="absolute rounded-full"
          style={{
            left: `${p.left}%`,
            top: `-${p.size * 2}px`,
            width: p.size,
            height: p.size,
            backgroundColor: p.tint,
            opacity: 0.85,
            // Expose drift as a CSS custom property so the shared
            // keyframe can read it and each petal follows a slightly
            // different arc without needing N distinct keyframe blocks.
            // `as React.CSSProperties` cast — TypeScript's default CSS
            // types don't know about CSS custom properties.
            ["--drift" as string]: `${p.drift}px`,
            animation: `sakura-fall ${p.duration}s linear ${p.delay}s infinite`,
          }}
        />
      ))}
      <style>{`
        @keyframes sakura-fall {
          0% {
            transform: translate(0, 0) rotate(0deg);
            opacity: 0;
          }
          8% { opacity: 0.85; }
          92% { opacity: 0.85; }
          100% {
            transform: translate(var(--drift, 30px), 110vh) rotate(720deg);
            opacity: 0;
          }
        }
      `}</style>
    </div>
  );
}

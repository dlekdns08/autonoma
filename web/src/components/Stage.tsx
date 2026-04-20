"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentData, AgentEmote, BossData, CookieData } from "@/lib/types";
import PixelMap from "./stage/pixel/PixelMap";
import PixelCharacter from "./stage/pixel/PixelCharacter";
import { STAGE, CHAR } from "./stage/pixel/types";
import type { SkyMode } from "./stage/pixel/types";
import type { MapTheme } from "./stage/pixel/mapData";
import { buildMap } from "./stage/pixel/mapData";
import {
  useAgentMotion,
  type MotionState,
  type DialoguePair,
} from "./stage/useAgentMotion";

interface Props {
  agents: AgentData[];
  sky?: string;
  theme?: MapTheme;
  boss?: BossData | null;
  cookies?: CookieData[];
  /** Per-agent reaction icons; expired entries are pruned by useSwarm. */
  emotes?: Record<string, AgentEmote>;
  /** Live amplitude getter from useAgentVoice. Sampled per-frame inside
   *  AgentOnMap to drive a small "speaking" glow. Returns 0 when silent. */
  getMouthAmplitude?: (agent: string) => number;
  onSelectAgent?: (name: string) => void;
  onCookieCollected?: (recipient: string) => void;
  /** When set, the matching pixel sprite plays the bloom-dissolve animation. */
  transitioningAgent?: string | null;
}

const RARITY_TEXT: Record<string, string> = {
  legendary: "text-amber-300",
  rare: "text-purple-300",
  uncommon: "text-cyan-300",
  common: "text-white/90",
};

function resolveSky(sky: string | undefined): SkyMode {
  if (!sky) return "day";
  const s = sky.toLowerCase();
  if (s.includes("dawn")) return "dawn";
  if (s.includes("dusk") || s.includes("sunset") || s.includes("evening")) return "dusk";
  if (s.includes("night") || s.includes("midnight") || s.includes("star")) return "night";
  return "day";
}

const CHAR_W_PCT = (CHAR.width / STAGE.width) * 100;
const CHAR_H_PCT = (CHAR.height / STAGE.height) * 100;

const BOSS_SPECIES_ICON: Record<string, string> = {
  dragon: "🐉",
  kraken: "🐙",
  golem: "🗿",
  shadow: "👤",
  phoenix: "🔥",
};

export default function Stage({
  agents,
  sky,
  theme = "hq",
  boss = null,
  cookies = [],
  emotes,
  getMouthAmplitude,
  onSelectAgent,
  onCookieCollected,
  transitioningAgent = null,
}: Props) {
  const skyMode = resolveSky(sky);
  const map = useMemo(() => buildMap(theme), [theme]);
  // Speech bubbles are rendered in the VTuber panel now (with lip-sync
  // and proper framing), so we no longer surface them on the pixel map.
  // Keeping the bubbles pipeline so other callers (pairs, attackingAgents)
  // keep working — we just stop passing `bubble` down to AgentOnMap.
  const { motions, pairs, attackingAgents } = useAgentMotion({
    agents,
    map,
    boss,
    cookies,
    onCookieCollected,
  });

  if (agents.length === 0) {
    return (
      <div className="relative h-full overflow-hidden rounded-xl border border-cyan-500/20">
        <PixelMap sky={skyMode} theme={theme}>
          <div className="pointer-events-none flex h-full items-center justify-center">
            <p className="font-mono text-white/60 text-lg drop-shadow-lg">
              The HQ awaits a mission...
            </p>
          </div>
        </PixelMap>
      </div>
    );
  }

  return (
    <div className="relative h-full overflow-hidden rounded-xl border border-cyan-500/20">
      <PixelMap sky={skyMode} theme={theme}>
        {/* Cookies sit under the characters so an agent walking onto one
            is drawn on top of it. */}
        {cookies.map((c) => (
          <CookieSprite key={`cookie-${c.recipient}`} cookie={c} />
        ))}

        {boss && <BossSprite boss={boss} attackingAgents={attackingAgents} />}

        <DialogueLinks pairs={pairs} motions={motions} />
        {pairs.map((p) => (
          <div
            key={`heart-${p.a}-${p.b}`}
            className="pointer-events-none absolute animate-pulse text-pink-300 drop-shadow-[0_0_4px_rgba(244,114,182,0.9)]"
            style={{
              left: `${p.midX}%`,
              top: `${p.midY - 14}%`,
              transform: "translate(-50%, -50%)",
              fontSize: "14px",
            }}
          >
            ♥
          </div>
        ))}
        {agents.map((agent) => {
          const motion = motions[agent.name];
          if (!motion) return null;
          const emote = emotes?.[agent.name] ?? null;
          const isTransitioning = transitioningAgent === agent.name;
          return (
            <AgentOnMap
              key={agent.name}
              agent={agent}
              motion={motion}
              emote={emote}
              getMouthAmplitude={getMouthAmplitude}
              onClick={onSelectAgent ? () => onSelectAgent(agent.name) : undefined}
              blooming={isTransitioning}
            />
          );
        })}
      </PixelMap>
    </div>
  );
}

function DialogueLinks({
  pairs,
  motions,
}: {
  pairs: DialoguePair[];
  motions: Record<string, MotionState>;
}) {
  if (pairs.length === 0) return null;
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
    >
      <defs>
        {pairs.map((p) => (
          <linearGradient
            key={`grad-${p.a}-${p.b}`}
            id={`dlg-grad-${p.a}-${p.b}`}
            gradientUnits="userSpaceOnUse"
            x1={motions[p.a]?.x ?? 0}
            y1={(motions[p.a]?.y ?? 0) - 8}
            x2={motions[p.b]?.x ?? 0}
            y2={(motions[p.b]?.y ?? 0) - 8}
          >
            <stop offset="0%" stopColor="rgba(139,92,246,0.7)" />
            <stop offset="100%" stopColor="rgba(34,211,238,0.7)" />
          </linearGradient>
        ))}
      </defs>
      {pairs.map((p) => {
        const a = motions[p.a];
        const b = motions[p.b];
        if (!a || !b) return null;
        const ax = a.x;
        const ay = a.y - 8;
        const bx = b.x;
        const by = b.y - 8;
        return (
          <g key={`${p.a}-${p.b}`}>
            <line
              x1={ax}
              y1={ay}
              x2={bx}
              y2={by}
              stroke={`url(#dlg-grad-${p.a}-${p.b})`}
              strokeWidth={1}
              strokeDasharray="4 3"
              vectorEffect="non-scaling-stroke"
              style={{ animation: "dialogue-flow 0.8s linear infinite" }}
            />
          </g>
        );
      })}
    </svg>
  );
}

// ── Boss sprite on the stage ─────────────────────────────────────────────
//
// The backend places the boss at the centre of the War Room. We render a
// big emoji for the species, a red HP bar under it, and a hit-flash that
// triggers from the `hitSeq` counter bumped by useSwarm. A floating
// damage number shoots up from the boss whenever a new hit lands.

function BossSprite({ boss, attackingAgents = [] }: { boss: BossData; attackingAgents?: string[] }) {
  const [shakeKey, setShakeKey] = useState(0);
  const [damagePops, setDamagePops] = useState<
    Array<{ id: number; value: number; agent: string }>
  >([]);
  const [localFlash, setLocalFlash] = useState(false);
  const popIdRef = useRef(0);
  const lastHitRef = useRef(0);
  const localFlashTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Track any pending timers across renders so we can clear them on unmount.
  // Each damage pop schedules a cleanup timer, and the flash interval spawns
  // short "clear localFlash" timers — leaving them running after unmount
  // would fire setState on a gone component.
  const popTimersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  const flashTimersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      popTimersRef.current.forEach(clearTimeout);
      popTimersRef.current.clear();
      flashTimersRef.current.forEach(clearTimeout);
      flashTimersRef.current.clear();
      if (localFlashTimerRef.current) {
        clearInterval(localFlashTimerRef.current);
        localFlashTimerRef.current = null;
      }
    };
  }, []);

  // Backend hit: shake + damage number
  useEffect(() => {
    if (boss.hitSeq === 0 || boss.hitSeq === lastHitRef.current) return;
    lastHitRef.current = boss.hitSeq;
    setShakeKey((k) => k + 1);
    if (boss.lastDamage > 0) {
      const id = ++popIdRef.current;
      const value = boss.lastDamage;
      const agent = boss.lastAttacker;
      setDamagePops((prev) => [...prev.slice(-3), { id, value, agent }]);
      const timerId = setTimeout(() => {
        popTimersRef.current.delete(timerId);
        if (!mountedRef.current) return;
        setDamagePops((prev) => prev.filter((p) => p.id !== id));
      }, 1100);
      popTimersRef.current.add(timerId);
    }
  }, [boss.hitSeq, boss.lastDamage, boss.lastAttacker]);

  // Local attack flash: pulses faster the more agents are attacking
  useEffect(() => {
    if (localFlashTimerRef.current) clearInterval(localFlashTimerRef.current);
    if (attackingAgents.length === 0) return;
    const interval = Math.max(300, 900 / attackingAgents.length);
    localFlashTimerRef.current = setInterval(() => {
      setShakeKey((k) => k + 1);
      setLocalFlash(true);
      const resetId = setTimeout(() => {
        flashTimersRef.current.delete(resetId);
        if (!mountedRef.current) return;
        setLocalFlash(false);
      }, 120);
      flashTimersRef.current.add(resetId);
    }, interval);
    return () => {
      if (localFlashTimerRef.current) {
        clearInterval(localFlashTimerRef.current);
        localFlashTimerRef.current = null;
      }
      flashTimersRef.current.forEach(clearTimeout);
      flashTimersRef.current.clear();
    };
  }, [attackingAgents.length]);

  const icon = BOSS_SPECIES_ICON[boss.species] ?? "☠";
  const hpPct = boss.max_hp > 0 ? (boss.hp / boss.max_hp) * 100 : 0;
  const hpGradient =
    hpPct > 50
      ? "bg-gradient-to-r from-rose-600 to-red-500"
      : "bg-gradient-to-r from-red-700 to-orange-600";
  const hpPulse = hpPct <= 20 ? "animate-pulse" : "";
  const isUnderAttack = attackingAgents.length > 0;

  return (
    <div
      className="pointer-events-none absolute"
      style={{
        left: `${boss.x}%`,
        top: `${boss.y}%`,
        transform: "translate(-50%, -100%)",
      }}
    >
      {/* Damage numbers — positioned absolutely above the sprite. */}
      {damagePops.map((pop, idx) => (
        <div
          key={pop.id}
          className="absolute left-1/2 -translate-x-1/2 font-mono text-xs font-bold text-yellow-300 drop-shadow-[0_0_3px_rgba(0,0,0,0.9)] animate-[floatUp_1.1s_ease-out_forwards]"
          style={{
            bottom: "100%",
            marginBottom: `${4 + idx * 2}px`,
            textShadow: "0 0 4px rgba(0,0,0,0.9)",
          }}
        >
          -{pop.value}
          {pop.agent && (
            <span className="ml-1 text-[9px] text-white/70">{pop.agent}</span>
          )}
        </div>
      ))}

      <div
        key={shakeKey}
        className="flex flex-col items-center animate-[bossShake_0.35s_ease-out]"
      >
        {/* Glow aura — intensifies when agents are attacking */}
        <div className="relative">
          <div
            className={`absolute inset-0 rounded-full blur-md transition-all duration-150 ${
              localFlash
                ? "bg-red-400/90 scale-125"
                : isUnderAttack
                  ? "bg-red-500/60 animate-pulse"
                  : "bg-red-500/30 animate-pulse"
            }`}
          />
          <div
            className="relative text-4xl leading-none drop-shadow-[0_0_6px_rgba(255,0,0,0.8)]"
            style={{
              filter: localFlash
                ? "saturate(2) brightness(1.6)"
                : isUnderAttack
                  ? "saturate(1.7)"
                  : "saturate(1.3)",
              transition: "filter 0.12s ease-out",
            }}
          >
            {icon}
          </div>
        </div>

        {/* Name + HP */}
        <div className="mt-0.5 rounded border border-red-500/60 bg-black/80 px-1.5 py-0.5 font-mono text-[8px] text-red-200 whitespace-nowrap">
          ☠ {boss.name} Lv{boss.level}
        </div>
        <div
          className="mt-0.5 h-1 w-16 overflow-hidden rounded-full"
          style={{
            background: "rgba(0,0,0,0.6)",
            border: "1px solid rgba(239,68,68,0.3)",
          }}
        >
          <div
            className={`h-full transition-all duration-200 ${hpGradient} ${hpPulse}`}
            style={{ width: `${hpPct}%` }}
          />
        </div>
        <div className="font-mono text-[7px] text-red-300/80 leading-none">
          {boss.hp}/{boss.max_hp}
        </div>

        {/* Attacker tags — shown while agents are in strike range */}
        {isUnderAttack && (
          <div className="mt-0.5 flex flex-wrap justify-center gap-0.5 max-w-[80px]">
            {attackingAgents.slice(0, 4).map((name) => (
              <span
                key={name}
                className="rounded bg-red-900/70 px-1 font-mono text-[6px] text-red-200 border border-red-700/50"
              >
                ⚔ {name.slice(0, 6)}
              </span>
            ))}
          </div>
        )}
      </div>

      <style jsx>{`
        @keyframes bossShake {
          0% {
            transform: translate(0, 0);
          }
          20% {
            transform: translate(-2px, 1px);
          }
          40% {
            transform: translate(2px, -1px);
          }
          60% {
            transform: translate(-1px, 1px);
          }
          80% {
            transform: translate(1px, 0);
          }
          100% {
            transform: translate(0, 0);
          }
        }
        @keyframes floatUp {
          0% {
            transform: translate(-50%, 0) scale(0.7);
            opacity: 0;
          }
          20% {
            transform: translate(-50%, -6px) scale(1.15);
            opacity: 1;
          }
          100% {
            transform: translate(-50%, -28px) scale(1);
            opacity: 0;
          }
        }
      `}</style>
    </div>
  );
}

// ── Fortune cookie sprite ───────────────────────────────────────────────

function CookieSprite({ cookie }: { cookie: CookieData }) {
  const opened = cookie.openedAt !== undefined;
  return (
    <div
      className="pointer-events-none absolute"
      style={{
        left: `${cookie.x}%`,
        top: `${cookie.y}%`,
        transform: "translate(-50%, -100%)",
      }}
    >
      {opened ? (
        <div className="relative">
          <div className="text-xl animate-[cookiePop_1.1s_ease-out_forwards]">
            ✨
          </div>
          <div className="absolute -top-3 left-1/2 -translate-x-1/2 font-mono text-[8px] font-bold text-amber-200 whitespace-nowrap animate-[floatUp_1.1s_ease-out_forwards]">
            +XP!
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center animate-[cookieBob_2s_ease-in-out_infinite]">
          <div className="text-lg drop-shadow-[0_0_3px_rgba(251,191,36,0.6)]">
            🥠
          </div>
          <div className="mt-0.5 rounded bg-amber-500/20 px-1 text-[7px] font-mono text-amber-200 whitespace-nowrap">
            {cookie.recipient.slice(0, 8)}
          </div>
        </div>
      )}
      <style jsx>{`
        @keyframes cookieBob {
          0%,
          100% {
            transform: translateY(0);
          }
          50% {
            transform: translateY(-2px);
          }
        }
        @keyframes cookiePop {
          0% {
            transform: scale(0.6);
            opacity: 0.6;
          }
          40% {
            transform: scale(1.6);
            opacity: 1;
          }
          100% {
            transform: scale(2.2);
            opacity: 0;
          }
        }
        @keyframes floatUp {
          0% {
            transform: translate(-50%, 0);
            opacity: 0;
          }
          25% {
            opacity: 1;
          }
          100% {
            transform: translate(-50%, -18px);
            opacity: 0;
          }
        }
      `}</style>
    </div>
  );
}

function AgentOnMap({
  agent,
  motion,
  emote,
  getMouthAmplitude,
  onClick,
  blooming = false,
}: {
  agent: AgentData;
  motion: MotionState;
  emote: AgentEmote | null;
  getMouthAmplitude?: (agent: string) => number;
  onClick?: () => void;
  blooming?: boolean;
}) {
  // Drive a "speaking glow ring" off the live audio amplitude. We mutate
  // boxShadow directly on a ref so React doesn't re-render at 60 fps per
  // agent. The ring color is rose when speaking, falls back to state color.
  const ringRef = useRef<HTMLDivElement | null>(null);

  // Derive a static boxShadow from agent.state for non-speaking states.
  // This is also used as the fallback when amplitude is 0.
  const stateBoxShadow = (() => {
    const s = agent.state ?? "";
    if (s === "working")
      return "0 0 0 2px rgba(34,211,238,0.5), 0 0 10px rgba(34,211,238,0.2)";
    if (s === "thinking")
      return "0 0 0 2px rgba(139,92,246,0.5), 0 0 10px rgba(139,92,246,0.2)";
    return "none";
  })();

  useEffect(() => {
    if (!getMouthAmplitude) return;
    let raf = 0;
    const tick = () => {
      const amp = getMouthAmplitude(agent.name);
      const el = ringRef.current;
      if (el) {
        if (amp > 0.01) {
          el.style.boxShadow =
            "0 0 0 2px rgba(251,113,133,0.65), 0 0 14px rgba(251,113,133,0.3)";
        } else {
          el.style.boxShadow = stateBoxShadow;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [agent.name, getMouthAmplitude, stateBoxShadow]);

  // When there's no amplitude source, derive the ring from agent.state only.
  const staticBoxShadow = (() => {
    const s = agent.state ?? "";
    if (s === "speaking")
      return "0 0 0 2px rgba(251,113,133,0.65), 0 0 14px rgba(251,113,133,0.3)";
    if (s === "working")
      return "0 0 0 2px rgba(34,211,238,0.5), 0 0 10px rgba(34,211,238,0.2)";
    if (s === "thinking")
      return "0 0 0 2px rgba(139,92,246,0.5), 0 0 10px rgba(139,92,246,0.2)";
    return "none";
  })();

  const isSpeaking =
    (agent.state ?? "") === "speaking" ||
    (getMouthAmplitude ? getMouthAmplitude(agent.name) > 0.01 : false);

  return (
    <div
      className={`absolute ${onClick && !blooming ? "cursor-pointer" : ""} ${blooming ? "animate-pixel-bloom" : ""}`}
      role={onClick && !blooming ? "button" : undefined}
      tabIndex={onClick && !blooming ? 0 : undefined}
      onClick={blooming ? undefined : onClick}
      onKeyDown={(e) => {
        if (!onClick || blooming) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      style={{
        left: `${motion.x}%`,
        top: `${motion.y}%`,
        width: `${CHAR_W_PCT}%`,
        height: `${CHAR_H_PCT}%`,
        transform: `translate(-50%, -100%) translateY(${-motion.jumpOffset}px)`,
        transition: blooming ? "none" : "top 120ms linear",
        transformOrigin: "center bottom",
        zIndex: blooming ? 20 : undefined,
      }}
    >
      {emote && (
        // `key` includes seq so a re-emote on the same agent restarts
        // the animation instead of being silently merged.
        <div
          key={emote.seq}
          className="pointer-events-none absolute -top-3 left-1/2 -translate-x-1/2 select-none text-[16px]"
          style={{
            animation: "emote-pop 1800ms ease-out forwards",
            background: "rgba(12,11,29,0.75)",
            border: "1px solid rgba(139,92,246,0.3)",
            borderRadius: "8px",
            padding: "2px 4px",
            backdropFilter: "blur(6px)",
            boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
          }}
        >
          {emote.icon}
        </div>
      )}

      {/* Sprite wrapper — carries the mood-colored glow ring */}
      <div
        ref={ringRef}
        className="pointer-events-none absolute inset-0 rounded-sm"
        style={{
          boxShadow: getMouthAmplitude ? stateBoxShadow : staticBoxShadow,
          transition: "box-shadow 0.15s ease-out",
        }}
      />

      {/* Ground shadow — elliptical blob behind the sprite */}
      <div
        style={{
          position: "absolute",
          bottom: -2,
          left: "50%",
          transform: "translateX(-50%)",
          width: 10,
          height: 3,
          background: "rgba(0,0,0,0.45)",
          borderRadius: "50%",
          filter: "blur(1px)",
        }}
      />

      <PixelCharacter
        role={agent.role}
        species={agent.species}
        mood={agent.mood}
        seed={agent.name}
        walkPhase={motion.isMoving ? motion.walkPhase : undefined}
        facingLeft={motion.facingLeft}
      />

      <div className="absolute top-full left-1/2 -translate-x-1/2 mt-0.5 flex flex-col items-center whitespace-nowrap">
        {/* Pill name badge */}
        <div
          className="font-mono text-[8px] max-w-[52px] overflow-hidden text-ellipsis"
          style={{
            background: "rgba(12,11,29,0.82)",
            border: `1px solid ${isSpeaking ? "rgba(251,113,133,0.5)" : "rgba(139,92,246,0.25)"}`,
            borderRadius: "9999px",
            padding: "1px 5px",
            color: "#c4b5fd",
            whiteSpace: "nowrap",
          }}
        >
          {agent.name.slice(0, 8)}
          <span className="text-[7px] text-amber-400/80 ml-0.5">
            L{agent.level}
          </span>
        </div>
        <div className="mt-0.5 h-[3px] w-10 overflow-hidden rounded-full bg-black/50">
          <div
            className="h-full rounded-full bg-gradient-to-r from-cyan-400 to-fuchsia-400"
            style={{
              width: `${agent.xp_to_next > 0 ? (agent.xp / agent.xp_to_next) * 100 : 0}%`,
            }}
          />
        </div>
      </div>

      <style jsx>{`
        @keyframes emote-pop {
          0% {
            transform: translate(-50%, 4px) scale(0.4);
            opacity: 0;
          }
          18% {
            transform: translate(-50%, -6px) scale(1.25);
            opacity: 1;
          }
          70% {
            transform: translate(-50%, -10px) scale(1);
            opacity: 1;
          }
          100% {
            transform: translate(-50%, -14px) scale(0.9);
            opacity: 0;
          }
        }
      `}</style>
    </div>
  );
}

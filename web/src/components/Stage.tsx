"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentData, BossData, CookieData } from "@/lib/types";
import PixelMap from "./stage/pixel/PixelMap";
import PixelCharacter from "./stage/pixel/PixelCharacter";
import { STAGE, CHAR } from "./stage/pixel/types";
import type { SkyMode } from "./stage/pixel/types";
import type { MapTheme } from "./stage/pixel/mapData";
import { buildMap } from "./stage/pixel/mapData";
import {
  useAgentMotion,
  type MotionState,
  type DialogueBubble,
  type DialoguePair,
} from "./stage/useAgentMotion";

interface Props {
  agents: AgentData[];
  sky?: string;
  theme?: MapTheme;
  boss?: BossData | null;
  cookies?: CookieData[];
  onSelectAgent?: (name: string) => void;
  onCookieCollected?: (recipient: string) => void;
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
  onSelectAgent,
  onCookieCollected,
}: Props) {
  const skyMode = resolveSky(sky);
  const map = useMemo(() => buildMap(theme), [theme]);
  const { motions, bubbles, pairs, attackingAgents } = useAgentMotion({
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
          const bubble = bubbles.find((b) => b.speaker === agent.name);
          return (
            <AgentOnMap
              key={agent.name}
              agent={agent}
              motion={motion}
              dialogue={bubble ?? null}
              onClick={onSelectAgent ? () => onSelectAgent(agent.name) : undefined}
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
              stroke="rgba(244, 114, 182, 0.55)"
              strokeWidth={0.35}
              strokeDasharray="1.2 0.8"
              vectorEffect="non-scaling-stroke"
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
  const hpColor =
    hpPct > 60 ? "bg-red-500" : hpPct > 30 ? "bg-orange-500" : "bg-red-700";
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
        <div className="mt-0.5 h-1 w-16 overflow-hidden rounded-full border border-red-900/80 bg-black/70">
          <div
            className={`h-full transition-all duration-200 ${hpColor}`}
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
  dialogue,
  onClick,
}: {
  agent: AgentData;
  motion: MotionState;
  dialogue: DialogueBubble | null;
  onClick?: () => void;
}) {
  const rarityClass = RARITY_TEXT[agent.rarity || "common"] ?? RARITY_TEXT.common;
  const speech = dialogue?.text ?? agent.speech;

  return (
    <div
      className={`absolute ${onClick ? "cursor-pointer" : ""}`}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(e) => {
        if (!onClick) return;
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
        transition: "top 120ms linear",
      }}
    >
      {speech && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 max-w-[220px] whitespace-nowrap">
          <div className="relative rounded-md border border-slate-700 bg-white/95 px-2 py-0.5 text-[10px] text-slate-900 shadow-lg">
            <div className="truncate font-medium">{speech}</div>
            <div className="absolute -bottom-[4px] left-1/2 h-2 w-2 -translate-x-1/2 rotate-45 border-b border-r border-slate-700 bg-white/95" />
          </div>
        </div>
      )}

      <PixelCharacter
        role={agent.role}
        species={agent.species}
        mood={agent.mood}
        seed={agent.name}
        walkPhase={motion.isMoving ? motion.walkPhase : undefined}
        facingLeft={motion.facingLeft}
      />

      <div className="absolute top-full left-1/2 -translate-x-1/2 mt-0.5 flex flex-col items-center whitespace-nowrap">
        <div
          className={`rounded-full bg-black/70 px-1.5 py-[1px] font-mono text-[9px] font-bold ${rarityClass} shadow-md`}
        >
          {agent.name.slice(0, 10)} · Lv{agent.level}
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
    </div>
  );
}

"use client";

import type { AgentData } from "@/lib/types";
import MapScene, { GROUND_Y_PERCENT } from "./stage/MapScene";
import Chibi from "./stage/Chibi";
import { useAgentMotion, type MotionState } from "./stage/useAgentMotion";

interface Props {
  agents: AgentData[];
  sky?: string;
}

const RARITY_TEXT: Record<string, string> = {
  legendary: "text-amber-300",
  rare: "text-purple-300",
  uncommon: "text-cyan-300",
  common: "text-white/90",
};

function resolveSky(sky: string | undefined): string {
  if (!sky) return "day";
  const s = sky.toLowerCase();
  if (s.includes("dawn")) return "dawn";
  if (s.includes("dusk") || s.includes("sunset") || s.includes("evening")) return "dusk";
  if (s.includes("night") || s.includes("midnight") || s.includes("star")) return "night";
  return "day";
}

export default function Stage({ agents, sky }: Props) {
  const skyMode = resolveSky(sky);
  const motions = useAgentMotion({ agents, groundYPercent: GROUND_Y_PERCENT });

  if (agents.length === 0) {
    return (
      <div className="relative h-full overflow-hidden rounded-xl border border-cyan-500/20">
        <MapScene sky={skyMode} theme="meadow">
          <div className="flex h-full items-center justify-center">
            <p className="font-mono text-white/60 text-lg drop-shadow-lg">
              The town awaits a hero...
            </p>
          </div>
        </MapScene>
      </div>
    );
  }

  return (
    <div className="relative h-full overflow-hidden rounded-xl border border-cyan-500/20">
      <MapScene sky={skyMode} theme="meadow">
        {agents.map((agent) => {
          const motion = motions[agent.name];
          if (!motion) return null;
          return <AgentOnMap key={agent.name} agent={agent} motion={motion} />;
        })}
      </MapScene>
    </div>
  );
}

function AgentOnMap({ agent, motion }: { agent: AgentData; motion: MotionState }) {
  const rarityClass = RARITY_TEXT[agent.rarity || "common"] ?? RARITY_TEXT.common;
  const size = 84;

  return (
    <div
      className="absolute will-change-transform"
      style={{
        left: `${motion.x}%`,
        top: `${motion.y}%`,
        transform: `translate(-50%, -100%) translateY(${-motion.jumpOffset}px)`,
        transition: "top 120ms linear",
      }}
    >
      {agent.speech && (
        <div className="absolute -top-14 left-1/2 -translate-x-1/2 max-w-[200px]">
          <div className="relative rounded-xl border border-slate-700 bg-white/95 px-3 py-1.5 text-xs text-slate-900 shadow-lg">
            <div className="truncate font-medium">{agent.speech}</div>
            <div className="absolute -bottom-[5px] left-1/2 h-2.5 w-2.5 -translate-x-1/2 rotate-45 border-b border-r border-slate-700 bg-white/95" />
          </div>
        </div>
      )}

      <div className="flex flex-col items-center">
        <Chibi
          species={agent.species}
          mood={agent.mood}
          state={agent.state}
          role={agent.role}
          seed={agent.name}
          facingLeft={motion.facingLeft}
          walkPhase={motion.isMoving ? motion.walkPhase : undefined}
          rarity={agent.rarity}
          size={size}
        />
        <div
          className={`mt-0.5 rounded-full bg-black/55 px-2 py-0.5 font-mono text-[10px] font-bold ${rarityClass} shadow-md`}
        >
          {agent.name.slice(0, 10)} · Lv{agent.level}
        </div>
        <div className="mt-0.5 h-1 w-14 overflow-hidden rounded-full bg-black/40">
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

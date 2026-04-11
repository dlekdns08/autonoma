"use client";

import { useMemo } from "react";
import type { AgentData } from "@/lib/types";
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
} from "./stage/useAgentMotion";

interface Props {
  agents: AgentData[];
  sky?: string;
  theme?: MapTheme;
  onSelectAgent?: (name: string) => void;
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

export default function Stage({ agents, sky, theme = "meadow" }: Props) {
  const skyMode = resolveSky(sky);
  const map = useMemo(() => buildMap(theme), [theme]);
  const { motions, bubbles } = useAgentMotion({ agents, map });

  if (agents.length === 0) {
    return (
      <div className="relative h-full overflow-hidden rounded-xl border border-cyan-500/20">
        <PixelMap sky={skyMode} theme={theme}>
          <div className="flex h-full items-center justify-center">
            <p className="font-mono text-white/60 text-lg drop-shadow-lg">
              The town awaits a hero...
            </p>
          </div>
        </PixelMap>
      </div>
    );
  }

  return (
    <div className="relative h-full overflow-hidden rounded-xl border border-cyan-500/20">
      <PixelMap sky={skyMode} theme={theme}>
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
            />
          );
        })}
      </PixelMap>
    </div>
  );
}

function AgentOnMap({
  agent,
  motion,
  dialogue,
}: {
  agent: AgentData;
  motion: MotionState;
  dialogue: DialogueBubble | null;
}) {
  const rarityClass = RARITY_TEXT[agent.rarity || "common"] ?? RARITY_TEXT.common;
  const speech = dialogue?.text ?? agent.speech;

  return (
    <div
      className="absolute"
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

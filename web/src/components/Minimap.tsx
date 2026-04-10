"use client";

import type { AgentData } from "@/lib/types";

interface Props {
  agents: AgentData[];
  onSelectAgent?: (name: string) => void;
}

const STATE_COLORS: Record<string, string> = {
  working: "#22d3ee",    // cyan
  thinking: "#a78bfa",   // purple
  celebrating: "#fbbf24", // yellow
  idle: "rgba(255,255,255,0.3)",
  talking: "#34d399",    // green
};

export default function Minimap({ agents, onSelectAgent }: Props) {
  if (agents.length === 0) return null;

  return (
    <div className="rounded-xl border border-white/10 bg-slate-950/80 p-2 backdrop-blur-sm">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[9px] font-mono text-white/30">MAP</span>
        <span className="text-[8px] font-mono text-white/20">{agents.length} agents</span>
      </div>

      <div className="relative w-full aspect-[2/1] rounded-lg bg-white/[0.02] border border-white/5 overflow-hidden">
        {/* Grid lines */}
        <svg className="absolute inset-0 w-full h-full opacity-20" viewBox="0 0 100 50">
          {[25, 50, 75].map((x) => (
            <line key={`v${x}`} x1={x} y1={0} x2={x} y2={50} stroke="white" strokeWidth="0.2" />
          ))}
          {[12.5, 25, 37.5].map((y) => (
            <line key={`h${y}`} x1={0} y1={y} x2={100} y2={y} stroke="white" strokeWidth="0.2" />
          ))}
        </svg>

        {/* Agent dots */}
        {agents.map((agent, idx) => {
          const cols = Math.min(agents.length, 4);
          const row = Math.floor(idx / cols);
          const col = idx % cols;
          const x = 10 + (col / Math.max(cols - 1, 1)) * 80;
          const y = 15 + row * 35;
          const color = STATE_COLORS[agent.state] || STATE_COLORS.idle;

          return (
            <div
              key={agent.name}
              className="absolute cursor-pointer group"
              style={{
                left: `${x}%`,
                top: `${y}%`,
                transform: "translate(-50%, -50%)",
              }}
              onClick={() => onSelectAgent?.(agent.name)}
            >
              {/* Pulse ring for working agents */}
              {agent.state === "working" && (
                <div
                  className="absolute inset-0 rounded-full animate-ping"
                  style={{
                    background: color,
                    width: 8,
                    height: 8,
                    margin: -1,
                    opacity: 0.3,
                  }}
                />
              )}

              {/* Dot */}
              <div
                className="w-[6px] h-[6px] rounded-full transition-all duration-300"
                style={{ background: color }}
              />

              {/* Tooltip */}
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 hidden group-hover:block whitespace-nowrap rounded bg-black/80 px-1.5 py-0.5 text-[8px] text-white font-mono">
                {agent.species_emoji || agent.emoji} {agent.name}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

"use client";

import { useMemo } from "react";
import type { AgentData, RelationshipData } from "@/lib/types";

interface Props {
  agents: AgentData[];
  relationships: RelationshipData[];
  onSelectAgent?: (name: string) => void;
}

export default function RelationshipWeb({ agents, relationships, onSelectAgent }: Props) {
  const layout = useMemo(() => {
    if (agents.length === 0) return { nodes: [], edges: [] };

    const cx = 150;
    const cy = 120;
    const radius = Math.min(90, 40 + agents.length * 10);

    const nodes = agents.map((agent, i) => {
      const angle = (Math.PI * 2 * i) / agents.length - Math.PI / 2;
      return {
        agent,
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
      };
    });

    const edges = relationships
      .filter((r) => r.trust !== undefined)
      .map((r) => {
        const from = nodes.find((n) => n.agent.name === r.from);
        const to = nodes.find((n) => n.agent.name === r.to);
        if (!from || !to) return null;

        const trust = r.trust ?? 0.5;
        const color =
          trust >= 0.8 ? "rgba(236,72,153,0.6)" :   // love — pink
          trust >= 0.5 ? "rgba(34,211,238,0.4)" :    // friendly — cyan
          trust >= 0.3 ? "rgba(255,255,255,0.15)" :   // neutral
          "rgba(239,68,68,0.4)";                       // rivalry — red

        const width = Math.max(0.5, Math.abs(trust - 0.5) * 4);

        return { from, to, trust, color, width, label: r.label };
      })
      .filter(Boolean) as Array<{
        from: (typeof nodes)[0];
        to: (typeof nodes)[0];
        trust: number;
        color: string;
        width: number;
        label?: string;
      }>;

    return { nodes, edges };
  }, [agents, relationships]);

  if (agents.length === 0) {
    return (
      <div className="flex flex-col gap-2 rounded-xl border border-pink-500/20 bg-slate-900/50 p-3">
        <h3 className="text-xs font-bold text-pink-300 font-mono">♥ Bonds ♥</h3>
        <p className="text-xs text-white/30 font-mono">(._.) No agents yet...</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-pink-500/20 bg-slate-900/50 p-3">
      <h3 className="text-xs font-bold text-pink-300 font-mono">♥ Bonds ♥</h3>
      <svg viewBox="0 0 300 240" className="w-full">
        {/* Edges */}
        {layout.edges.map((edge, i) => (
          <g key={i}>
            <line
              x1={edge.from.x}
              y1={edge.from.y}
              x2={edge.to.x}
              y2={edge.to.y}
              stroke={edge.color}
              strokeWidth={edge.width}
              strokeDasharray={edge.trust < 0.3 ? "4,4" : "none"}
            />
            {/* Heart for high trust */}
            {edge.trust >= 0.8 && (
              <text
                x={(edge.from.x + edge.to.x) / 2}
                y={(edge.from.y + edge.to.y) / 2 - 4}
                textAnchor="middle"
                fontSize="8"
                fill="#ec4899"
              >
                ♥
              </text>
            )}
            {/* Crossed swords for rivalry */}
            {edge.trust < 0.2 && (
              <text
                x={(edge.from.x + edge.to.x) / 2}
                y={(edge.from.y + edge.to.y) / 2 - 4}
                textAnchor="middle"
                fontSize="8"
                fill="#ef4444"
              >
                ⚔
              </text>
            )}
          </g>
        ))}

        {/* Nodes */}
        {layout.nodes.map(({ agent, x, y }) => (
          <g
            key={agent.name}
            className="cursor-pointer"
            onClick={() => onSelectAgent?.(agent.name)}
          >
            {/* Glow circle */}
            <circle cx={x} cy={y} r="18" fill="rgba(0,0,0,0.4)" stroke="rgba(255,255,255,0.1)" strokeWidth="1" />
            {/* Emoji */}
            <text x={x} y={y + 1} textAnchor="middle" dominantBaseline="middle" fontSize="16">
              {agent.species_emoji || agent.emoji}
            </text>
            {/* Name */}
            <text
              x={x}
              y={y + 28}
              textAnchor="middle"
              fill="rgba(255,255,255,0.6)"
              fontSize="7"
              fontFamily="monospace"
            >
              {agent.name.slice(0, 10)}
            </text>
          </g>
        ))}
      </svg>

      {/* Legend */}
      <div className="flex items-center gap-3 text-[8px] text-white/30 font-mono mt-1">
        <span><span className="text-pink-400">♥</span> love</span>
        <span><span className="text-cyan-400">—</span> friend</span>
        <span><span className="text-white/30">- -</span> neutral</span>
        <span><span className="text-red-400">⚔</span> rival</span>
      </div>
    </div>
  );
}

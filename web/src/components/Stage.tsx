"use client";

import { useEffect, useState } from "react";
import type { AgentData } from "@/lib/types";

const MOOD_FACES: Record<string, string> = {
  happy: "(^w^)",
  focused: "(>_<)",
  frustrated: "(>.<)",
  excited: "(*^*)",
  tired: "(-_-)",
  proud: "(^_~)",
  worried: "(o_o)",
  curious: "(?.?)",
  determined: "(!_!)",
  relaxed: "(~_~)",
  inspired: "(!!)",
  mischievous: "(>w<)",
  nostalgic: "(._.):",
};

const RARITY_COLORS: Record<string, string> = {
  legendary: "text-amber-400 drop-shadow-[0_0_6px_rgba(251,191,36,0.6)]",
  rare: "text-purple-400",
  uncommon: "text-cyan-400",
  common: "text-white/80",
};

const STATE_ANIMATIONS: Record<string, string> = {
  working: "animate-bounce",
  celebrating: "animate-pulse",
  thinking: "animate-spin-slow",
  talking: "",
  idle: "",
};

interface Props {
  agents: AgentData[];
}

// Floating particles
interface Particle {
  id: number;
  x: number;
  y: number;
  char: string;
  opacity: number;
}

let particleId = 0;

export default function Stage({ agents }: Props) {
  const [particles, setParticles] = useState<Particle[]>([]);
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    const t = setInterval(() => {
      setFrame((f) => f + 1);
      setParticles((prev) => {
        // Move particles up and fade
        const moved = prev
          .map((p) => ({ ...p, y: p.y - 1, opacity: p.opacity - 0.05 }))
          .filter((p) => p.opacity > 0 && p.y > 0);

        // Spawn new particles for celebrating agents
        const celebrating = agents.filter((a) => a.state === "celebrating");
        if (celebrating.length > 0 && Math.random() < 0.4) {
          const chars = ["✦", "♥", "★", "♪", "✧", "♡", "☆"];
          moved.push({
            id: particleId++,
            x: 10 + Math.random() * 80,
            y: 90 + Math.random() * 10,
            char: chars[Math.floor(Math.random() * chars.length)],
            opacity: 1,
          });
        }
        return moved.slice(-40);
      });
    }, 200);
    return () => clearInterval(t);
  }, [agents]);

  if (agents.length === 0) {
    const msgs = ["(^_^) All agents standing by~", "(-_-)zzZ Waiting...", "(o.o) Ready!"];
    return (
      <div className="relative flex h-full items-center justify-center rounded-xl border border-cyan-500/20 bg-gradient-to-b from-slate-950 to-slate-900">
        <p className="font-mono text-white/30 text-lg">{msgs[frame % msgs.length]}</p>
      </div>
    );
  }

  return (
    <div className="relative h-full overflow-hidden rounded-xl border border-cyan-500/20 bg-gradient-to-b from-slate-950 via-slate-900 to-indigo-950/30">
      {/* Floating particles */}
      {particles.map((p) => (
        <span
          key={p.id}
          className="pointer-events-none absolute text-xs transition-all duration-200"
          style={{
            left: `${p.x}%`,
            top: `${p.y}%`,
            opacity: p.opacity,
            color: p.char === "♥" || p.char === "♡" ? "#f472b6" : "#fcd34d",
          }}
        >
          {p.char}
        </span>
      ))}

      {/* Agents */}
      <div className="absolute inset-0 p-4">
        <div className="relative h-full">
          {agents.map((agent, idx) => (
            <AgentSprite key={agent.name} agent={agent} index={idx} total={agents.length} frame={frame} />
          ))}
        </div>
      </div>

      {/* Stage label */}
      <div className="absolute bottom-1 left-1/2 -translate-x-1/2 text-[10px] font-mono text-white/20">
        frame {frame % 1000}
      </div>
    </div>
  );
}

function AgentSprite({ agent, index, total, frame }: { agent: AgentData; index: number; total: number; frame: number }) {
  // Distribute agents across the stage
  const cols = Math.min(total, 4);
  const row = Math.floor(index / cols);
  const col = index % cols;
  const xPct = 5 + (col / Math.max(cols - 1, 1)) * 80;
  const yPct = 10 + row * 40;

  const moodFace = MOOD_FACES[agent.mood] || "(._.)";
  const rarityClass = RARITY_COLORS[agent.rarity || "common"];
  const stateAnim = STATE_ANIMATIONS[agent.state] || "";

  const xpPct = agent.xp_to_next > 0 ? (agent.xp / agent.xp_to_next) * 100 : 0;

  return (
    <div
      className="absolute transition-all duration-500 ease-out"
      style={{ left: `${xPct}%`, top: `${yPct}%`, transform: "translate(-50%, -50%)" }}
    >
      {/* Speech Bubble */}
      {agent.speech && (
        <div className="absolute -top-16 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-lg border border-white/20 bg-slate-800/90 px-3 py-1.5 text-xs text-white/90 shadow-lg backdrop-blur-sm">
          <div className="max-w-[180px] truncate">{agent.speech}</div>
          <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 h-2 w-2 rotate-45 border-b border-r border-white/20 bg-slate-800/90" />
        </div>
      )}

      {/* Agent Body */}
      <div className={`flex flex-col items-center gap-0.5 ${stateAnim}`}>
        {/* Species Emoji */}
        <span className="text-2xl">{agent.species_emoji || agent.emoji}</span>

        {/* Mood Face */}
        <span className="font-mono text-xs text-white/70">{moodFace}</span>

        {/* Name Tag */}
        <div className={`mt-0.5 rounded-full px-2 py-0.5 text-[10px] font-bold ${rarityClass} bg-white/5 font-mono`}>
          ~{agent.name.slice(0, 8)}~
        </div>

        {/* Level & XP Bar */}
        <div className="flex items-center gap-1 mt-0.5">
          <span className="text-[9px] font-mono text-yellow-400">Lv{agent.level}</span>
          <div className="h-1 w-12 rounded-full bg-white/10 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-fuchsia-500 transition-all duration-300"
              style={{ width: `${xpPct}%` }}
            />
          </div>
        </div>

        {/* Role */}
        <span className="text-[8px] text-white/30 font-mono">{agent.role}</span>
      </div>
    </div>
  );
}

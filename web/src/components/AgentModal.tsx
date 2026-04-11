"use client";

import { useEffect, useRef, useState } from "react";
import type { AgentData } from "@/lib/types";

interface Props {
  agent: AgentData;
  onClose: () => void;
  onSend?: (agentName: string, message: string) => void;
}

// SVG radar chart for agent stats
function StatsRadar({ stats }: { stats: Record<string, number> }) {
  const entries = Object.entries(stats).slice(0, 6);
  if (entries.length < 3) return null;

  const cx = 100;
  const cy = 100;
  const r = 70;
  const n = entries.length;

  // Background polygon rings
  const rings = [0.33, 0.66, 1.0];
  const ringPaths = rings.map((scale) => {
    const points = entries
      .map((_, i) => {
        const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
        return `${cx + Math.cos(angle) * r * scale},${cy + Math.sin(angle) * r * scale}`;
      })
      .join(" ");
    return points;
  });

  // Data polygon
  const maxStat = Math.max(...entries.map(([, v]) => v), 10);
  const dataPoints = entries
    .map(([, v], i) => {
      const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
      const scale = v / maxStat;
      return `${cx + Math.cos(angle) * r * scale},${cy + Math.sin(angle) * r * scale}`;
    })
    .join(" ");

  // Labels
  const labels = entries.map(([k], i) => {
    const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
    const lx = cx + Math.cos(angle) * (r + 18);
    const ly = cy + Math.sin(angle) * (r + 18);
    return { x: lx, y: ly, label: k };
  });

  return (
    <svg viewBox="0 0 200 200" className="w-full max-w-[200px] mx-auto">
      {/* Grid lines */}
      {ringPaths.map((pts, i) => (
        <polygon key={i} points={pts} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="1" />
      ))}
      {/* Axis lines */}
      {entries.map((_, i) => {
        const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
        return (
          <line
            key={i}
            x1={cx}
            y1={cy}
            x2={cx + Math.cos(angle) * r}
            y2={cy + Math.sin(angle) * r}
            stroke="rgba(255,255,255,0.06)"
            strokeWidth="1"
          />
        );
      })}
      {/* Data area */}
      <polygon
        points={dataPoints}
        fill="rgba(139,92,246,0.25)"
        stroke="rgba(139,92,246,0.7)"
        strokeWidth="2"
      />
      {/* Data points */}
      {entries.map(([, v], i) => {
        const angle = (Math.PI * 2 * i) / n - Math.PI / 2;
        const scale = v / maxStat;
        return (
          <circle
            key={i}
            cx={cx + Math.cos(angle) * r * scale}
            cy={cy + Math.sin(angle) * r * scale}
            r="3"
            fill="#a78bfa"
            stroke="#7c3aed"
            strokeWidth="1"
          />
        );
      })}
      {/* Labels */}
      {labels.map(({ x, y, label }, i) => (
        <text
          key={i}
          x={x}
          y={y}
          textAnchor="middle"
          dominantBaseline="middle"
          fill="rgba(255,255,255,0.5)"
          fontSize="8"
          fontFamily="monospace"
        >
          {label}
        </text>
      ))}
    </svg>
  );
}

// Mood history sparkline
function MoodSparkline({ mood }: { mood: string }) {
  const MOOD_VALUES: Record<string, number> = {
    frustrated: 1, worried: 2, tired: 3, focused: 4,
    relaxed: 5, happy: 6, proud: 7, excited: 8, inspired: 9,
  };
  const val = MOOD_VALUES[mood] || 5;
  const bars = Array.from({ length: 10 }, (_, i) => (i < val ? val : 0));

  return (
    <div className="flex items-end gap-0.5 h-6">
      {bars.map((v, i) => (
        <div
          key={i}
          className="w-1.5 rounded-full transition-all duration-300"
          style={{
            height: `${(v / 9) * 100}%`,
            background: v > 0
              ? `hsl(${(v / 9) * 120}, 70%, 60%)`
              : "rgba(255,255,255,0.05)",
          }}
        />
      ))}
    </div>
  );
}

export default function AgentModal({ agent, onClose, onSend }: Props) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const [tab, setTab] = useState<"stats" | "info">("stats");
  const [instruction, setInstruction] = useState("");
  const [sentFlash, setSentFlash] = useState(false);

  const handleSend = () => {
    const text = instruction.trim();
    if (!text || !onSend) return;
    onSend(agent.name, text);
    setInstruction("");
    setSentFlash(true);
    setTimeout(() => setSentFlash(false), 900);
  };

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  const xpPct = agent.xp_to_next > 0 ? (agent.xp / agent.xp_to_next) * 100 : 0;

  const RARITY_STYLES: Record<string, string> = {
    legendary: "text-amber-400 border-amber-500/50 bg-amber-500/10",
    rare: "text-purple-400 border-purple-500/50 bg-purple-500/10",
    uncommon: "text-cyan-400 border-cyan-500/50 bg-cyan-500/10",
    common: "text-white/60 border-white/20 bg-white/5",
  };

  const rarityStyle = RARITY_STYLES[agent.rarity || "common"];

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fade-in"
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div className="w-full max-w-md rounded-2xl border border-purple-500/30 bg-gradient-to-b from-slate-900 to-slate-950 shadow-2xl shadow-purple-500/10 overflow-hidden">
        {/* Header */}
        <div className="relative px-6 py-5 bg-gradient-to-r from-purple-950/50 via-fuchsia-950/30 to-purple-950/50 border-b border-purple-500/20">
          <button
            onClick={onClose}
            className="absolute top-3 right-3 text-white/30 hover:text-white/70 transition-colors text-lg"
          >
            ✕
          </button>

          <div className="flex items-center gap-4">
            <div className="text-5xl">{agent.species_emoji || agent.emoji}</div>
            <div>
              <h2 className="text-xl font-bold text-white font-mono">{agent.name}</h2>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-sm text-white/50">{agent.role}</span>
                {agent.rarity && agent.rarity !== "common" && (
                  <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold border ${rarityStyle}`}>
                    {agent.rarity.toUpperCase()}
                  </span>
                )}
              </div>
              {agent.catchphrase && (
                <p className="text-xs text-white/30 italic mt-1">&quot;{agent.catchphrase}&quot;</p>
              )}
            </div>
          </div>

          {/* Level + XP */}
          <div className="mt-4 flex items-center gap-3">
            <div className="flex items-center gap-1.5 rounded-full bg-yellow-500/20 px-3 py-1">
              <span className="text-yellow-400 font-mono text-sm font-bold">Lv {agent.level}</span>
            </div>
            <div className="flex-1">
              <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-cyan-500 via-purple-500 to-fuchsia-500 transition-all duration-500"
                  style={{ width: `${xpPct}%` }}
                />
              </div>
              <div className="flex justify-between mt-0.5">
                <span className="text-[9px] text-white/30 font-mono">{agent.xp} XP</span>
                <span className="text-[9px] text-white/30 font-mono">{agent.xp_to_next} to next</span>
              </div>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-white/5">
          {(["stats", "info"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 py-2 text-xs font-mono transition-colors ${
                tab === t ? "text-purple-300 border-b-2 border-purple-400" : "text-white/30 hover:text-white/50"
              }`}
            >
              {t === "stats" ? "★ Stats" : "♪ Info"}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="p-5 max-h-80 overflow-y-auto scrollbar-thin">
          {tab === "stats" && (
            <div className="flex flex-col gap-4">
              {/* Stats Radar */}
              {agent.stats && Object.keys(agent.stats).length >= 3 ? (
                <StatsRadar stats={agent.stats} />
              ) : (
                <div className="text-center text-xs text-white/30 font-mono py-4">
                  (?.?) Stats not available yet
                </div>
              )}

              {/* Mood */}
              <div className="flex items-center justify-between rounded-lg bg-white/[0.03] p-3 border border-white/5">
                <div>
                  <div className="text-[10px] text-white/30 font-mono">Current Mood</div>
                  <div className="text-sm text-white/80 font-mono mt-0.5">{agent.mood}</div>
                </div>
                <MoodSparkline mood={agent.mood} />
              </div>

              {/* State */}
              <div className="flex items-center justify-between rounded-lg bg-white/[0.03] p-3 border border-white/5">
                <div>
                  <div className="text-[10px] text-white/30 font-mono">State</div>
                  <div className="text-sm text-white/80 font-mono mt-0.5">{agent.state}</div>
                </div>
                <div className={`h-3 w-3 rounded-full ${
                  agent.state === "working" ? "bg-cyan-400 animate-pulse" :
                  agent.state === "celebrating" ? "bg-yellow-400 animate-bounce" :
                  agent.state === "thinking" ? "bg-purple-400 animate-spin-slow" :
                  "bg-white/20"
                }`} />
              </div>
            </div>
          )}

          {tab === "info" && (
            <div className="flex flex-col gap-3">
              {/* Traits */}
              {agent.traits && agent.traits.length > 0 && (
                <div className="rounded-lg bg-white/[0.03] p-3 border border-white/5">
                  <div className="text-[10px] text-white/30 font-mono mb-2">Traits</div>
                  <div className="flex flex-wrap gap-1.5">
                    {agent.traits.map((t) => (
                      <span key={t} className="rounded-full bg-purple-500/15 px-2.5 py-1 text-[11px] text-purple-300 font-mono border border-purple-500/20">
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Species */}
              {agent.species && (
                <div className="rounded-lg bg-white/[0.03] p-3 border border-white/5">
                  <div className="text-[10px] text-white/30 font-mono mb-1">Species</div>
                  <div className="text-sm text-white/70 font-mono">
                    {agent.species_emoji} {agent.species}
                  </div>
                </div>
              )}

              {/* Speech */}
              {agent.speech && (
                <div className="rounded-lg bg-white/[0.03] p-3 border border-white/5">
                  <div className="text-[10px] text-white/30 font-mono mb-1">Last Speech</div>
                  <div className="text-xs text-white/60 font-mono italic">
                    &quot;{agent.speech}&quot;
                  </div>
                </div>
              )}

              {/* Position */}
              <div className="rounded-lg bg-white/[0.03] p-3 border border-white/5">
                <div className="text-[10px] text-white/30 font-mono mb-1">Position</div>
                <div className="text-xs text-white/50 font-mono">
                  ({agent.position.x}, {agent.position.y})
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Direct Instruction Composer */}
        {onSend && (
          <div className="border-t border-purple-500/20 bg-slate-950/60 px-5 py-3">
            <div className="mb-1.5 flex items-center justify-between">
              <label className="text-[10px] font-mono uppercase tracking-wider text-purple-300/70">
                ✉ Direct Instruction to {agent.name}
              </label>
              {sentFlash && (
                <span className="text-[9px] font-mono text-emerald-400">sent ✓</span>
              )}
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSend();
                  }
                }}
                placeholder={`Tell ${agent.name} what to do...`}
                className="flex-1 rounded-md border border-white/10 bg-black/40 px-3 py-1.5 text-xs text-white/90 font-mono placeholder-white/25 outline-none focus:border-purple-400/60"
              />
              <button
                type="button"
                onClick={handleSend}
                disabled={!instruction.trim()}
                className="rounded-md border border-purple-500/40 bg-purple-500/20 px-3 py-1.5 text-xs font-mono text-purple-200 transition-colors hover:bg-purple-500/30 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

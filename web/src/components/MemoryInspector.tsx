"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

interface MemoryExperience {
  category?: string;
  content: string;
}

interface MemoryRelationship {
  name: string;
  trust: number;
}

interface AgentMemory {
  experiences?: MemoryExperience[];
  hindsight?: string[];
  relationships?: MemoryRelationship[];
}

interface Props {
  agentNames: string[];
}

export default function MemoryInspector({ agentNames }: Props) {
  const [selected, setSelected] = useState<string>(agentNames[0] ?? "");
  const [memory, setMemory] = useState<AgentMemory | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchMemory = useCallback(async (agent: string) => {
    if (!agent) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/agents/${encodeURIComponent(agent)}/memory`, {
        credentials: "include",
      });
      if (!res.ok) {
        setError(`${res.status} ${res.statusText}`);
        setMemory(null);
        return;
      }
      const data = (await res.json()) as AgentMemory;
      setMemory(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Network error");
      setMemory(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selected) fetchMemory(selected);
  }, [selected, fetchMemory]);

  // Sync selected if agentNames changes and current selection is gone
  useEffect(() => {
    if (agentNames.length > 0 && !agentNames.includes(selected)) {
      setSelected(agentNames[0]);
    }
  }, [agentNames, selected]);

  const CATEGORY_COLORS: Record<string, { bg: string; color: string }> = {
    task:         { bg: "rgba(34,211,238,0.12)", color: "#67e8f9" },
    social:       { bg: "rgba(139,92,246,0.12)", color: "#c4b5fd" },
    error:        { bg: "rgba(239,68,68,0.12)",  color: "#fca5a5" },
    achievement:  { bg: "rgba(245,158,11,0.12)", color: "#fcd34d" },
    observation:  { bg: "rgba(255,255,255,0.05)", color: "#9d8ec4" },
  };

  return (
    <div
      className="flex flex-col gap-4 rounded-xl p-4"
      style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(12,11,29,0.85)" }}
    >
      <div className="flex items-center gap-3">
        <h2 className="font-mono font-bold text-sm text-violet-300 tracking-widest uppercase flex-1">
          ◈ Memory Inspector
        </h2>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="rounded-md border border-white/10 bg-black/50 px-2 py-1 text-xs font-mono text-white/80 outline-none focus:border-violet-400/50"
        >
          {agentNames.length === 0 ? (
            <option value="">— no agents —</option>
          ) : (
            agentNames.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))
          )}
        </select>
        <button
          type="button"
          onClick={() => fetchMemory(selected)}
          disabled={!selected || loading}
          className="rounded-md border border-violet-500/30 bg-violet-500/15 px-3 py-1 text-[11px] font-mono text-violet-300 transition-colors hover:bg-violet-500/25 disabled:opacity-40"
        >
          {loading ? "..." : "↺ Refresh"}
        </button>
      </div>

      {error && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2 text-xs font-mono text-red-400">
          {error}
        </div>
      )}

      {!memory && !loading && !error && (
        <p className="text-xs text-white/30 font-mono">(^_^) Select an agent to inspect memory.</p>
      )}

      {memory && (
        <div className="flex flex-col gap-4">
          {/* Experiences */}
          {memory.experiences && memory.experiences.length > 0 && (
            <div>
              <div className="text-[10px] font-mono text-white/30 uppercase tracking-widest mb-2">
                Recent Experiences
              </div>
              <div className="flex flex-col gap-1.5">
                {memory.experiences.slice(-8).map((exp, i) => {
                  const cat = exp.category ?? "observation";
                  const style = CATEGORY_COLORS[cat] ?? CATEGORY_COLORS.observation;
                  return (
                    <div key={i} className="flex items-start gap-2">
                      <span
                        className="rounded px-1.5 py-0.5 text-[8px] font-mono shrink-0 mt-0.5"
                        style={{ background: style.bg, color: style.color }}
                      >
                        {cat}
                      </span>
                      <span className="text-xs text-white/60 font-mono leading-relaxed">{exp.content}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Hindsight */}
          {memory.hindsight && memory.hindsight.length > 0 && (
            <div>
              <div className="text-[10px] font-mono text-white/30 uppercase tracking-widest mb-2">
                Hindsight Notes
              </div>
              <div className="flex flex-col gap-1">
                {memory.hindsight.map((note, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs font-mono text-white/50">
                    <span className="text-amber-400/60 shrink-0">•</span>
                    <span>{note}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Relationship opinions */}
          {memory.relationships && memory.relationships.length > 0 && (
            <div>
              <div className="text-[10px] font-mono text-white/30 uppercase tracking-widest mb-2">
                Relationship Trust
              </div>
              <div className="flex flex-col gap-1.5">
                {memory.relationships.map((rel) => {
                  const pct = Math.max(0, Math.min(100, rel.trust));
                  const filled = Math.round(pct / 10);
                  const bar = "█".repeat(filled) + "░".repeat(10 - filled);
                  return (
                    <div key={rel.name} className="flex items-center gap-2 text-[10px] font-mono">
                      <span className="text-white/50 w-24 truncate">{rel.name}</span>
                      <span className="text-purple-400/60">{bar}</span>
                      <span className="text-white/40">{pct}%</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

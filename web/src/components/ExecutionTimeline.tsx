"use client";

import { memo, useMemo, useState } from "react";
import type { EventLogEntry } from "@/lib/types";

const TIMELINE_EVENTS = new Set([
  "task.assigned",
  "task.started",
  "task.completed",
  "sandbox.run_started",
  "sandbox.run_finished",
  "agent.spawned",
]);

const EVENT_BADGE: Record<string, { label: string; color: string; bg: string }> = {
  "agent.spawned":        { label: "spawned",   color: "#6ee7b7", bg: "rgba(16,185,129,0.15)" },
  "task.assigned":        { label: "assigned",  color: "#fcd34d", bg: "rgba(245,158,11,0.15)" },
  "task.started":         { label: "started",   color: "#67e8f9", bg: "rgba(34,211,238,0.15)" },
  "task.completed":       { label: "done",      color: "#86efac", bg: "rgba(34,197,94,0.15)"  },
  "sandbox.run_started":  { label: "run▶",      color: "#c4b5fd", bg: "rgba(139,92,246,0.15)" },
  "sandbox.run_finished": { label: "run■",      color: "#a78bfa", bg: "rgba(109,40,217,0.15)" },
};

function getAgent(entry: EventLogEntry): string {
  return (entry.data.agent as string) || (entry.data.name as string) || "unknown";
}

function getLabel(entry: EventLogEntry): string {
  const d = entry.data;
  switch (entry.event) {
    case "agent.spawned":
      return `${d.emoji ?? ""} ${d.name ?? "?"} (${d.role ?? "agent"})`;
    case "task.assigned":
    case "task.started":
    case "task.completed":
      return (d.title as string) ?? entry.event;
    case "sandbox.run_started":
    case "sandbox.run_finished": {
      const lang = (d.language as string) ?? "code";
      const ok = entry.event === "sandbox.run_finished" ? ((d.ok as boolean) ? " ✓" : " ✗") : "";
      return `${lang}${ok}`;
    }
    default:
      return entry.event;
  }
}

interface Props {
  events: EventLogEntry[];
}

export default function ExecutionTimeline({ events }: Props) {
  const [collapsed, setCollapsed] = useState(false);

  // Filter and limit
  const relevant = events
    .filter((e) => TIMELINE_EVENTS.has(e.event))
    .slice(-50);

  // Group by agent
  const byAgent: Record<string, EventLogEntry[]> = {};
  for (const entry of relevant) {
    const agent = getAgent(entry);
    if (!byAgent[agent]) byAgent[agent] = [];
    byAgent[agent].push(entry);
  }

  const agentNames = Object.keys(byAgent);

  return (
    <div
      className="flex flex-col rounded-xl"
      style={{ border: "1px solid rgba(255,255,255,0.06)", background: "rgba(12,11,29,0.7)" }}
    >
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="flex items-center gap-2 px-3 py-2 w-full text-left"
        style={{ borderBottom: collapsed ? "none" : "1px solid rgba(255,255,255,0.05)" }}
      >
        <span className="text-cyan-400 text-[11px]">◈</span>
        <h3 className="text-[11px] font-bold text-cyan-300 font-mono tracking-widest uppercase flex-1">
          Timeline
        </h3>
        <span className="text-white/30 text-[10px] ml-1">{collapsed ? "▶" : "▼"}</span>
      </button>

      {!collapsed && (
        <div className="flex flex-col gap-3 p-2 max-h-64 overflow-y-auto scrollbar-thin">
          {agentNames.length === 0 ? (
            <p className="text-xs text-white/30 font-mono px-1">(^_^) No events yet...</p>
          ) : (
            agentNames.map((agentName) => (
              <div key={agentName}>
                <div className="text-[9px] font-mono text-white/40 mb-1 px-1">{agentName}</div>
                <div className="flex flex-col gap-0.5 pl-2 border-l border-white/10">
                  {byAgent[agentName].map((entry) => {
                    const badge = EVENT_BADGE[entry.event] ?? {
                      label: entry.event,
                      color: "#9d8ec4",
                      bg: "rgba(139,92,246,0.08)",
                    };
                    return (
                      <div
                        key={entry.id}
                        className="flex items-center gap-1.5 text-[10px] font-mono"
                      >
                        <span
                          className="rounded px-1 py-0.5 text-[8px] shrink-0"
                          style={{ background: badge.bg, color: badge.color }}
                        >
                          {badge.label}
                        </span>
                        <span className="text-white/50 truncate">{getLabel(entry)}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

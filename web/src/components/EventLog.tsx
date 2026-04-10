"use client";

import { useEffect, useRef } from "react";
import type { EventLogEntry } from "@/lib/types";

const EVENT_STYLES: Record<string, { icon: string; color: string }> = {
  "agent.spawned": { icon: "★", color: "text-green-400" },
  "agent.speech": { icon: "💬", color: "text-white/70" },
  "agent.level_up": { icon: "★★★", color: "text-yellow-400" },
  "agent.dream": { icon: "💤", color: "text-indigo-300" },
  "agent.error": { icon: "✖", color: "text-red-400" },
  "task.assigned": { icon: "♫", color: "text-yellow-300" },
  "task.completed": { icon: "★", color: "text-green-400" },
  "file.created": { icon: "♪", color: "text-cyan-400" },
  "world.event": { icon: "~*~", color: "text-fuchsia-400" },
  "world.clock": { icon: "🕐", color: "text-white/40" },
  "guild.formed": { icon: "♥♥", color: "text-cyan-300" },
  "campfire.complete": { icon: "🔥", color: "text-amber-400" },
  "fortune.given": { icon: "🥠", color: "text-yellow-300" },
  "boss.appeared": { icon: "☠", color: "text-red-500 font-bold" },
  "boss.defeated": { icon: "★★★", color: "text-green-400 font-bold" },
  "boss.damage": { icon: "⚔", color: "text-red-400" },
  "boss.escaped": { icon: "💨", color: "text-white/50" },
  "ghost.appears": { icon: "👻", color: "text-white/40 italic" },
  "swarm.round": { icon: "→", color: "text-white/20" },
  "director.plan_ready": { icon: "♥", color: "text-yellow-300" },
  "project.completed": { icon: "★★★", color: "text-green-400 font-bold" },
};

function formatEvent(entry: EventLogEntry): { icon: string; color: string; text: string } {
  const style = EVENT_STYLES[entry.event] || { icon: "•", color: "text-white/40" };
  const d = entry.data;

  switch (entry.event) {
    case "agent.spawned":
      return { ...style, text: `New agent: ${d.emoji} ${d.name} (${d.role})` };
    case "agent.speech":
      return { ...style, text: `${d.agent}: ${d.text}` };
    case "agent.level_up":
      return { ...style, text: `${d.agent} LEVELED UP to Lv${d.level}!` };
    case "agent.dream":
      return { icon: d.dream_type === "nightmare" ? "👻" : "💤", color: style.color, text: `${d.agent} dreams: ${d.dream}` };
    case "task.completed":
      return { ...style, text: `${d.agent} done: ${d.title}` };
    case "file.created":
      return { ...style, text: `${d.agent} → ${d.path}` };
    case "world.event":
      return { ...style, text: `WORLD EVENT: ${d.title}` };
    case "guild.formed":
      return { ...style, text: `Guild formed: ${d.name} (${(d.members as string[])?.join(", ")})` };
    case "campfire.complete":
      return { ...style, text: `Campfire! ${d.stories} stories shared~` };
    case "fortune.given":
      return { ...style, text: `${d.agent}: ${d.fortune}` };
    case "boss.appeared":
      return { ...style, text: `BOSS: ${d.name} (Lv${d.level}, ${d.hp}HP)` };
    case "boss.defeated":
      return { ...style, text: `BOSS DEFEATED: ${d.name}! +${d.xp_reward}XP!` };
    case "boss.damage":
      return { ...style, text: `${d.message}` };
    case "ghost.appears":
      return { ...style, text: `${d.message}` };
    case "swarm.round":
      return { ...style, text: `Round ${d.round}` };
    default:
      return { ...style, text: `${entry.event}: ${JSON.stringify(d).slice(0, 60)}` };
  }
}

interface Props {
  events: EventLogEntry[];
}

export default function EventLog({ events }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events.length]);

  // Filter out noisy clock events
  const filtered = events.filter((e) => e.event !== "world.clock");

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-green-500/20 bg-slate-900/50 p-3">
      <h3 className="text-xs font-bold text-green-300 font-mono">♪ Activity ♪</h3>

      <div ref={scrollRef} className="flex flex-col gap-0.5 max-h-52 overflow-y-auto scrollbar-thin">
        {filtered.length === 0 ? (
          <p className="text-xs text-white/30 font-mono">(^_^) Waiting for activity...</p>
        ) : (
          filtered.slice(-50).map((entry) => {
            const { icon, color, text } = formatEvent(entry);
            return (
              <div key={entry.id} className={`flex items-start gap-1.5 text-[11px] font-mono ${color}`}>
                <span className="shrink-0">{icon}</span>
                <span className="break-words">{text}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

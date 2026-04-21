"use client";

import { memo, useEffect, useMemo, useRef } from "react";
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
  "world.event": { icon: "~*~", color: "text-violet-400" },
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
      return { ...style, text: `New agent: ${d.emoji ?? "🤖"} ${d.name ?? "unknown"} (${d.role ?? "agent"})` };
    case "agent.speech":
      return { ...style, text: `${d.agent ?? "?"}: ${d.text ?? ""}` };
    case "agent.level_up":
      return { ...style, text: `${d.agent ?? "?"} LEVELED UP to Lv${d.level ?? "?"}!` };
    case "agent.dream":
      return { icon: d.dream_type === "nightmare" ? "👻" : "💤", color: style.color, text: `${d.agent ?? "?"} dreams: ${d.dream ?? ""}` };
    case "task.completed":
      return { ...style, text: `${d.agent ?? "?"} done: ${d.title ?? ""}` };
    case "file.created":
      return { ...style, text: `${d.agent ?? "?"} → ${d.path ?? ""}` };
    case "world.event":
      return { ...style, text: `WORLD EVENT: ${d.title ?? ""}` };
    case "guild.formed":
      return { ...style, text: `Guild formed: ${d.name ?? "?"} (${(d.members as string[] | undefined)?.join(", ") ?? ""})` };
    case "campfire.complete":
      return { ...style, text: `Campfire! ${d.stories ?? 0} stories shared~` };
    case "fortune.given":
      return { ...style, text: `${d.agent ?? "?"}: ${d.fortune ?? ""}` };
    case "boss.appeared":
      return { ...style, text: `BOSS: ${d.name ?? "???"} (Lv${d.level ?? "?"}, ${d.hp ?? "?"}HP)` };
    case "boss.defeated":
      return { ...style, text: `BOSS DEFEATED: ${d.name ?? "???"}! +${d.xp_reward ?? 0}XP!` };
    case "boss.damage":
      return { ...style, text: `${d.message ?? ""}` };
    case "ghost.appears":
      return { ...style, text: `${d.message ?? ""}` };
    case "swarm.round":
      return { ...style, text: `Round ${d.round ?? 0}` };
    default:
      return { ...style, text: `${entry.event}: ${JSON.stringify(d).slice(0, 60)}` };
  }
}

interface Props {
  events: EventLogEntry[];
}

const MAX_VISIBLE = 50;

function EventLogImpl({ events }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Only the last MAX_VISIBLE non-clock entries are ever rendered, so keep
  // the filter/slice memoised on identity of `events` — otherwise every
  // unrelated parent re-render walked the full (potentially thousands)
  // event list twice.
  const visible = useMemo(() => {
    const filtered: EventLogEntry[] = [];
    for (let i = events.length - 1; i >= 0 && filtered.length < MAX_VISIBLE; i--) {
      const ev = events[i];
      if (ev.event !== "world.clock") filtered.push(ev);
    }
    filtered.reverse();
    return filtered;
  }, [events]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [visible.length]);

  return (
    <div className="flex flex-col gap-2 rounded-xl p-3 h-full" style={{ border: "1px solid rgba(255,255,255,0.06)", background: "rgba(12,11,29,0.7)" }}>
      <h3 className="text-[10px] font-bold font-mono tracking-widest uppercase" style={{ color: "#a78bfa" }}>◈ Activity</h3>

      <div
        ref={scrollRef}
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        className="flex flex-col gap-0.5 max-h-52 overflow-y-auto scrollbar-thin"
      >
        {visible.length === 0 ? (
          <p className="text-xs text-white/30 font-mono">(^_^) Waiting for activity...</p>
        ) : (
          visible.map((entry) => {
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

const EventLog = memo(EventLogImpl);
export default EventLog;

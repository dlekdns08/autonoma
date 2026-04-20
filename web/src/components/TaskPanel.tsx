"use client";

import type { TaskData } from "@/lib/types";

const STATUS_STYLES: Record<string, { bg: string; text: string; icon: string }> = {
  open: { bg: "bg-white/5", text: "text-white/40", icon: "☆" },
  assigned: { bg: "bg-yellow-500/10", text: "text-yellow-400", icon: "♫" },
  in_progress: { bg: "bg-cyan-500/10", text: "text-cyan-400", icon: "♪" },
  review: { bg: "bg-violet-500/10", text: "text-violet-400", icon: "♦" },
  done: { bg: "bg-green-500/10", text: "text-green-400", icon: "★" },
  blocked: { bg: "bg-red-500/10", text: "text-red-400", icon: "✖" },
};

interface Props {
  tasks: TaskData[];
}

export default function TaskPanel({ tasks }: Props) {
  const done = tasks.filter((t) => t.status === "done").length;
  const total = tasks.length;
  const pct = total > 0 ? (done / total) * 100 : 0;

  return (
    <div className="flex flex-col gap-2 rounded-xl p-3" style={{ border: "1px solid rgba(255,255,255,0.07)", background: "rgba(12,11,29,0.7)" }}>
      <h3 className="text-[10px] font-bold font-mono tracking-widest uppercase" style={{ color: "#a78bfa" }}>◈ Tasks</h3>

      {tasks.length === 0 ? (
        <p className="text-xs text-white/30 font-mono">(?.?) No tasks yet...</p>
      ) : (
        <>
          <div className="flex flex-col gap-1 max-h-48 overflow-y-auto">
            {tasks.map((task) => {
              const style = STATUS_STYLES[task.status] || STATUS_STYLES.open;
              return (
                <div key={task.id || task.title} className={`flex items-center gap-2 rounded-lg px-2 py-1.5 ${style.bg}`}>
                  <span className={`text-xs ${style.text}`}>{style.icon}</span>
                  <span className={`flex-1 truncate text-xs ${style.text}`}>{task.title}</span>
                  {task.assigned_to && (
                    <span className="text-[10px] text-white/30">{task.assigned_to}</span>
                  )}
                </div>
              );
            })}
          </div>

          {/* Progress Bar */}
          <div className="mt-1">
            <div className="flex items-center gap-2">
              <div className="h-2 flex-1 rounded-full bg-white/10 overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-yellow-500 to-green-500 transition-all duration-500"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="text-[10px] font-mono text-white/50">
                {done}/{total} ({pct.toFixed(0)}%)
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

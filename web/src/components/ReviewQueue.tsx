"use client";

import { useState } from "react";
import type { TaskData } from "@/lib/types";

interface Props {
  tasks: TaskData[];
  currentRound?: number;
}

// We track when tasks entered review by using a module-level map keyed by
// task id. This persists across renders but resets on page reload, which
// is acceptable — we just want an approximate "rounds waiting" count.
const reviewEntryRound: Record<string, number> = {};

export default function ReviewQueue({ tasks, currentRound = 0 }: Props) {
  const [collapsed, setCollapsed] = useState(false);

  // Filter tasks in "review" status or assigned to a reviewer role.
  const reviewTasks = tasks.filter(
    (t) => t.status === "review" || (t.status === "assigned" && t.assigned_to?.toLowerCase().includes("review")),
  );

  // Track entry rounds
  for (const t of reviewTasks) {
    if (reviewEntryRound[t.id ?? t.title] === undefined) {
      reviewEntryRound[t.id ?? t.title] = currentRound;
    }
  }

  const count = reviewTasks.length;

  return (
    <div
      className="flex flex-col rounded-xl"
      style={{ border: "1px solid rgba(139,92,246,0.15)", background: "rgba(12,11,29,0.7)" }}
    >
      {/* Header */}
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="flex items-center gap-2 px-3 py-2 w-full text-left"
        style={{ borderBottom: collapsed ? "none" : "1px solid rgba(255,255,255,0.05)" }}
      >
        <span className="text-violet-400 text-[11px]">◈</span>
        <h3 className="text-[11px] font-bold text-violet-300 font-mono tracking-widest uppercase flex-1">
          Reviews ({count})
        </h3>
        {count > 0 && (
          <span
            className="rounded-full px-1.5 py-0.5 text-[8px] font-mono"
            style={{
              background: "rgba(139,92,246,0.2)",
              color: "#a78bfa",
              border: "1px solid rgba(139,92,246,0.3)",
            }}
          >
            {count}
          </span>
        )}
        <span className="text-white/30 text-[10px] ml-1">{collapsed ? "▶" : "▼"}</span>
      </button>

      {!collapsed && (
        <div className="flex flex-col gap-1 p-2 max-h-48 overflow-y-auto scrollbar-thin">
          {reviewTasks.length === 0 ? (
            <p className="text-xs text-white/30 font-mono px-1">(^_^) No pending reviews</p>
          ) : (
            reviewTasks.map((task) => {
              const key = task.id ?? task.title;
              const entryRound = reviewEntryRound[key] ?? currentRound;
              const waitingRounds = currentRound - entryRound;
              const isOverdue = waitingRounds > 3;

              return (
                <div
                  key={key}
                  className="flex items-start gap-2 rounded-lg px-2 py-1.5"
                  style={{
                    background: isOverdue
                      ? "rgba(239,68,68,0.12)"
                      : "rgba(139,92,246,0.08)",
                    border: isOverdue
                      ? "1px solid rgba(239,68,68,0.25)"
                      : "1px solid rgba(139,92,246,0.12)",
                  }}
                >
                  <span className="text-xs mt-0.5" style={{ color: isOverdue ? "#fca5a5" : "#c4b5fd" }}>
                    {isOverdue ? "!" : "♦"}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div
                      className="text-xs truncate font-mono"
                      style={{ color: isOverdue ? "#fca5a5" : "#c4b5fd" }}
                    >
                      {task.title}
                    </div>
                    <div className="flex items-center gap-2 mt-0.5">
                      {task.assigned_to && (
                        <span className="text-[9px] text-white/40 font-mono">{task.assigned_to}</span>
                      )}
                      {waitingRounds > 0 && (
                        <span
                          className="text-[9px] font-mono"
                          style={{ color: isOverdue ? "#fb7185" : "#9d8ec4" }}
                        >
                          {waitingRounds}r waiting{isOverdue ? " (overdue)" : ""}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

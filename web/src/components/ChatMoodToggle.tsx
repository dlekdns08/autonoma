"use client";

/**
 * "Chat mood" toggle — opt-in, browser-side sentiment on viewer chat.
 * Shared by the dashboard ChatPanel header and the /watch kiosk header
 * so the control (and its localStorage-backed state) looks the same
 * everywhere. Nothing downloads until this is switched on.
 */

import type { ChatMoodControl } from "@/hooks/useChatSentiment";

export default function ChatMoodToggle({
  control,
  className = "",
}: {
  control: ChatMoodControl;
  className?: string;
}) {
  const { enabled, setEnabled, status, progress, error } = control;
  const dot =
    status === "ready"
      ? "bg-emerald-400"
      : status === "loading"
        ? "animate-pulse bg-amber-400"
        : status === "error"
          ? "bg-rose-500"
          : "bg-white/25";
  const detail =
    status === "loading"
      ? progress > 0
        ? `${Math.round(progress)}%`
        : "loading"
      : status === "error"
        ? "error"
        : status === "ready"
          ? "on"
          : "off";
  return (
    <button
      type="button"
      onClick={() => setEnabled(!enabled)}
      aria-pressed={enabled}
      title={
        error
          ? `Chat mood failed: ${error}`
          : "Classify viewer chat in your browser (small on-device model, downloaded only when enabled) and let the addressed agent react"
      }
      className={`flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-[10px] transition-colors ${
        enabled
          ? "border-fuchsia-400/50 bg-fuchsia-500/15 text-fuchsia-100"
          : "border-white/10 bg-white/5 text-white/55 hover:text-white"
      } ${className}`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden="true" />
      <span>chat mood</span>
      <span className="text-white/45">{detail}</span>
    </button>
  );
}

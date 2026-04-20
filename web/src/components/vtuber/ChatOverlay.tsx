"use client";

/**
 * Floating spectator-chat overlay for the OBS view.
 *
 *   <ChatOverlay messages={chat} />
 *
 * Mirrors the Twitch/YouTube stream-chat aesthetic: a vertical stack of
 * pill-shaped messages hugging the corner, newest at the bottom, each
 * fading out after a TTL so the overlay never fills the screen. Lives
 * outside the spotlight Canvas so `canvas.captureStream()` recordings
 * don't include it — users who want chat baked into the clip should
 * record via OBS itself or a screen recorder instead.
 */

import { useEffect, useState } from "react";
import type { ChatMessage } from "@/lib/types";

interface Props {
  messages: ChatMessage[];
  /** Latest N kept visible. Defaults to 8 — stream overlays rarely want
   *  more, and more than that starts competing with the character for
   *  attention. */
  maxVisible?: number;
  /** How long a message remains visible after it arrives, in ms.
   *  Defaults to 15s, tuned to match Twitch's default auto-hide. */
  ttlMs?: number;
  /** Which corner the stack hugs. */
  position?: "left" | "right";
}

export default function ChatOverlay({
  messages,
  maxVisible = 8,
  ttlMs = 15000,
  position = "left",
}: Props) {
  // A single 1Hz tick re-filters the visible window. Cheaper and easier
  // to reason about than one setTimeout per message; the UI doesn't
  // need sub-second precision for "is this message too old to show".
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const visible = messages
    .slice(-maxVisible)
    .filter((m) => now - m.timestamp < ttlMs);

  const sideClass = position === "right" ? "right-3" : "left-3";

  return (
    <div
      className={`pointer-events-none absolute bottom-24 ${sideClass} flex max-w-[340px] flex-col gap-1.5`}
    >
      {visible.map((m) => (
        <div
          key={m.id}
          className="animate-[chat-in_220ms_ease-out] rounded-lg bg-black/80 px-3 py-1.5 font-mono text-xs leading-snug text-white shadow-[0_4px_16px_rgba(0,0,0,0.5)] backdrop-blur-sm"
        >
          <span
            className="mr-1.5 font-bold"
            style={{ color: colorFor(m.from) }}
          >
            {m.from}
            {m.isOwner ? " ♚" : ""}
          </span>
          {m.text}
        </div>
      ))}
      <style jsx>{`
        @keyframes chat-in {
          from {
            opacity: 0;
            transform: translateX(${position === "right" ? "8px" : "-8px"});
          }
          to {
            opacity: 1;
            transform: translateX(0);
          }
        }
      `}</style>
    </div>
  );
}

// Deterministic pastel color per username — Twitch does the same trick
// so repeat chatters always appear in the same hue and regulars get
// recognizable at a glance. djb2 → HSL with fixed S/L so every color
// reads well on the dark chat pill.
function colorFor(name: string): string {
  let h = 5381;
  for (let i = 0; i < name.length; i++) {
    h = ((h << 5) + h + name.charCodeAt(i)) >>> 0;
  }
  return `hsl(${h % 360} 80% 72%)`;
}

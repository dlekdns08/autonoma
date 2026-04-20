"use client";

/**
 * Spectator chat for the multi-viewer phase.
 *
 * Lives in its own column so the host's own controls (start / stop)
 * stay visually separate from "what other viewers are saying" — the
 * social feed should never be mistaken for an authoritative log.
 *
 * Auto-scrolls only when the user is already pinned to the bottom; if
 * they've scrolled up to read older messages, we leave them there and
 * surface a small "↓" badge instead.
 */

import { useEffect, useRef, useState } from "react";
import type { ChatMessage, RoomState } from "@/lib/types";

interface Props {
  room: RoomState;
  messages: ChatMessage[];
  onSend: (text: string) => void;
  onSetName: (name: string) => void;
  onJoinRoom: (code: string) => void;
}

export default function ChatPanel({
  room,
  messages,
  onSend,
  onSetName,
  onJoinRoom,
}: Props) {
  const [draft, setDraft] = useState("");
  const [name, setName] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !pinnedRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 16;
  };

  const submit = () => {
    if (!draft.trim()) return;
    onSend(draft);
    setDraft("");
  };

  return (
    <div className="flex h-full flex-col gap-2 rounded-lg border border-cyan-500/20 bg-black/40 p-2 text-xs">
      <div className="flex items-center justify-between">
        <div className="font-mono text-cyan-300">
          {room.code ? (
            <>
              ROOM <span className="font-bold">{room.code}</span>
            </>
          ) : (
            <span className="text-white/40">no room yet</span>
          )}
        </div>
        <div className="text-white/60">
          {room.viewerCount} viewer{room.viewerCount === 1 ? "" : "s"}
        </div>
      </div>

      {!room.isOwner && (
        <div className="rounded bg-amber-500/10 px-2 py-1 text-[10px] text-amber-200">
          Watching as a spectator. Only the host can drive the swarm.
        </div>
      )}

      {room.isOwner && room.code && (
        <div className="text-[10px] text-white/50">
          Share this URL to let friends watch live:{" "}
          <code className="text-cyan-300">?room={room.code}</code>
        </div>
      )}

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 min-h-0 overflow-y-auto rounded bg-black/30 p-1.5 font-mono"
      >
        {messages.length === 0 ? (
          <div className="text-white/30">No chat yet — say hi.</div>
        ) : (
          messages.map((m) => (
            <div key={m.id} className="leading-5">
              <span
                className={`mr-1 ${m.isOwner ? "text-amber-300" : "text-cyan-300"}`}
              >
                {m.from}:
              </span>
              <span className="text-white/90 whitespace-pre-wrap">{m.text}</span>
            </div>
          ))
        )}
      </div>

      <div className="flex gap-1">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Say something to the room..."
          maxLength={280}
          className="flex-1 rounded bg-black/50 px-2 py-1 font-mono text-white placeholder-white/30 outline-none ring-1 ring-cyan-500/30 focus:ring-cyan-400/60"
        />
        <button
          onClick={submit}
          className="rounded bg-cyan-500/30 px-2 py-1 text-cyan-100 hover:bg-cyan-500/50"
        >
          send
        </button>
      </div>

      <div className="grid grid-cols-2 gap-1 border-t border-white/10 pt-1.5">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() && onSetName(name.trim())}
          placeholder="display name"
          maxLength={24}
          className="rounded bg-black/40 px-1.5 py-0.5 text-[10px] text-white outline-none ring-1 ring-white/10 focus:ring-cyan-400/40"
        />
        <div className="flex gap-1">
          <input
            value={joinCode}
            onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
            placeholder="join code"
            maxLength={8}
            className="flex-1 rounded bg-black/40 px-1.5 py-0.5 text-[10px] uppercase text-white outline-none ring-1 ring-white/10 focus:ring-cyan-400/40"
          />
          <button
            onClick={() => joinCode.trim() && onJoinRoom(joinCode)}
            disabled={!joinCode.trim()}
            className="rounded bg-fuchsia-500/30 px-1.5 text-[10px] text-fuchsia-100 hover:bg-fuchsia-500/50 disabled:opacity-30"
          >
            join
          </button>
        </div>
      </div>
    </div>
  );
}

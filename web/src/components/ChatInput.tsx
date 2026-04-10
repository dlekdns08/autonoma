"use client";

import { useCallback, useRef, useState } from "react";

interface Props {
  onSend: (message: string) => void;
  connected: boolean;
}

const COMMANDS = [
  { cmd: "/status", desc: "Show swarm status" },
  { cmd: "/agents", desc: "List all agents" },
  { cmd: "/tasks", desc: "Show task overview" },
  { cmd: "/cheer", desc: "Cheer for the agents!" },
  { cmd: "/snapshot", desc: "Request state snapshot" },
];

export default function ChatInput({ onSend, connected }: Props) {
  const [input, setInput] = useState("");
  const [showCommands, setShowCommands] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSend = useCallback(() => {
    const msg = input.trim();
    if (!msg) return;

    onSend(msg);
    setHistory((prev) => [msg, ...prev].slice(0, 20));
    setHistoryIdx(-1);
    setInput("");
    setShowCommands(false);
  }, [input, onSend]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSend();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (history.length > 0) {
          const next = Math.min(historyIdx + 1, history.length - 1);
          setHistoryIdx(next);
          setInput(history[next]);
        }
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        if (historyIdx > 0) {
          const next = historyIdx - 1;
          setHistoryIdx(next);
          setInput(history[next]);
        } else {
          setHistoryIdx(-1);
          setInput("");
        }
      } else if (e.key === "Escape") {
        setShowCommands(false);
      }
    },
    [handleSend, history, historyIdx],
  );

  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setInput(val);
    setShowCommands(val.startsWith("/"));
  }, []);

  const filteredCmds = COMMANDS.filter((c) => c.cmd.startsWith(input.toLowerCase()));

  return (
    <div className="relative border-t border-white/5 bg-slate-950/80 backdrop-blur-sm">
      {/* Command autocomplete */}
      {showCommands && filteredCmds.length > 0 && (
        <div className="absolute bottom-full left-0 right-0 border border-white/10 bg-slate-900/95 backdrop-blur-sm rounded-t-lg overflow-hidden">
          {filteredCmds.map((cmd) => (
            <button
              key={cmd.cmd}
              className="flex items-center gap-3 w-full px-4 py-2 text-left hover:bg-white/5 transition-colors"
              onClick={() => {
                setInput(cmd.cmd);
                setShowCommands(false);
                inputRef.current?.focus();
              }}
            >
              <span className="text-xs font-mono text-cyan-400">{cmd.cmd}</span>
              <span className="text-[10px] text-white/30">{cmd.desc}</span>
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 px-4 py-2">
        <span className={`text-xs font-mono ${connected ? "text-green-400" : "text-red-400"}`}>
          {connected ? "▶" : "■"}
        </span>
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={connected ? "Type a command or message... (/ for commands)" : "Not connected..."}
          disabled={!connected}
          className="flex-1 bg-transparent text-sm text-white/80 font-mono placeholder:text-white/20 outline-none disabled:opacity-50"
        />
        <button
          onClick={handleSend}
          disabled={!connected || !input.trim()}
          className="rounded-lg bg-purple-500/20 px-3 py-1 text-xs font-mono text-purple-300 hover:bg-purple-500/30 transition-colors disabled:opacity-30"
        >
          Send
        </button>
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";

const FACES = ["(^_^)", "(o.o)", "(-_-)", "(>.<)", "(*^*)"];
const SPARKLES = ["✦", "✧", "★", "☆", "♥", "♡", "♪"];

interface Props {
  connected: boolean;
  onStart: (goal: string) => void;
}

export default function IdleScreen({ connected, onStart }: Props) {
  const [frame, setFrame] = useState(0);
  const [goal, setGoal] = useState("");
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    const t = setInterval(() => setFrame((f) => f + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const handleStart = useCallback(() => {
    const trimmed = goal.trim();
    if (!trimmed || !connected) return;
    setStarting(true);
    onStart(trimmed);
  }, [goal, connected, onStart]);

  const face = FACES[frame % FACES.length];
  const sparkle = SPARKLES[frame % SPARKLES.length];

  return (
    <div className="flex flex-col items-center justify-center h-full gap-8">
      <div className="text-center">
        <h1 className="text-5xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 to-cyan-400 font-mono">
          ~* Autonoma *~
        </h1>
        <p className="mt-3 text-white/40 text-sm font-mono">
          Self-Organizing Agent Swarm
        </p>
      </div>

      <div className="text-6xl font-mono text-fuchsia-300 animate-bounce">{face}</div>

      {/* Goal Input */}
      <div className="w-full max-w-lg px-4">
        <div className="flex gap-2">
          <input
            type="text"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleStart()}
            placeholder="What should the swarm build?"
            disabled={!connected || starting}
            className="flex-1 rounded-xl border border-fuchsia-500/30 bg-slate-900/80 px-4 py-3 text-sm text-white font-mono placeholder:text-white/20 outline-none focus:border-fuchsia-500/60 transition-colors disabled:opacity-50"
          />
          <button
            onClick={handleStart}
            disabled={!connected || !goal.trim() || starting}
            className="rounded-xl bg-gradient-to-r from-fuchsia-600 to-cyan-600 px-6 py-3 text-sm font-bold font-mono text-white hover:from-fuchsia-500 hover:to-cyan-500 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {starting ? "Starting..." : "Build!"}
          </button>
        </div>
      </div>

      <div className="flex flex-col items-center gap-3 text-sm text-white/50 font-mono">
        <p>
          {sparkle} Enter a goal and let the agents work {sparkle}
        </p>
        <div className="flex items-center gap-2 mt-2">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-green-400 animate-pulse" : "bg-red-500"}`} />
          <span className="text-xs">{connected ? "WebSocket connected" : "Connecting..."}</span>
        </div>
      </div>
    </div>
  );
}

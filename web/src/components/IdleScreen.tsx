"use client";

import { useEffect, useState } from "react";

const FACES = ["(^_^)", "(o.o)", "(-_-)", "(>.<)", "(*^*)"];
const SPARKLES = ["✦", "✧", "★", "☆", "♥", "♡", "♪"];

export default function IdleScreen({ connected }: { connected: boolean }) {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    const t = setInterval(() => setFrame((f) => f + 1), 1000);
    return () => clearInterval(t);
  }, []);

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

      <div className="flex flex-col items-center gap-3 text-sm text-white/50 font-mono">
        <p>
          {sparkle} Waiting for swarm to start... {sparkle}
        </p>
        <p className="text-xs text-white/30">
          Run <code className="rounded bg-white/10 px-1.5 py-0.5 text-cyan-300">uv run autonoma build &quot;your goal&quot;</code> to begin
        </p>
        <div className="flex items-center gap-2 mt-2">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-green-400 animate-pulse" : "bg-red-500"}`} />
          <span className="text-xs">{connected ? "WebSocket connected" : "Connecting..."}</span>
        </div>
      </div>
    </div>
  );
}

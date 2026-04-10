"use client";

import { useEffect, useState } from "react";

const TITLES = ["~* Autonoma *~", "~♪ Autonoma ♪~", "~★ Autonoma ★~", "~♥ Autonoma ♥~"];

interface Props {
  projectName: string;
  round: number;
  maxRounds: number;
  sky: string;
  connected: boolean;
}

export default function Header({ projectName, round, maxRounds, sky, connected }: Props) {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setFrame((f) => f + 1), 800);
    return () => clearInterval(t);
  }, []);

  const title = TITLES[frame % TITLES.length];

  return (
    <header className="border-b-2 border-fuchsia-500/30 bg-gradient-to-r from-fuchsia-950/40 via-purple-950/40 to-fuchsia-950/40 px-4 py-3 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-xl font-bold text-fuchsia-300 tracking-wide font-mono">
            {title}
          </span>
          <span className="text-sm text-white/60">Self-Organizing Agent Swarm</span>
          {projectName && (
            <span className="rounded-full bg-cyan-500/20 px-3 py-0.5 text-xs text-cyan-300">
              {projectName}
            </span>
          )}
        </div>
        <div className="flex items-center gap-4 text-sm">
          {sky && <span className="text-white/50 font-mono text-xs">{sky}</span>}
          {round > 0 && (
            <span className="text-yellow-300 font-mono">
              ★ Round {round}/{maxRounds} ★
            </span>
          )}
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-green-400 animate-pulse" : "bg-red-500"}`} />
        </div>
      </div>
    </header>
  );
}

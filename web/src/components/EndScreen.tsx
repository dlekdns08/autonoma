"use client";

import { useState } from "react";

interface Props {
  epilogue: string;
  leaderboard: string;
  multiverse: string;
  graveyard: string;
}

type Tab = "epilogue" | "leaderboard" | "multiverse" | "graveyard";

export default function EndScreen({ epilogue, leaderboard, multiverse, graveyard }: Props) {
  const [tab, setTab] = useState<Tab>("epilogue");

  const allTabs: { id: Tab; label: string; icon: string; content: string }[] = [
    { id: "epilogue", label: "Story", icon: "★", content: epilogue },
    { id: "leaderboard", label: "Rankings", icon: "👑", content: leaderboard },
    { id: "multiverse", label: "What If", icon: "🌀", content: multiverse },
    { id: "graveyard", label: "Graveyard", icon: "👻", content: graveyard },
  ];
  const tabs = allTabs.filter((t) => t.content && !t.content.includes("No "));

  const activeContent = tabs.find((t) => t.id === tab)?.content || epilogue;

  return (
    <div className="flex flex-col items-center justify-center h-full gap-6 p-8">
      {/* Celebration Header */}
      <div className="text-center animate-fade-in">
        <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 via-cyan-400 to-yellow-400 font-mono">
          ~*~ PROJECT COMPLETE! ~*~
        </h1>
        <p className="mt-2 text-white/50 text-sm">The adventure has ended... for now.</p>
      </div>

      {/* Tab Selector */}
      {tabs.length > 1 && (
        <div className="flex gap-2">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`rounded-lg px-4 py-2 text-xs font-mono transition-all ${
                tab === t.id
                  ? "bg-fuchsia-500/30 text-fuchsia-300 border border-fuchsia-500/50"
                  : "bg-white/5 text-white/40 hover:bg-white/10 border border-transparent"
              }`}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      <div className="max-w-2xl w-full rounded-xl border border-fuchsia-500/20 bg-slate-900/80 p-6 backdrop-blur-sm">
        <pre className="whitespace-pre-wrap text-xs font-mono text-white/70 leading-relaxed">
          {activeContent}
        </pre>
      </div>
    </div>
  );
}

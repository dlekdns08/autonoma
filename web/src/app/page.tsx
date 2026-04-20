"use client";

import { useCallback, useState } from "react";
import { useSwarm } from "@/hooks/useSwarm";
import Header from "@/components/Header";
import Stage from "@/components/Stage";
import TaskPanel from "@/components/TaskPanel";
import FileTree from "@/components/FileTree";
import EventLog from "@/components/EventLog";
import BossOverlay from "@/components/BossOverlay";
import EndScreen from "@/components/EndScreen";
import IdleScreen from "@/components/IdleScreen";
import AuthModal from "@/components/AuthModal";
import ToastContainer from "@/components/Toast";
import AgentModal from "@/components/AgentModal";
import RelationshipWeb from "@/components/RelationshipWeb";
import ChatInput from "@/components/ChatInput";
import Starfield from "@/components/Starfield";
import Minimap from "@/components/Minimap";
import type { AgentData } from "@/lib/types";

export default function Home() {
  const {
    state, connected, toasts, dismissToast,
    sendMessage, sendToAgent, startSwarm, resetSwarm, collectCookie,
    authState, authenticate, logout,
  } = useSwarm();
  const [selectedAgent, setSelectedAgent] = useState<AgentData | null>(null);

  const needsAuth = authState.status !== "authenticated";

  const handleSelectAgent = useCallback(
    (name: string) => {
      const agent = state.agents.find((a) => a.name === name);
      if (agent) setSelectedAgent(agent);
    },
    [state.agents],
  );

  // Idle state
  if (state.status === "idle") {
    return (
      <div className="flex h-screen flex-col relative">
        <Starfield intensity={0.3} />
        <Header projectName="" round={0} maxRounds={0} sky="" connected={connected} />
        <main className="flex-1 relative z-10">
          <IdleScreen connected={connected && !needsAuth} onStart={startSwarm} />
        </main>
        <ToastContainer toasts={toasts} onDismiss={dismissToast} />
        {needsAuth && authState.status !== "unknown" && (
          <AuthModal authState={authState} onAuthenticate={authenticate} />
        )}
      </div>
    );
  }

  // Finished state
  if (state.status === "finished") {
    return (
      <div className="flex h-screen flex-col relative">
        <Starfield intensity={0.8} sky="night" />
        <Header
          projectName={state.project_name}
          round={state.round}
          maxRounds={state.max_rounds}
          sky={state.sky}
          connected={connected}
        />
        <main className="flex-1 overflow-hidden relative z-10">
          <EndScreen
            finalAnswer={state.final_answer}
            epilogue={state.epilogue}
            leaderboard={state.leaderboard}
            multiverse={state.multiverse}
            graveyard={state.graveyard}
            files={state.files}
            projectName={state.project_name}
            onReset={resetSwarm}
          />
        </main>
        <ToastContainer toasts={toasts} onDismiss={dismissToast} />
        {needsAuth && authState.status !== "unknown" && (
          <AuthModal authState={authState} onAuthenticate={authenticate} />
        )}
      </div>
    );
  }

  // Running state — full dashboard
  return (
    <div className="flex h-screen flex-col relative">
      {/* Animated starfield background */}
      <Starfield intensity={state.boss ? 0.9 : 0.5} sky={state.sky} />

      <Header
        projectName={state.project_name}
        round={state.round}
        maxRounds={state.max_rounds}
        sky={state.sky}
        connected={connected}
      />

      <main className="flex flex-1 overflow-hidden relative z-10">
        {/* Left: Stage + Events */}
        <div className="flex flex-1 flex-col gap-2 p-2">
          <div className="relative flex-[3] min-h-0">
            <Stage
              agents={state.agents}
              sky={state.sky}
              boss={state.boss}
              cookies={state.cookies}
              onSelectAgent={handleSelectAgent}
              onCookieCollected={collectCookie}
            />
            {state.boss && <BossOverlay boss={state.boss} />}

            {/* Minimap overlay */}
            <div className="absolute bottom-2 right-2 w-32">
              <Minimap agents={state.agents} onSelectAgent={handleSelectAgent} />
            </div>
          </div>
          <div className="flex-[1] min-h-0">
            <EventLog events={state.events} />
          </div>
        </div>

        {/* Right: Sidebar */}
        <div className="flex w-80 flex-col gap-2 overflow-y-auto p-2 border-l border-white/5 scrollbar-thin">
          <TaskPanel tasks={state.tasks} />

          {/* Relationship Web */}
          <RelationshipWeb
            agents={state.agents}
            relationships={state.relationships}
            onSelectAgent={handleSelectAgent}
          />

          <FileTree files={state.files} />

          {/* Agent Cards */}
          <div className="flex flex-col gap-2 rounded-xl border border-purple-500/20 bg-slate-900/50 p-3">
            <h3 className="text-xs font-bold text-purple-300 font-mono">♥ Agents ♥</h3>
            <div className="flex flex-col gap-1.5 max-h-60 overflow-y-auto scrollbar-thin">
              {state.agents.map((agent) => (
                <AgentCard
                  key={agent.name}
                  agent={agent}
                  onClick={() => setSelectedAgent(agent)}
                />
              ))}
            </div>
          </div>
        </div>
      </main>

      {/* Chat Input */}
      <ChatInput onSend={sendMessage} connected={connected} />

      {/* Footer */}
      <footer className="flex items-center justify-between gap-4 border-t border-fuchsia-500/10 bg-slate-950/50 px-4 py-2 text-[10px] font-mono text-white/30 relative z-10">
        <div className="flex items-center gap-4">
          <span>♥ Autonoma v0.1.0</span>
          <span>Agents: {state.agents.length}</span>
          <span>Files: {state.files.length}</span>
          <span>Events: {state.events.length}</span>
          {state.boss && <span className="text-red-400">BOSS: {state.boss.name}</span>}
        </div>
        {authState.status === "authenticated" && (
          <div className="flex items-center gap-3">
            <span className="text-white/20">
              {authState.isAdmin ? "👑 admin" : `${authState.provider} / ${authState.model}`}
            </span>
            <button
              onClick={logout}
              className="text-white/20 hover:text-white/50 transition-colors underline underline-offset-2"
            >
              로그아웃
            </button>
          </div>
        )}
      </footer>

      {/* Toast Notifications */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* Agent Modal */}
      {selectedAgent && (
        <AgentModal
          agent={selectedAgent}
          onClose={() => setSelectedAgent(null)}
          onSend={sendToAgent}
        />
      )}

      {/* Auth Modal */}
      {needsAuth && authState.status !== "unknown" && (
        <AuthModal authState={authState} onAuthenticate={authenticate} />
      )}
    </div>
  );
}

function AgentCard({ agent, onClick }: { agent: AgentData; onClick: () => void }) {
  const xpPct = agent.xp_to_next > 0 ? (agent.xp / agent.xp_to_next) * 100 : 0;

  const moodColors: Record<string, string> = {
    happy: "bg-green-500/20 text-green-400",
    excited: "bg-yellow-500/20 text-yellow-400",
    frustrated: "bg-red-500/20 text-red-400",
    proud: "bg-fuchsia-500/20 text-fuchsia-400",
    worried: "bg-orange-500/20 text-orange-400",
    relaxed: "bg-cyan-500/20 text-cyan-400",
    determined: "bg-amber-500/20 text-amber-400",
    focused: "bg-blue-500/20 text-blue-400",
  };

  const moodStyle = moodColors[agent.mood] || "bg-white/10 text-white/50";

  return (
    <div
      className="rounded-lg bg-white/[0.03] p-2 border border-white/5 hover:border-purple-500/20 transition-colors cursor-pointer"
      onClick={onClick}
    >
      <div className="flex items-center gap-2">
        <span className="text-lg">{agent.species_emoji || agent.emoji}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-bold text-white/80 truncate">{agent.name}</span>
            <span className="text-[9px] text-yellow-400 font-mono">Lv{agent.level}</span>
          </div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-[9px] text-white/30">{agent.role}</span>
            {agent.rarity && agent.rarity !== "common" && (
              <span
                className={`text-[8px] rounded px-1 ${
                  agent.rarity === "legendary"
                    ? "bg-amber-500/20 text-amber-400"
                    : agent.rarity === "rare"
                      ? "bg-purple-500/20 text-purple-400"
                      : "bg-cyan-500/20 text-cyan-400"
                }`}
              >
                {agent.rarity}
              </span>
            )}
          </div>
        </div>
        <span className={`rounded-full px-1.5 py-0.5 text-[8px] ${moodStyle}`}>{agent.mood}</span>
      </div>

      {/* XP Bar */}
      <div className="mt-1.5 flex items-center gap-1.5">
        <div className="h-1 flex-1 rounded-full bg-white/10 overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-fuchsia-500 transition-all duration-300"
            style={{ width: `${xpPct}%` }}
          />
        </div>
        <span className="text-[8px] text-white/30 font-mono">
          {agent.xp}/{agent.xp_to_next}
        </span>
      </div>

      {/* Traits */}
      {agent.traits && agent.traits.length > 0 && (
        <div className="mt-1 flex gap-1">
          {agent.traits.map((t) => (
            <span key={t} className="rounded bg-white/5 px-1 py-0.5 text-[7px] text-white/30">
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

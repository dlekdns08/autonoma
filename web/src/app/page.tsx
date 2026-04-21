"use client";

import { useCallback, useState } from "react";
import { useSwarm } from "@/hooks/useSwarm";
import { useAuth } from "@/hooks/useAuth";
import { useKeyNav } from "@/hooks/useKeyNav";
import Header from "@/components/Header";
import Stage from "@/components/Stage";
import TaskPanel from "@/components/TaskPanel";
import FileTree from "@/components/FileTree";
import EventLog from "@/components/EventLog";
import BossOverlay from "@/components/BossOverlay";
import EndScreen from "@/components/EndScreen";
import IdleScreen from "@/components/IdleScreen";
import AuthModal from "@/components/AuthModal";
import ModelSettingsModal from "@/components/ModelSettingsModal";
import ToastContainer from "@/components/Toast";
import AgentModal from "@/components/AgentModal";
import RelationshipWeb from "@/components/RelationshipWeb";
import ChatInput from "@/components/ChatInput";
import ChatPanel from "@/components/ChatPanel";
import Starfield from "@/components/Starfield";
import Minimap from "@/components/Minimap";
import VTuberStage from "@/components/vtuber/VTuberStage";
import KeyboardHelpModal from "@/components/KeyboardHelpModal";
import ReviewQueue from "@/components/ReviewQueue";
import ExecutionTimeline from "@/components/ExecutionTimeline";
import type { AgentData } from "@/lib/types";

// ── Top-level gate ─────────────────────────────────────────────────────
// The backend expects a logged-in user for everything meaningful, so the
// page is gated on the cookie session from `useAuth` before the swarm
// dashboard ever mounts. The dashboard component itself is unchanged —
// it still runs `useSwarm` and owns WebSocket lifecycle as before.

export default function Home() {
  const { user, loading, logout } = useAuth();

  // Initial hydrate — show a tiny spinner instead of flashing the login
  // modal while /api/auth/me is in flight.
  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/40">
        loading...
      </div>
    );
  }

  if (!user) {
    return (
      <div className="h-screen w-screen bg-[#0a0a12]">
        <AuthModal />
      </div>
    );
  }

  return <Dashboard />;
}

// Fixed top-right chip showing the current user + quick actions. Rendered
// inside <Dashboard> so the ⚙ button can reach the swarm's `authenticate`
// callback without threading it through context.
function UserChip({
  username,
  isAdmin,
  onLogout,
  onOpenSettings,
}: {
  username: string;
  isAdmin: boolean;
  onLogout: () => void;
  onOpenSettings?: () => void;
}) {
  return (
    <div
      className="fixed top-2 right-24 z-40 flex items-center gap-2 rounded-full border border-white/10 bg-slate-950/80 px-3 py-1 font-mono text-[10px] text-white/70 backdrop-blur-sm"
      style={{ boxShadow: "0 0 12px rgba(139,92,246,0.08)" }}
    >
      <span>
        👤 {username}
        {isAdmin && " 👑"}
      </span>
      {onOpenSettings && (
        <>
          <span className="text-white/20">|</span>
          <button
            type="button"
            onClick={onOpenSettings}
            className="text-cyan-300/70 hover:text-cyan-200 transition-colors"
            title="모델 설정"
            aria-label="모델 설정"
          >
            ⚙ 모델
          </button>
        </>
      )}
      {isAdmin && (
        <>
          <span className="text-white/20">|</span>
          <a
            href="/admin/users"
            className="text-amber-300/70 hover:text-amber-200 transition-colors underline underline-offset-2"
          >
            users
          </a>
        </>
      )}
      <span className="text-white/20">|</span>
      <button
        type="button"
        onClick={onLogout}
        className="text-white/50 hover:text-fuchsia-300 transition-colors underline underline-offset-2"
      >
        logout
      </button>
    </div>
  );
}

function Dashboard() {
  const {
    state, connected, connectionFailed, toasts, dismissToast,
    sendMessage, sendToAgent, startSwarm, resetSwarm, collectCookie,
    authState, authenticate, logout, sessionId,
    emotes, getMouthAmplitude,
    room, chat, sendChat, setDisplayName, joinRoom,
    speakingAgents,
    lastRunFieldPaths,
  } = useSwarm();
  const { user, logout: httpLogout } = useAuth();
  const [selectedAgent, setSelectedAgent] = useState<AgentData | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Pixel → VTuber transition state
  const [bloomingAgent, setBloomingAgent] = useState<string | null>(null);
  const [vtPinned, setVtPinned] = useState<string | null>(null);

  const [shareCopied, setShareCopied] = useState(false);

  const handleShare = useCallback(() => {
    const url = room.code
      ? `${window.location.origin}${window.location.pathname}?room=${room.code}`
      : window.location.href;
    void navigator.clipboard.writeText(url).then(() => {
      setShareCopied(true);
      setTimeout(() => setShareCopied(false), 2000);
    });
  }, [room.code]);

  // A viewer is in spectator mode when they joined via a shared URL (not owner)
  const isSpectator = !room.isOwner;

  // Panel visibility — toggled by keyboard shortcuts
  const [showTasks, setShowTasks] = useState(true);
  const [showFiles, setShowFiles] = useState(true);
  const [showChat, setShowChat] = useState(true);

  const { showHelp, setShowHelp } = useKeyNav({
    onToggleTasks: () => setShowTasks((v) => !v),
    onToggleFiles: () => setShowFiles((v) => !v),
    onToggleChat: () => setShowChat((v) => !v),
    isRunning: state.status === "running",
  });

  // Floating user chrome (chip + settings modal). Rendered inside every
  // Dashboard layout variant so the ⚙ action is reachable from idle,
  // running, and finished states alike.
  const userChrome = user ? (
    <>
      <UserChip
        username={user.username}
        isAdmin={user.role === "admin"}
        onLogout={() => void httpLogout()}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      {settingsOpen && (
        <ModelSettingsModal
          authState={authState}
          onAuthenticate={authenticate}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </>
  ) : null;

  const needsAuth = authState.status !== "authenticated";

  const handleSelectAgent = useCallback(
    (name: string) => {
      const agent = state.agents.find((a) => a.name === name);
      if (agent) setSelectedAgent(agent);
    },
    [state.agents],
  );

  // Clicking a pixel sprite → bloom dissolve → VTuber reveal
  const handlePixelClick = useCallback(
    (name: string) => {
      if (bloomingAgent) return; // already transitioning
      setBloomingAgent(name);
      const t = setTimeout(() => {
        setVtPinned(name);
        setBloomingAgent(null);
      }, 520);
      return () => clearTimeout(t);
    },
    [bloomingAgent],
  );

  // ── Idle ────────────────────────────────────────────────────────────
  if (state.status === "idle") {
    return (
      <div className="flex h-screen flex-col relative">
        <Starfield intensity={0.3} />
        <Header projectName="" round={0} maxRounds={0} sky="" connected={connected} />
        <main className="flex-1 relative z-10">
          <IdleScreen
            connected={connected && !needsAuth}
            onStart={startSwarm}
            lastRunFieldPaths={lastRunFieldPaths}
          />
        </main>
        <ToastContainer toasts={toasts} onDismiss={dismissToast} />
        {needsAuth && authState.status !== "unknown" && (
          <AuthModal authState={authState} onAuthenticate={authenticate} />
        )}
        {userChrome}
      </div>
    );
  }

  // ── Finished ────────────────────────────────────────────────────────
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
            sessionId={sessionId}
            completed={state.completed}
            incompleteReason={state.incompleteReason}
            onReset={resetSwarm}
          />
        </main>
        <ToastContainer toasts={toasts} onDismiss={dismissToast} />
        {needsAuth && authState.status !== "unknown" && (
          <AuthModal authState={authState} onAuthenticate={authenticate} />
        )}
        {userChrome}
      </div>
    );
  }

  // ── Running — full dashboard ─────────────────────────────────────────
  return (
    <div className="flex h-screen flex-col relative overflow-hidden">
      <Starfield intensity={state.boss ? 0.9 : 0.5} sky={state.sky} />

      <Header
        projectName={state.project_name}
        round={state.round}
        maxRounds={state.max_rounds}
        sky={state.sky}
        connected={connected}
      />

      <main className="flex flex-1 overflow-hidden relative z-10 gap-0">

        {/* ── Left: VTuber stage ──────────────────────────────────────── */}
        <div
          className="flex shrink-0 flex-col"
          style={{
            width: 356,
            borderRight: "1px solid rgba(139,92,246,0.1)",
            padding: "6px",
          }}
        >
          <VTuberStage
            agents={state.agents}
            getMouthAmplitude={getMouthAmplitude}
            speakingAgents={speakingAgents}
            onSelectAgent={handleSelectAgent}
            backdrop="studio"
            forcePinnedAgent={vtPinned}
            emotes={emotes}
          />
        </div>

        {/* ── Center: pixel map + event log ───────────────────────────── */}
        <div className="flex flex-1 flex-col min-w-0" style={{ padding: "6px", gap: "6px" }}>

          {/* Pixel map — terminal frame aesthetic */}
          <div
            className="relative flex-[3] min-h-0 rounded-xl overflow-hidden scanlines"
            style={{
              border: "1px solid rgba(139,92,246,0.18)",
              boxShadow: "0 0 0 1px rgba(0,0,0,0.6) inset, 0 0 32px rgba(139,92,246,0.06)",
            }}
          >
            {/* Corner bracket marks — cyber terminal aesthetic */}
            <CornerMarks />

            <Stage
              agents={state.agents}
              sky={state.sky}
              boss={state.boss}
              cookies={state.cookies}
              emotes={emotes}
              getMouthAmplitude={getMouthAmplitude}
              onSelectAgent={handlePixelClick}
              onCookieCollected={collectCookie}
              transitioningAgent={bloomingAgent}
            />
            {state.boss && <BossOverlay boss={state.boss} />}

            <div className="absolute bottom-2 right-2 w-28 z-10">
              <Minimap agents={state.agents} onSelectAgent={handleSelectAgent} />
            </div>
          </div>

          {/* Event log */}
          <div
            className="flex-[1] min-h-0 rounded-xl overflow-hidden"
            style={{ border: "1px solid rgba(255,255,255,0.06)" }}
          >
            <EventLog events={state.events} />
          </div>
        </div>

        {/* ── Right: sidebar panels ────────────────────────────────────── */}
        <div
          className="flex flex-col overflow-y-auto scrollbar-thin"
          style={{
            width: 308,
            borderLeft: "1px solid rgba(255,255,255,0.05)",
            padding: "6px",
            gap: "6px",
          }}
        >
          {showTasks && <TaskPanel tasks={state.tasks} />}

          <RelationshipWeb
            agents={state.agents}
            relationships={state.relationships}
            onSelectAgent={handleSelectAgent}
          />

          {showChat && (
            <div style={{ height: 272 }}>
              <ChatPanel
                room={room}
                messages={chat}
                onSend={sendChat}
                onSetName={setDisplayName}
                onJoinRoom={joinRoom}
              />
            </div>
          )}

          {showFiles && <FileTree files={state.files} sessionId={sessionId} />}

          {/* Agent Cards */}
          <div
            className="flex flex-col rounded-xl overflow-hidden"
            style={{ border: "1px solid rgba(139,92,246,0.15)" }}
          >
            <div
              className="flex items-center gap-2 px-3 py-2"
              style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}
            >
              <span className="text-violet-400 text-[11px]">◈</span>
              <h3 className="text-[11px] font-bold text-violet-300 font-mono tracking-widest uppercase">
                Agents
              </h3>
              <span
                className="ml-auto font-mono text-[9px] rounded-full px-2 py-0.5"
                style={{
                  background: "rgba(139,92,246,0.15)",
                  color: "#a78bfa",
                  border: "1px solid rgba(139,92,246,0.25)",
                }}
              >
                {state.agents.length}
              </span>
            </div>
            <div
              className="flex flex-col overflow-y-auto scrollbar-thin"
              style={{ maxHeight: 240, gap: "4px", padding: "6px" }}
            >
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

      {/* Status bar */}
      <footer
        className="flex items-center justify-between gap-4 px-4 py-1.5 font-mono text-[9px] relative z-10"
        style={{
          borderTop: "1px solid rgba(255,255,255,0.04)",
          background: "rgba(7,6,15,0.9)",
          color: "#534a78",
        }}
      >
        <div className="flex items-center gap-4">
          <span className="text-violet-800">⬡ autonoma</span>
          <span>agents:{state.agents.length}</span>
          <span>files:{state.files.length}</span>
          <span>events:{state.events.length}</span>
          {state.boss && (
            <span style={{ color: "#fb7185" }}>BOSS:{state.boss.name}</span>
          )}
          <span className="text-white/20">|</span>
          <button
            type="button"
            onClick={() => setShowTasks((v) => !v)}
            title="Tasks [T]"
            className={`hover:text-violet-400 transition-colors ${showTasks ? "text-violet-600" : "text-white/30"}`}
          >
            tasks
          </button>
          <button
            type="button"
            onClick={() => setShowChat((v) => !v)}
            title="Chat [C]"
            className={`hover:text-cyan-400 transition-colors ${showChat ? "text-cyan-700" : "text-white/30"}`}
          >
            chat
          </button>
          <button
            type="button"
            onClick={() => setShowFiles((v) => !v)}
            title="Files [F]"
            className={`hover:text-green-400 transition-colors ${showFiles ? "text-green-700" : "text-white/30"}`}
          >
            files
          </button>
          <button
            type="button"
            onClick={() => setShowHelp(true)}
            title="Keyboard shortcuts [?]"
            className="hover:text-white/60 transition-colors text-white/25"
          >
            ?
          </button>
        </div>
        {authState.status === "authenticated" && (
          <div className="flex items-center gap-3">
            <span>
              {authState.isAdmin ? "admin" : `${authState.provider}/${authState.model}`}
            </span>
            <button
              onClick={logout}
              className="hover:text-violet-400 transition-colors underline underline-offset-2"
            >
              logout
            </button>
          </div>
        )}
      </footer>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {selectedAgent && (
        <AgentModal
          agent={selectedAgent}
          onClose={() => setSelectedAgent(null)}
          onSend={sendToAgent}
        />
      )}

      {needsAuth && authState.status !== "unknown" && (
        <AuthModal authState={authState} onAuthenticate={authenticate} />
      )}

      {showHelp && <KeyboardHelpModal onClose={() => setShowHelp(false)} />}

      {userChrome}
    </div>
  );
}

// ── Corner bracket marks ──────────────────────────────────────────────
// Decorative corner marks around the pixel-map stage panel.
// Pure CSS, zero runtime cost.
function CornerMarks() {
  const corner =
    "absolute w-3 h-3 pointer-events-none z-10";
  const lineStyle = "absolute bg-violet-500/50";
  return (
    <>
      {/* top-left */}
      <span className={`${corner} top-1.5 left-1.5`}>
        <span className={`${lineStyle} top-0 left-0 h-px w-full`} />
        <span className={`${lineStyle} top-0 left-0 w-px h-full`} />
      </span>
      {/* top-right */}
      <span className={`${corner} top-1.5 right-1.5`}>
        <span className={`${lineStyle} top-0 right-0 h-px w-full`} />
        <span className={`${lineStyle} top-0 right-0 w-px h-full`} />
      </span>
      {/* bottom-left */}
      <span className={`${corner} bottom-1.5 left-1.5`}>
        <span className={`${lineStyle} bottom-0 left-0 h-px w-full`} />
        <span className={`${lineStyle} bottom-0 left-0 w-px h-full`} />
      </span>
      {/* bottom-right */}
      <span className={`${corner} bottom-1.5 right-1.5`}>
        <span className={`${lineStyle} bottom-0 right-0 h-px w-full`} />
        <span className={`${lineStyle} bottom-0 right-0 w-px h-full`} />
      </span>
    </>
  );
}

// ── Agent Card ────────────────────────────────────────────────────────
const MOOD_STYLES: Record<string, { bg: string; color: string }> = {
  happy:      { bg: "rgba(16,185,129,0.15)",  color: "#6ee7b7" },
  excited:    { bg: "rgba(245,158,11,0.15)",  color: "#fcd34d" },
  frustrated: { bg: "rgba(239,68,68,0.15)",   color: "#fca5a5" },
  proud:      { bg: "rgba(139,92,246,0.15)",  color: "#c4b5fd" },
  worried:    { bg: "rgba(249,115,22,0.15)",  color: "#fdba74" },
  relaxed:    { bg: "rgba(34,211,238,0.15)",  color: "#67e8f9" },
  determined: { bg: "rgba(251,191,36,0.15)",  color: "#fde68a" },
  focused:    { bg: "rgba(99,102,241,0.15)",  color: "#a5b4fc" },
};

function AgentCard({ agent, onClick }: { agent: AgentData; onClick: () => void }) {
  const xpPct = agent.xp_to_next > 0 ? (agent.xp / agent.xp_to_next) * 100 : 0;
  const mood = MOOD_STYLES[agent.mood] ?? { bg: "rgba(255,255,255,0.06)", color: "#9d8ec4" };

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left rounded-lg transition-all"
      style={{
        background: "rgba(17,14,38,0.6)",
        border: "1px solid rgba(255,255,255,0.06)",
        padding: "7px 9px",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "rgba(139,92,246,0.28)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.borderColor = "rgba(255,255,255,0.06)";
      }}
    >
      <div className="flex items-center gap-2">
        <span className="text-base leading-none">{agent.species_emoji || agent.emoji}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span
              className="text-[11px] font-bold truncate font-mono"
              style={{ color: "#ede9fe" }}
            >
              {agent.name}
            </span>
            <span className="text-[9px] font-mono" style={{ color: "#f59e0b" }}>
              L{agent.level}
            </span>
          </div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="text-[9px] font-mono" style={{ color: "#534a78" }}>
              {agent.role}
            </span>
            {agent.rarity && agent.rarity !== "common" && (
              <span
                className="text-[8px] rounded px-1 font-mono"
                style={{
                  background:
                    agent.rarity === "legendary"
                      ? "rgba(245,158,11,0.15)"
                      : agent.rarity === "rare"
                        ? "rgba(139,92,246,0.15)"
                        : "rgba(34,211,238,0.15)",
                  color:
                    agent.rarity === "legendary"
                      ? "#fcd34d"
                      : agent.rarity === "rare"
                        ? "#c4b5fd"
                        : "#67e8f9",
                }}
              >
                {agent.rarity}
              </span>
            )}
          </div>
        </div>
        <span
          className="rounded-full px-1.5 py-0.5 text-[8px] font-mono shrink-0"
          style={{ background: mood.bg, color: mood.color }}
        >
          {agent.mood}
        </span>
      </div>

      {/* XP bar */}
      <div className="mt-2 flex items-center gap-1.5">
        <div
          className="h-0.5 flex-1 rounded-full overflow-hidden"
          style={{ background: "rgba(255,255,255,0.07)" }}
        >
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${xpPct}%`,
              background: "linear-gradient(90deg, #7c3aed, #22d3ee)",
            }}
          />
        </div>
        <span className="text-[8px] font-mono" style={{ color: "#534a78" }}>
          {agent.xp}/{agent.xp_to_next}
        </span>
      </div>

      {/* Traits */}
      {agent.traits && agent.traits.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {agent.traits.slice(0, 3).map((t) => (
            <span
              key={t}
              className="rounded px-1 py-0.5 text-[7px] font-mono"
              style={{ background: "rgba(139,92,246,0.08)", color: "#534a78" }}
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </button>
  );
}

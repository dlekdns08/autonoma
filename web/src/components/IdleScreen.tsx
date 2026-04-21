"use client";

import { useCallback, useEffect, useState } from "react";
import HarnessPanel, { type HarnessStartPayload } from "@/components/HarnessPanel";

const FACES = ["(^_^)", "(o.o)", "(-_-)", "(>.<)", "(*^*)"];
const SPARKLES = ["✦", "✧", "★", "☆", "♥", "♡", "♪"];

interface Props {
  connected: boolean;
  onStart: (goal: string, opts?: HarnessStartPayload) => void;
  /** Field paths that were active in the previous run — the Pipeline
   *  view pulses these nodes so users can see what the last run touched
   *  before they tweak settings for the next one. */
  lastRunFieldPaths?: ReadonlySet<string>;
}

export default function IdleScreen({ connected, onStart, lastRunFieldPaths }: Props) {
  const [frame, setFrame] = useState(0);
  const [goal, setGoal] = useState("");
  const [starting, setStarting] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  // The user can tweak settings before typing a goal; hold the pending
  // payload in state so the gear-button indicator re-renders when
  // customised, and so hitting Enter after closing the panel still
  // applies the pending tweaks on start.
  const [pendingHarness, setPendingHarness] = useState<HarnessStartPayload>({});

  useEffect(() => {
    const t = setInterval(() => setFrame((f) => f + 1), 1000);
    return () => clearInterval(t);
  }, []);

  const handleStart = useCallback(() => {
    const trimmed = goal.trim();
    if (!trimmed || !connected) return;
    setStarting(true);
    onStart(trimmed, pendingHarness);
  }, [goal, connected, onStart, pendingHarness]);

  const handleApplyHarness = useCallback((payload: HarnessStartPayload) => {
    setPendingHarness(payload);
    setPanelOpen(false);
  }, []);

  const handleApplyAndStartHarness = useCallback((payload: HarnessStartPayload) => {
    setPendingHarness(payload);
    setPanelOpen(false);
    const trimmed = goal.trim();
    if (trimmed && connected) {
      setStarting(true);
      onStart(trimmed, payload);
    }
  }, [goal, connected, onStart]);

  const face = FACES[frame % FACES.length];
  const sparkle = SPARKLES[frame % SPARKLES.length];

  const hasCustomHarness =
    !!pendingHarness.preset_id ||
    Object.keys(pendingHarness.overrides ?? {}).length > 0;

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
            onClick={() => setPanelOpen(true)}
            disabled={!connected || starting}
            title="Harness settings"
            className={`rounded-xl border px-3 py-3 text-sm font-mono transition-colors disabled:opacity-30 disabled:cursor-not-allowed ${
              hasCustomHarness
                ? "border-fuchsia-500/60 bg-fuchsia-500/10 text-fuchsia-200 hover:bg-fuchsia-500/20"
                : "border-white/20 bg-slate-900/80 text-white/70 hover:border-white/40"
            }`}
          >
            ⚙
          </button>
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
          {sparkle} The orchestrator will assemble its own team {sparkle}
        </p>
        <div className="flex items-center gap-2 mt-2">
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-green-400 animate-pulse" : "bg-red-500"}`} />
          <span className="text-xs">{connected ? "WebSocket connected" : "Connecting..."}</span>
        </div>
      </div>

      <HarnessPanel
        open={panelOpen}
        onClose={() => setPanelOpen(false)}
        onApply={handleApplyHarness}
        onApplyAndStart={handleApplyAndStartHarness}
        activeFieldPaths={lastRunFieldPaths}
      />
    </div>
  );
}

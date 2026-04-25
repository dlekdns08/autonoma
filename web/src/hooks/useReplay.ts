"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";
import type { SwarmState } from "@/lib/types";

/**
 * Phase 1-#1 — replay one full session round-by-round.
 *
 * The backend returns every persisted ``ProjectState`` checkpoint for a
 * session. Each frame is self-contained, so scrubbing to round N is a
 * pointer assignment — no replay-from-zero required.
 */

export interface ReplayFrame {
  round_number: number;
  created_at: string | null;
  // ``state`` is the JSON-serialised ProjectState. We map it onto the
  // SwarmState shape on demand because the dashboard's components are
  // already wired to that.
  state: ReplayProjectStateJson;
}

export interface ReplayProjectStateJson {
  name?: string;
  description?: string;
  tasks?: unknown[];
  files?: unknown[];
  agents?: unknown[];
  messages?: unknown[];
  started_at?: string;
  completed?: boolean;
  final_answer?: string;
}

export interface ReplayBundle {
  session_id: number;
  frame_count: number;
  first_round: number;
  last_round: number;
  frames: ReplayFrame[];
}

export interface UseReplay {
  bundle: ReplayBundle | null;
  loading: boolean;
  error: string | null;
  // Currently-rendered round number (matches bundle.frames[i].round_number).
  round: number;
  setRound: (round: number) => void;
  step: (delta: number) => void;
  // The ``SwarmState``-shaped projection of the frame at the current round.
  // Returns null when no bundle is loaded.
  state: SwarmState | null;
  refresh: () => Promise<void>;
}

const EMPTY_SWARM_STATE: SwarmState = {
  status: "finished",
  project_name: "",
  goal: "",
  round: 0,
  max_rounds: 0,
  agents: [],
  tasks: [],
  files: [],
  // Stage / room state — replays default to a calm sky and no boss
  // because we're rendering historical data, not live combat.
  sky: "day",
  events: [],
  boss: null,
  cookies: [],
  epilogue: "",
  leaderboard: "",
  multiverse: "",
  graveyard: "",
  relationships: [],
  final_answer: "",
  completed: false,
  incompleteReason: "",
};

function projectFrameToSwarmState(frame: ReplayFrame): SwarmState {
  const s = frame.state ?? {};
  return {
    ...EMPTY_SWARM_STATE,
    project_name: typeof s.name === "string" ? s.name : "",
    goal: typeof s.description === "string" ? s.description : "",
    round: frame.round_number,
    agents: (s.agents as SwarmState["agents"]) ?? [],
    tasks: (s.tasks as SwarmState["tasks"]) ?? [],
    files: (s.files as SwarmState["files"]) ?? [],
    completed: !!s.completed,
    final_answer: typeof s.final_answer === "string" ? s.final_answer : "",
  };
}

export function useReplay(sessionId: number | null): UseReplay {
  const [bundle, setBundle] = useState<ReplayBundle | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [round, setRoundState] = useState<number>(0);

  const refresh = useCallback(async () => {
    if (sessionId == null) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/session/${sessionId}/replay`,
        { credentials: "include", headers: { Accept: "application/json" } },
      );
      if (res.status === 404) {
        setError("이 세션에는 저장된 체크포인트가 없습니다.");
        setBundle(null);
        return;
      }
      if (!res.ok) {
        setError(`HTTP ${res.status}`);
        return;
      }
      const data = (await res.json()) as ReplayBundle;
      setBundle(data);
      // Default cursor: the *first* frame so the viewer can press Play
      // and watch from the start. Jumping to the end would skip the
      // narrative arc the replay exists to preserve.
      setRoundState(data.first_round);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setRound = useCallback(
    (r: number) => {
      if (!bundle) return;
      const clamped = Math.max(
        bundle.first_round,
        Math.min(bundle.last_round, r),
      );
      setRoundState(clamped);
    },
    [bundle],
  );

  const step = useCallback(
    (delta: number) => {
      if (!bundle) return;
      setRoundState((prev) => {
        const next = prev + delta;
        return Math.max(
          bundle.first_round,
          Math.min(bundle.last_round, next),
        );
      });
    },
    [bundle],
  );

  // Resolve the current frame -> SwarmState. We pick the largest frame
  // whose round_number is <= the cursor, mirroring how a video player
  // shows the most recent keyframe at any seek position.
  const state: SwarmState | null = (() => {
    if (!bundle) return null;
    let chosen = bundle.frames[0];
    for (const frame of bundle.frames) {
      if (frame.round_number <= round) chosen = frame;
      else break;
    }
    return projectFrameToSwarmState(chosen);
  })();

  return {
    bundle,
    loading,
    error,
    round,
    setRound,
    step,
    state,
    refresh,
  };
}

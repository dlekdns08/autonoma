"use client";

/**
 * ``useGeneratedGestures`` — per-agent self-authored gestures.
 *
 * The backend composes signature moves per (agent name, trigger) and
 * serves them as ordinary mocap clips under the ``gen:`` id namespace
 * (see ``lib/mocap/clipPlayer.ts``). This hook keeps a cache of the
 * active gestures for every agent currently on stage and exposes a
 * ``lookup`` the trigger resolution consults *before* the per-VRM
 * bindings: a character's own move beats the shared one, which beats
 * the hand-written fallback in ``VRMCharacter``.
 *
 * Refetches on ``gesture.generated`` / ``gesture.retired`` events, which
 * the WebSocket bridge forwards as ``SwarmEvent`` rows.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

export type GeneratedTriggerKind = "mood" | "emote" | "state" | "manual";

export interface GeneratedGesture {
  id: string;
  clip_ref: string;
  agent_name: string;
  trigger_kind: GeneratedTriggerKind;
  trigger_value: string;
  name: string;
  duration_s: number;
  source: "llm" | "fallback";
  votes_up: number;
  votes_down: number;
  status: "active" | "retired";
}

export interface UseGeneratedGestures {
  /** ``gen:<id>`` for (agent, kind, value), or undefined. */
  lookup: (agent: string, kind: GeneratedTriggerKind, value: string) => string | undefined;
  /** All active gestures for one agent (empty until fetched). */
  forAgent: (agent: string) => GeneratedGesture[];
  /** Ask the backend to (re)compose one move. */
  generate: (
    agent: string,
    kind: GeneratedTriggerKind,
    value: string,
    extra?: { force?: boolean; role?: string; mood?: string },
  ) => Promise<GeneratedGesture | null>;
  vote: (id: string, up: boolean) => Promise<GeneratedGesture | null>;
  refresh: (agent?: string) => Promise<void>;
  enabled: boolean;
}

function keyOf(agent: string, kind: string, value: string): string {
  return `${agent}|${kind}|${value}`;
}

export function useGeneratedGestures(
  agentNames: readonly string[],
  lastEvent?: { event: string; data: Record<string, unknown> } | null,
  enabled = true,
): UseGeneratedGestures {
  const [byAgent, setByAgent] = useState<Record<string, GeneratedGesture[]>>({});
  const fetchedRef = useRef<Set<string>>(new Set());

  const fetchAgent = useCallback(
    async (agent: string) => {
      if (!enabled || !agent) return;
      try {
        const url = new URL(`${API_BASE_URL}/api/gestures`, window.location.href);
        url.searchParams.set("agent", agent);
        const res = await fetch(url.toString(), { credentials: "include" });
        if (!res.ok) return; // 401 for anonymous viewers: silently no gestures
        const body = (await res.json()) as { gestures: GeneratedGesture[] };
        setByAgent((prev) => ({ ...prev, [agent]: body.gestures ?? [] }));
      } catch {
        /* offline / aborted — keep whatever we had */
      }
    },
    [enabled],
  );

  // Fetch once per agent as it appears on stage.
  useEffect(() => {
    for (const name of agentNames) {
      if (fetchedRef.current.has(name)) continue;
      fetchedRef.current.add(name);
      void fetchAgent(name);
    }
  }, [agentNames, fetchAgent]);

  // Refetch the affected agent on generation / retirement.
  useEffect(() => {
    if (!lastEvent) return;
    if (lastEvent.event !== "gesture.generated" && lastEvent.event !== "gesture.retired") return;
    const agent = lastEvent.data.agent as string | undefined;
    if (agent) void fetchAgent(agent);
  }, [lastEvent, fetchAgent]);

  const index = useMemo(() => {
    const m = new Map<string, string>();
    for (const [agent, rows] of Object.entries(byAgent)) {
      for (const g of rows) {
        if (g.status !== "active") continue;
        m.set(keyOf(agent, g.trigger_kind, g.trigger_value), g.clip_ref);
      }
    }
    return m;
  }, [byAgent]);

  const lookup = useCallback(
    (agent: string, kind: GeneratedTriggerKind, value: string) =>
      index.get(keyOf(agent, kind, value)),
    [index],
  );

  const forAgent = useCallback((agent: string) => byAgent[agent] ?? [], [byAgent]);

  const generate = useCallback<UseGeneratedGestures["generate"]>(
    async (agent, kind, value, extra) => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/gestures/generate`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agent, kind, value, ...(extra ?? {}) }),
        });
        if (!res.ok) return null;
        const body = (await res.json()) as { gesture: GeneratedGesture };
        await fetchAgent(agent);
        return body.gesture;
      } catch {
        return null;
      }
    },
    [fetchAgent],
  );

  const vote = useCallback<UseGeneratedGestures["vote"]>(
    async (id, up) => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/gestures/${encodeURIComponent(id)}/vote`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ up }),
        });
        if (!res.ok) return null;
        const body = (await res.json()) as { gesture: GeneratedGesture };
        setByAgent((prev) => {
          const rows = prev[body.gesture.agent_name];
          if (!rows) return prev;
          return {
            ...prev,
            [body.gesture.agent_name]: rows
              .map((g) => (g.id === body.gesture.id ? body.gesture : g))
              .filter((g) => g.status === "active"),
          };
        });
        return body.gesture;
      } catch {
        return null;
      }
    },
    [],
  );

  const refresh = useCallback(
    async (agent?: string) => {
      const targets = agent ? [agent] : Object.keys(byAgent);
      await Promise.all(targets.map((a) => fetchAgent(a)));
    },
    [byAgent, fetchAgent],
  );

  return { lookup, forAgent, generate, vote, refresh, enabled };
}

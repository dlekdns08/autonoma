"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentData, FileEntry, RelationshipData, SwarmState, TaskData } from "@/lib/types";
import type { ToastItem } from "@/components/Toast";
import { createToastId } from "@/components/Toast";

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ||
  (typeof window !== "undefined" && window.location.hostname === "autonoma.koala.ai.kr"
    ? `wss://autonoma.koala.ai.kr/api/ws`
    : "ws://localhost:3479/ws");

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined" && window.location.hostname === "autonoma.koala.ai.kr"
    ? "https://autonoma.koala.ai.kr/api"
    : "http://localhost:3479");

const INITIAL_STATE: SwarmState = {
  status: "idle",
  project_name: "",
  goal: "",
  round: 0,
  max_rounds: 0,
  agents: [],
  tasks: [],
  files: [],
  sky: "",
  events: [],
  boss: null,
  epilogue: "",
  leaderboard: "",
  multiverse: "",
  graveyard: "",
  relationships: [],
  final_answer: "",
};

let eventIdCounter = 0;

export function useSwarm() {
  const [state, setState] = useState<SwarmState>(INITIAL_STATE);
  const [connected, setConnected] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const addToast = useCallback((type: ToastItem["type"], title: string, message: string, icon: string) => {
    setToasts((prev) => [
      ...prev.slice(-8),
      { id: createToastId(), type, title, message, icon, timestamp: Date.now() },
    ]);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addEvent = useCallback((event: string, data: Record<string, unknown>) => {
    setState((prev) => ({
      ...prev,
      events: [
        ...prev.events.slice(-200),
        { id: eventIdCounter++, event, data, timestamp: Date.now() },
      ],
    }));
  }, []);

  const handleMessage = useCallback(
    (raw: string) => {
      try {
        const msg = JSON.parse(raw) as { event: string; data: Record<string, unknown> };
        const { event, data } = msg;

        // Always log the event
        if (event !== "snapshot") {
          addEvent(event, data);
        }

        // Generate toasts for important events
        switch (event) {
          case "agent.level_up":
            addToast("level_up", "LEVEL UP!", `${data.agent} reached Lv${data.level}!`, "★");
            break;
          case "boss.appeared":
            addToast("boss", "BOSS APPEARED!", `${data.name} (Lv${data.level}) challenges the swarm!`, "☠");
            break;
          case "boss.defeated":
            addToast("boss", "BOSS DEFEATED!", `${data.name} has been vanquished! +${data.xp_reward}XP`, "★");
            break;
          case "guild.formed":
            addToast("guild", "Guild Formed!", `${data.name}: ${(data.members as string[])?.join(", ")}`, "♥♥");
            break;
          case "fortune.given":
            addToast("fortune", "Fortune Cookie!", `${data.agent}: ${data.fortune}`, "🥠");
            break;
          case "ghost.appears":
            addToast("ghost", "Ghost Sighting!", `${data.message}`, "👻");
            break;
          case "project.completed":
            addToast("achievement", "PROJECT COMPLETE!", "The swarm has finished its work!", "★★★");
            break;
          case "agent.spawned":
            addToast("info", "Agent Spawned", `${data.emoji} ${data.name} (${data.role})`, `${data.emoji}`);
            break;
          case "human.feedback":
            addToast(
              "info",
              "Feedback delivered",
              `${(data.text as string)?.slice(0, 60) ?? ""}`,
              "✉",
            );
            break;
        }

        setState((prev) => {
          const next = { ...prev };

          switch (event) {
            case "snapshot":
              return {
                ...prev,
                status: (data.status as SwarmState["status"]) || "idle",
                project_name: (data.project_name as string) || "",
                goal: (data.goal as string) || "",
                round: (data.round as number) || 0,
                agents: (data.agents as AgentData[]) || [],
                tasks: (data.tasks as TaskData[]) || [],
                files: (data.files as FileEntry[]) || [],
                sky: (data.sky as string) || "",
                relationships: (data.relationships as RelationshipData[]) || [],
                final_answer: (data.final_answer as string) || prev.final_answer,
              };

            case "swarm.started":
              next.status = "running";
              next.max_rounds = (data.max_rounds as number) || 30;
              break;

            case "swarm.round":
              next.round = (data.round as number) || 0;
              next.max_rounds = (data.max_rounds as number) || next.max_rounds;
              if (data.sky) next.sky = data.sky as string;
              if (data.relationships) next.relationships = data.relationships as RelationshipData[];
              break;

            case "swarm.finished":
              next.status = "finished";
              if (data.final_answer) next.final_answer = data.final_answer as string;
              if (data.epilogue) next.epilogue = data.epilogue as string;
              if (data.leaderboard) next.leaderboard = data.leaderboard as string;
              if (data.multiverse) next.multiverse = data.multiverse as string;
              if (data.graveyard) next.graveyard = data.graveyard as string;
              break;

            case "agent.spawned": {
              const name = data.name as string;
              if (!prev.agents.find((a) => a.name === name)) {
                next.agents = [
                  ...prev.agents,
                  {
                    name,
                    emoji: (data.emoji as string) || "?",
                    role: (data.role as string) || "general",
                    color: "cyan",
                    position: { x: 0, y: 0 },
                    state: "idle",
                    mood: "happy",
                    level: 1,
                    xp: 0,
                    xp_to_next: 50,
                  },
                ];
              }
              break;
            }

            case "agent.speech": {
              const agentName = data.agent as string;
              next.agents = prev.agents.map((a) =>
                a.name === agentName ? { ...a, speech: data.text as string } : a,
              );
              break;
            }

            case "agent.state": {
              const agentName = data.agent as string;
              next.agents = prev.agents.map((a) =>
                a.name === agentName
                  ? {
                      ...a,
                      state: (data.state as string) || a.state,
                      mood: (data.mood as string) || a.mood,
                    }
                  : a,
              );
              break;
            }

            case "agent.level_up": {
              const agentName = data.agent as string;
              next.agents = prev.agents.map((a) =>
                a.name === agentName ? { ...a, level: (data.level as number) || a.level } : a,
              );
              break;
            }

            case "task.assigned":
            case "task.completed": {
              const title = data.title as string;
              const existingIdx = prev.tasks.findIndex((t) => t.title === title);
              if (existingIdx >= 0) {
                next.tasks = prev.tasks.map((t, i) =>
                  i === existingIdx
                    ? {
                        ...t,
                        status: event === "task.completed" ? "done" : "in_progress",
                        assigned_to: (data.assigned_to as string) || t.assigned_to,
                      }
                    : t,
                );
              }
              break;
            }

            case "file.created": {
              const path = data.path as string;
              if (path && !prev.files.some((f) => f.path === path)) {
                next.files = [
                  ...prev.files,
                  {
                    path,
                    size: (data.size as number) || 0,
                    description: (data.description as string) || "",
                    created_by: (data.agent as string) || "",
                  },
                ];
              }
              break;
            }

            case "world.clock":
              if (data.sky) next.sky = data.sky as string;
              break;

            case "relationship.update":
              next.relationships = (data.relationships as RelationshipData[]) || prev.relationships;
              break;

            case "boss.appeared":
              next.boss = {
                name: (data.name as string) || "???",
                species: (data.species as string) || "unknown",
                level: (data.level as number) || 1,
                hp: (data.hp as number) || 100,
                max_hp: (data.hp as number) || 100,
              };
              break;

            case "boss.damage":
              if (next.boss) {
                const bossMsg = (data.message as string) || "";
                const match = bossMsg.match(/\((\d+)\/(\d+) HP\)/);
                if (match) {
                  next.boss = { ...next.boss, hp: parseInt(match[1]), max_hp: parseInt(match[2]) };
                }
              }
              break;

            case "boss.defeated":
            case "boss.escaped":
              next.boss = null;
              break;
          }

          return next;
        });
      } catch {
        // ignore parse errors
      }
    },
    [addEvent, addToast],
  );

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => {
      setConnected(false);
      reconnectRef.current = setTimeout(connect, 2000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (e) => handleMessage(e.data);
  }, [handleMessage]);

  const sendMessage = useCallback((message: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ command: "message", text: message }));
    }
  }, []);

  const sendToAgent = useCallback((agentName: string, message: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          command: "message",
          text: message,
          target: agentName,
        }),
      );
    }
  }, []);

  const startSwarm = useCallback((goal: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ command: "start", goal }));
    }
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { state, connected, toasts, dismissToast, sendMessage, sendToAgent, startSwarm };
}

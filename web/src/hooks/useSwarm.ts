"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AgentData,
  AgentEmote,
  AuthState,
  BossData,
  CookieData,
  FileEntry,
  RelationshipData,
  SwarmState,
  TaskData,
  UserCredentials,
} from "@/lib/types";
import type { ToastItem } from "@/components/Toast";
import { createToastId } from "@/components/Toast";
import { useAgentVoice } from "@/hooks/useAgentVoice";

const SESSION_KEY = "autonoma_auth";

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

/** Pick a placement for a fortune cookie inside one of the three HQ rooms.
 *  We pick a spot away from the exact centre so the cookie doesn't vanish
 *  under an agent standing still. */
function pickCookieSpot(recipient: string): { x: number; y: number } {
  // Hash the recipient name so multiple cookies don't pile on the same tile.
  let h = 0;
  for (let i = 0; i < recipient.length; i++) {
    h = (h * 31 + recipient.charCodeAt(i)) | 0;
  }
  const rooms: Array<[number, number, number, number]> = [
    [8, 30, 58, 78], // coder-lab (minX, maxX, minY, maxY)
    [45, 60, 58, 78], // war-room
    [75, 95, 58, 78], // design lounge
  ];
  const room = rooms[Math.abs(h) % rooms.length];
  const xSpan = room[1] - room[0];
  const ySpan = room[3] - room[2];
  const x = room[0] + ((Math.abs(h >> 3) % 1000) / 1000) * xSpan;
  const y = room[2] + ((Math.abs(h >> 9) % 1000) / 1000) * ySpan;
  return { x, y };
}

let eventIdCounter = 0;

const INITIAL_AUTH: AuthState = {
  status: "unknown",
  isAdmin: false,
  provider: null,
  model: null,
  error: null,
  hasAdmin: false,
  serverProvider: null,
  serverModel: null,
};

export function useSwarm() {
  const [state, setState] = useState<SwarmState>(INITIAL_STATE);
  const [connected, setConnected] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [authState, setAuthState] = useState<AuthState>(INITIAL_AUTH);
  // Per-connection session id issued by the backend on auth.status. Every
  // HTTP download route requires it so concurrent users stay isolated.
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [emotes, setEmotes] = useState<Record<string, AgentEmote>>({});
  const emoteSeqRef = useRef(0);
  const voice = useAgentVoice();
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

        // Audio events drive lip-sync only — they're hot-path and would
        // flood the events panel if logged. Pipe them straight to the voice
        // hook and bail before the addEvent / setState reducer below.
        if (
          event === "agent.speech_audio_start" ||
          event === "agent.speech_audio_chunk" ||
          event === "agent.speech_audio_end" ||
          event === "agent.speech_audio_dropped"
        ) {
          voice.pushAudioEvent(event, data);
          return;
        }

        // Always log the event
        if (event !== "snapshot") {
          addEvent(event, data);
        }

        // ── Auth events ──────────────────────────────────────────────
        if (event === "auth.status") {
          const hasAdmin = !!(data.has_admin);
          if (typeof data.session_id === "number") {
            setSessionId(data.session_id);
          }
          setAuthState((prev) => ({
            ...prev,
            status: "required",
            hasAdmin,
            serverProvider: (data.server_provider as AuthState["serverProvider"]) ?? null,
            serverModel: (data.server_model as string) ?? null,
            error: null,
          }));
          // Auto-authenticate if we have stored credentials
          const stored = typeof window !== "undefined"
            ? sessionStorage.getItem(SESSION_KEY)
            : null;
          if (stored && wsRef.current?.readyState === WebSocket.OPEN) {
            try {
              const creds = JSON.parse(stored) as UserCredentials;
              wsRef.current.send(JSON.stringify({ command: "authenticate", ...creds }));
            } catch {
              sessionStorage.removeItem(SESSION_KEY);
            }
          }
          return;
        }

        if (event === "auth.success") {
          setAuthState((prev) => ({
            ...prev,
            status: "authenticated",
            isAdmin: !!(data.is_admin),
            provider: (data.provider as AuthState["provider"]) ?? null,
            model: (data.model as string) ?? null,
            error: null,
          }));
          return;
        }

        if (event === "auth.failed") {
          setAuthState((prev) => ({
            ...prev,
            status: "required",
            error: (data.message as string) ?? "인증에 실패했습니다.",
          }));
          return;
        }

        if (event === "auth.required") {
          setAuthState((prev) => ({
            ...prev,
            status: "required",
            error: (data.message as string) ?? "로그인이 필요합니다.",
          }));
          return;
        }

        // Generate toasts for important events
        switch (event) {
          case "agent.level_up": {
            const name = data.agent as string | undefined;
            if (!name) break;
            addToast("level_up", "LEVEL UP!", `${name} reached Lv${data.level}!`, "★");
            break;
          }
          case "boss.appeared": {
            const name = data.name as string | undefined;
            if (!name) break;
            addToast("boss", "BOSS APPEARED!", `${name} (Lv${data.level}) challenges the swarm!`, "☠");
            break;
          }
          case "boss.defeated": {
            const name = data.name as string | undefined;
            if (!name) break;
            addToast("boss", "BOSS DEFEATED!", `${name} has been vanquished! +${data.xp_reward}XP`, "★");
            break;
          }
          case "guild.formed": {
            const name = data.name as string | undefined;
            if (!name) break;
            addToast("guild", "Guild Formed!", `${name}: ${(data.members as string[])?.join(", ")}`, "♥♥");
            break;
          }
          case "fortune.given": {
            const name = data.agent as string | undefined;
            if (!name) break;
            addToast("fortune", "Fortune Cookie!", `${name}: ${data.fortune}`, "🥠");
            break;
          }
          case "ghost.appears":
            addToast("ghost", "Ghost Sighting!", `${data.message}`, "👻");
            break;
          case "project.completed":
            addToast("achievement", "PROJECT COMPLETE!", "The swarm has finished its work!", "★★★");
            break;
          case "agent.spawned": {
            const name = data.name as string | undefined;
            if (!name) break;
            addToast("info", "Agent Spawned", `${data.emoji} ${name} (${data.role})`, `${data.emoji}`);
            break;
          }
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
            case "snapshot": {
              const snapshotBoss = data.boss as
                | (Partial<BossData> & { hp: number; max_hp: number })
                | null
                | undefined;
              const nextBoss: BossData | null = snapshotBoss
                ? {
                    name: snapshotBoss.name || "???",
                    species: snapshotBoss.species || "unknown",
                    level: snapshotBoss.level || 1,
                    hp: snapshotBoss.hp,
                    max_hp: snapshotBoss.max_hp,
                    x: snapshotBoss.x ?? 52,
                    y: snapshotBoss.y ?? 54,
                    hitSeq: prev.boss?.hitSeq ?? 0,
                    lastDamage: 0,
                    lastAttacker: "",
                  }
                : null;

              const snapshotCookies =
                (data.cookies as Array<{
                  recipient: string;
                  fortune: string;
                }>) || [];
              const nextCookies: CookieData[] = snapshotCookies.map((c) => {
                const existing = prev.cookies.find(
                  (ex) => ex.recipient === c.recipient,
                );
                if (existing) return existing;
                const pos = pickCookieSpot(c.recipient);
                return { recipient: c.recipient, fortune: c.fortune, ...pos };
              });

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
                boss: nextBoss,
                cookies: nextCookies,
              };
            }

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
              next.completed = data.completed === true;
              next.incompleteReason =
                typeof data.incomplete_reason === "string" ? data.incomplete_reason : "";
              break;

            case "swarm.reset":
              return INITIAL_STATE;

            case "agent.spawned": {
              const name = data.name as string | undefined;
              if (!name) break;
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
              const agentName = data.agent as string | undefined;
              if (!agentName) break;
              next.agents = prev.agents.map((a) =>
                a.name === agentName ? { ...a, speech: data.text as string } : a,
              );
              break;
            }

            case "agent.state": {
              const agentName = data.agent as string | undefined;
              if (!agentName) break;
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
              const agentName = data.agent as string | undefined;
              if (!agentName) break;
              next.agents = prev.agents.map((a) =>
                a.name === agentName ? { ...a, level: (data.level as number) || a.level } : a,
              );
              break;
            }

            case "task.assigned":
            case "task.completed": {
              const title = data.title as string | undefined;
              if (!title) break;
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

            case "boss.appeared": {
              const maxHp = (data.max_hp as number) || (data.hp as number) || 100;
              next.boss = {
                name: (data.name as string) || "???",
                species: (data.species as string) || "unknown",
                level: (data.level as number) || 1,
                hp: (data.hp as number) || maxHp,
                max_hp: maxHp,
                x: (data.x as number) ?? 52,
                y: (data.y as number) ?? 54,
                hitSeq: 0,
                lastDamage: 0,
                lastAttacker: "",
              };
              break;
            }

            case "boss.damage": {
              if (next.boss) {
                let hp = next.boss.hp;
                let maxHp = next.boss.max_hp;
                let damage = (data.damage as number) || 0;
                if (typeof data.hp === "number") hp = data.hp as number;
                if (typeof data.max_hp === "number") maxHp = data.max_hp as number;
                if (!damage && typeof data.message === "string") {
                  const m = (data.message as string).match(/(\d+)\/\d+ HP/);
                  if (m) hp = parseInt(m[1], 10);
                }
                next.boss = {
                  ...next.boss,
                  hp,
                  max_hp: maxHp,
                  hitSeq: next.boss.hitSeq + 1,
                  lastDamage: damage,
                  lastAttacker: (data.agent as string) || "",
                };
              }
              break;
            }

            case "boss.defeated":
            case "boss.escaped":
              next.boss = null;
              break;

            case "fortune.given": {
              const recipient = data.agent as string | undefined;
              const fortune = (data.fortune as string) || "";
              if (recipient && !prev.cookies.find((c) => c.recipient === recipient)) {
                const pos = pickCookieSpot(recipient);
                next.cookies = [...prev.cookies, { recipient, fortune, ...pos }];
              }
              break;
            }

            case "fortune.fulfilled": {
              const recipient = data.agent as string | undefined;
              if (!recipient) break;
              // Mark as opened so Stage can play the poof, then drop after a
              // short delay via an expiry check inside Stage itself.
              next.cookies = prev.cookies.map((c) =>
                c.recipient === recipient ? { ...c, openedAt: Date.now() } : c,
              );
              break;
            }
          }

          return next;
        });
      } catch {
        // ignore parse errors
      }
    },
    [addEvent, addToast, voice],
  );

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => {
      setConnected(false);
      // The old session id is dead on the backend the moment the socket
      // drops — clear it so stale downloads can't hit the wrong session.
      setSessionId(null);
      reconnectRef.current = setTimeout(connect, 2000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (e) => handleMessage(e.data);
  }, [handleMessage]);

  /** Send authentication credentials to the backend. */
  const authenticate = useCallback((credentials: UserCredentials) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ command: "authenticate", ...credentials }));
      // Persist for auto-reauth on reconnect (never store admin password)
      if (credentials.type === "user") {
        sessionStorage.setItem(SESSION_KEY, JSON.stringify(credentials));
      } else {
        // Admin: don't persist password; clear any stored user creds
        sessionStorage.removeItem(SESSION_KEY);
      }
    }
  }, []);

  /** Logout: clear auth state and stored credentials. */
  const logout = useCallback(() => {
    sessionStorage.removeItem(SESSION_KEY);
    setAuthState((prev) => ({ ...prev, status: "required", isAdmin: false, provider: null, model: null, error: null }));
  }, []);

  /** Locally mark a fortune cookie as collected when the agent reaches it. */
  const collectCookie = useCallback((recipient: string) => {
    setState((prev) => {
      if (!prev.cookies.find((c) => c.recipient === recipient && c.openedAt === undefined)) {
        return prev;
      }
      return {
        ...prev,
        cookies: prev.cookies.map((c) =>
          c.recipient === recipient && c.openedAt === undefined
            ? { ...c, openedAt: Date.now() }
            : c,
        ),
      };
    });
  }, []);

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

  const resetSwarm = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ command: "reset" }));
    }
    // Optimistically return the UI to idle; the server also broadcasts
    // a fresh snapshot that will arrive shortly and confirm the state.
    //
    // NOTE: we intentionally do NOT clear sessionId here. The backend's
    // reset handler (autonoma/api.py) only clears session.swarm/project
    // and keeps session.session_id intact — the ws connection is still
    // live and the same session continues to own the workspace. Clearing
    // sessionId here would leave download URLs unable to point at the
    // backend until the next auth.status round-trip.
    setState(INITIAL_STATE);
    voice.reset();
  }, [voice]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  // Drop cookies ~1.2s after they open, so Stage has time to play the poof.
  useEffect(() => {
    const opened = state.cookies.filter((c) => c.openedAt !== undefined);
    if (opened.length === 0) return;
    const timers = opened.map((c) => {
      const remaining = Math.max(0, 1200 - (Date.now() - (c.openedAt ?? 0)));
      return setTimeout(() => {
        setState((prev) => ({
          ...prev,
          cookies: prev.cookies.filter((x) => x.recipient !== c.recipient),
        }));
      }, remaining);
    });
    return () => timers.forEach(clearTimeout);
  }, [state.cookies]);

  return {
    state,
    connected,
    toasts,
    dismissToast,
    sendMessage,
    sendToAgent,
    startSwarm,
    resetSwarm,
    collectCookie,
    authState,
    authenticate,
    logout,
    sessionId,
    getMouthAmplitude: voice.getMouthAmplitude,
    speakingAgents: voice.speakingAgents,
  };
}

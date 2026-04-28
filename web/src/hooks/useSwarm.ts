"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AgentData,
  AgentEmote,
  AuthState,
  BossData,
  ChatMessage,
  CookieData,
  FileEntry,
  RelationshipData,
  RoomState,
  SwarmState,
  TaskData,
  UserCredentials,
} from "@/lib/types";
import type { ToastItem } from "@/components/Toast";
import { createToastId } from "@/components/Toast";
import { useAgentVoice } from "@/hooks/useAgentVoice";
import { useSfx } from "@/hooks/useSfx";

const SESSION_KEY = "autonoma_auth";

// WS/API base URLs are resolved lazily so SSR (where ``window`` is
// undefined) never touches the browser globals. ``getWsUrl`` is called
// from ``connect()`` inside a ``useEffect`` — never during render — so
// it is guaranteed to run client-side.
function getWsUrl(): string {
  if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
  if (typeof window === "undefined") return "ws://localhost:3479/api/ws";
  return window.location.hostname === "autonoma.letskoala.com"
    ? "wss://api.letskoala.com/api/ws"
    : "ws://localhost:3479/api/ws";
}

// ``API_BASE_URL`` is used in template strings during render (download
// href, etc.). Cache the resolved value once per module evaluation so
// server HTML and client hydration agree, and favor the build-time env
// var over hostname sniffing whenever possible.
function resolveApiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window === "undefined") {
    // Relative paths work when the backend is same-origin. For dev the
    // operator should set NEXT_PUBLIC_API_URL so SSR and CSR agree.
    return "";
  }
  return window.location.hostname === "autonoma.letskoala.com"
    ? "https://api.letskoala.com"
    : "http://localhost:3479";
}

export const API_BASE_URL: string = resolveApiBase();

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

function roleToColor(role: string): string {
  const r = role.toLowerCase();
  if (r.includes("director") || r.includes("lead")) return "yellow";
  if (r.includes("test") || r.includes("qa") || r.includes("verif")) return "red";
  if (r.includes("review") || r.includes("critic")) return "magenta";
  if (r.includes("writ") || r.includes("doc")) return "green";
  if (r.includes("design") || r.includes("architect")) return "blue";
  return "cyan"; // coder default
}

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

interface SandboxMetrics {
  runs: number;
  failures: number;
  timeouts: number;
  totalDurationMs: number;
}

const INITIAL_SANDBOX_METRICS: SandboxMetrics = {
  runs: 0,
  failures: 0,
  timeouts: 0,
  totalDurationMs: 0,
};

export interface CheckpointEntry {
  id: string;
  round: number;
  created_at: string;
}

/** Row-level binding change event carried by the WS bridge. ``seq`` is a
 *  monotonic counter, so even if the same row is edited twice back to
 *  back the subscriber's ``useEffect`` on this event sees each tick. */
export interface MocapBindingEvent {
  vrm_file: string;
  trigger_kind: string;
  trigger_value: string;
  clip_id: string | null;
  removed: boolean;
  seq: number;
}

/** Clip library mutation event carried by the WS bridge. ``seq`` is a
 *  monotonic counter — consumers gate on it to make the effect
 *  idempotent when React re-runs with the same event object. */
export interface MocapClipEvent {
  seq: number;
  clip_id: string;
  action: "created" | "renamed" | "deleted";
}

/** Admin-fired manual trigger event. Viewers apply an ephemeral
 *  override on the normal binding lookup so the bound clip plays
 *  briefly (bounded TTL) before returning to the mood/state/emote
 *  chain. ``seq`` lets subscribers distinguish two back-to-back fires
 *  of the same (vrm, slug) pair. */
export interface MocapTriggerFiredEvent {
  seq: number;
  vrm_file: string;
  trigger_kind: string;
  trigger_value: string;
  clip_id: string;
}

// Voice binding event state lives in ``useVoiceEventState`` so the WS
// routing here stays thin — the hook below composes it. The exported
// type is re-exposed from this module so existing consumers don't have
// to retarget their imports.
import { useVoiceEventState, type VoiceBindingEvent } from "./voice/useVoiceEventState";
export type { VoiceBindingEvent };

export function useSwarm() {
  const [state, setState] = useState<SwarmState>(INITIAL_STATE);
  const [connected, setConnected] = useState(false);
  const [connectionFailed, setConnectionFailed] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [authState, setAuthState] = useState<AuthState>(INITIAL_AUTH);
  const [sandboxMetrics, setSandboxMetrics] = useState<SandboxMetrics>(INITIAL_SANDBOX_METRICS);
  const [checkpoints, setCheckpoints] = useState<CheckpointEntry[]>([]);
  // Per-connection session id issued by the backend on auth.status. Every
  // HTTP download route requires it so concurrent users stay isolated.
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [emotes, setEmotes] = useState<Record<string, AgentEmote>>({});
  const emoteSeqRef = useRef(0);
  // ``mocapBindingsRefreshToken`` forces a full GET of the bindings
  // table — used for hard resyncs (mount, WS reconnect). Routine
  // row-level edits flow through ``mocapBindingEvent`` instead, which
  // carries the patch payload so consumers can splice without a
  // round-trip. Having both lets us fall back to a full refetch if the
  // event stream ever drops (e.g., socket closed mid-edit).
  const [mocapBindingsRefreshToken, setMocapBindingsRefreshToken] = useState(0);
  const [mocapBindingEvent, setMocapBindingEvent] =
    useState<MocapBindingEvent | null>(null);
  const mocapBindingEventSeqRef = useRef(0);
  // Clip library mutation events (create/rename/delete). Subscribers
  // (``useMocapClips``) use these to invalidate the per-clip cache so a
  // renamed/deleted clip doesn't keep playing out of a stale client
  // cache until its 5-minute TTL expires.
  const [mocapClipEvent, setMocapClipEvent] =
    useState<MocapClipEvent | null>(null);
  const mocapClipEventSeqRef = useRef(0);
  // Admin-fired manual trigger — ephemeral "play this clip once" signal.
  // Same pattern as binding/clip events: monotonic seq guards re-applies
  // when React hands the same event object back on a re-render.
  const [mocapTriggerFiredEvent, setMocapTriggerFiredEvent] =
    useState<MocapTriggerFiredEvent | null>(null);
  const mocapTriggerFiredEventSeqRef = useRef(0);
  // Same pattern as mocap bindings, for voice profile bindings. The
  // state lives in ``useVoiceEventState`` so the concrete refresh/patch
  // plumbing is out of this file.
  const voiceEvents = useVoiceEventState();
  const voiceBindingsRefreshToken = voiceEvents.refreshToken;
  const voiceBindingEvent = voiceEvents.latestEvent;
  // Multi-viewer state — defaults to a private room of one. The host
  // gets `code` filled in on `swarm.starting`; viewers get it from the
  // ?room= query param on first connect.
  const [room, setRoom] = useState<RoomState>({
    code: null,
    isOwner: true,
    viewerCount: 1,
    viewers: [],
  });
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const chatSeqRef = useRef(0);
  // Pipeline-view highlight state. Populated from the last session's
  // ``session.metadata`` payload — keys in ``strategy_picks`` look like
  // ``"section.field=value"``, and the pipeline view needs just the
  // ``section.field`` prefix so matching is value-agnostic.
  const [lastRunFieldPaths, setLastRunFieldPaths] = useState<ReadonlySet<string>>(
    () => new Set<string>(),
  );
  // Set once at mount — the room param is attempt-to-join-as-spectator.
  // Stays in a ref so reconnects can re-attempt without surviving page
  // navigation that wipes the URL (which is the right behavior).
  const pendingJoinCodeRef = useRef<string | null>(null);
  const voice = useAgentVoice();
  const sfx = useSfx();
  // Stable ref so handlers don't recreate when sfx identity changes.
  const sfxRef = useRef(sfx);
  sfxRef.current = sfx;
  // The voice object's reference changes whenever `speakingAgents` updates
  // (it's inside useMemo deps there — necessarily, since consumers read the
  // live Set through this reference). If `voice` sits directly in the deps
  // of `handleMessage`/`resetSwarm`/`connect`, every TTS start/stop rebuilds
  // those callbacks and the WebSocket useEffect re-fires — closing and
  // reopening the socket mid-run. The symptoms: first command sent, then
  // connection flaps, the original session is cleaned up (cancelling the
  // swarm), and the UI sits forever on "running" with no further events.
  // Route through a ref so handlers keep a stable identity.
  const voiceRef = useRef(voice);
  voiceRef.current = voice;
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval>>(undefined);
  // Exponential backoff state: attempt count resets on successful open.
  const reconnectAttemptsRef = useRef(0);

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
          voiceRef.current.pushAudioEvent(event, data);
          return;
        }

        // Streaming speech tokens — show a typing bubble without flooding the
        // event log.  `partial` carries the accumulated speech so far; `done`
        // is true on the final token with the complete text.
        if (event === "agent.speech_token") {
          const agentName = data.agent as string | undefined;
          const partial = (data.partial ?? data.text) as string | undefined;
          if (!agentName || !partial) return;
          setState((prev) => ({
            ...prev,
            agents: prev.agents.map((a) =>
              a.name === agentName ? { ...a, speech: partial + (data.done ? "" : "…") } : a,
            ),
          }));
          return;
        }

        // Reaction icons fire often (one per moody _say). They live
        // outside SwarmState because nothing else reads them — keeping
        // them in their own slice avoids re-render cascades through
        // the agent list every time someone sighs.
        if (event === "agent.emote") {
          const agent = data.agent as string | undefined;
          const icon = data.icon as string | undefined;
          const ttl = (data.ttl_ms as number | undefined) ?? 2000;
          if (!agent || !icon) return;
          const seq = ++emoteSeqRef.current;
          setEmotes((prev) => ({
            ...prev,
            [agent]: { icon, expiresAt: Date.now() + ttl, seq },
          }));
          return;
        }

        // Global mocap binding table changed (another viewer saved in
        // the ``/mocap`` editor). Emit a fresh event object so hooks
        // subscribed to ``mocapBindingEvent`` can apply a row-level
        // patch without a full GET. The event itself is a system-level
        // signal — suppress the event-log entry so the dashboard log
        // doesn't get noisy on every binding save.
        if (event === "mocap.bindings.updated") {
          setMocapBindingEvent({
            vrm_file: String(data.vrm_file ?? ""),
            trigger_kind: String(data.trigger_kind ?? ""),
            trigger_value: String(data.trigger_value ?? ""),
            clip_id:
              data.clip_id == null ? null : String(data.clip_id),
            removed: !!data.removed,
            seq: ++mocapBindingEventSeqRef.current,
          });
          return;
        }

        // Clip library changed — another client created/renamed/deleted
        // a clip. Subscribers invalidate their cached copy so the next
        // playback round-trip reflects the mutation. Suppress the
        // event-log entry so quiet background mutations don't pollute
        // the dashboard log.
        if (event === "mocap.clips.updated") {
          const clipId = String(data.clip_id ?? "");
          const rawAction = String(data.action ?? "");
          const action: MocapClipEvent["action"] =
            rawAction === "created" || rawAction === "renamed" || rawAction === "deleted"
              ? rawAction
              : "renamed";
          if (clipId) {
            setMocapClipEvent({
              seq: ++mocapClipEventSeqRef.current,
              clip_id: clipId,
              action,
            });
          }
          return;
        }

        // Admin-fired manual trigger. Propagates as a one-shot event
        // object; subscribers (page.tsx) record an ephemeral override
        // per vrm so the currently-rendered clip briefly switches to
        // the bound manual clip, then falls back to the normal chain
        // after the TTL. Suppressed from the event log so repeat fires
        // don't pollute it.
        if (event === "mocap.trigger.fired") {
          const vrm = String(data.vrm_file ?? "");
          const clipId = String(data.clip_id ?? "");
          if (vrm && clipId) {
            setMocapTriggerFiredEvent({
              seq: ++mocapTriggerFiredEventSeqRef.current,
              vrm_file: vrm,
              trigger_kind: String(data.trigger_kind ?? "manual"),
              trigger_value: String(data.trigger_value ?? ""),
              clip_id: clipId,
            });
          }
          return;
        }

        // Same row-level patch channel for voice bindings. Suppress the
        // event-log entry so voice config churn doesn't flood the log.
        if (event === "voice.bindings.updated") {
          voiceEvents.applyPatch({
            vrm_file: String(data.vrm_file ?? ""),
            profile_id:
              data.profile_id == null ? null : String(data.profile_id),
            removed: !!data.removed,
          });
          return;
        }

        // Always log the event
        if (event !== "snapshot") {
          addEvent(event, data);
        }

        // ── Server-side errors (e.g. harness validation failure) ──────
        if (event === "error") {
          const msg = (data.message as string) ?? "An error occurred.";
          addToast("info", "Error", msg, "✕");
          return;
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
          // Once authenticated, attempt the deferred ?room=CODE join.
          // We can't join earlier because the server requires auth
          // before any non-auth command is honoured.
          const code = pendingJoinCodeRef.current;
          if (code && wsRef.current?.readyState === WebSocket.OPEN) {
            pendingJoinCodeRef.current = null;
            wsRef.current.send(JSON.stringify({ command: "join_room", code }));
          }
          return;
        }

        // ── Multi-viewer ─────────────────────────────────────────────
        if (event === "swarm.starting") {
          // We started the swarm — the room is now ours and the server
          // hands us back the short code to share.
          const code = (data.room_code as string) ?? null;
          setRoom((prev) => ({ ...prev, code, isOwner: true }));
          return;
        }

        if (event === "room.joined") {
          setRoom((prev) => ({
            ...prev,
            code: (data.code as string) ?? prev.code,
            isOwner: !!(data.is_owner),
          }));
          return;
        }

        if (event === "room.join_failed") {
          // Don't toss the user back to "private room of one" silently —
          // surface it so they know the link was stale.
          addToast(
            "info",
            "Join failed",
            (data.message as string) || "Could not join room.",
            "✕",
          );
          return;
        }

        if (event === "room.viewers") {
          setRoom((prev) => ({
            ...prev,
            viewerCount: (data.viewer_count as number) ?? prev.viewerCount,
            viewers: (data.viewers as string[]) ?? prev.viewers,
          }));
          return;
        }

        if (event === "viewer.chat") {
          const id = ++chatSeqRef.current;
          setChat((prev) =>
            [
              ...prev.slice(-200),
              {
                id,
                from: (data.from as string) ?? "anon",
                text: (data.text as string) ?? "",
                isOwner: !!(data.is_owner),
                timestamp: Date.now(),
              },
            ],
          );
          return;
        }

        if (event === "auth.failed") {
          setAuthState((prev) => ({
            ...prev,
            status: "required",
            error: (data.message as string) ?? "Authentication failed.",
          }));
          return;
        }

        if (event === "auth.required") {
          setAuthState((prev) => ({
            ...prev,
            status: "required",
            error: (data.message as string) ?? "Login required.",
          }));
          return;
        }

        if (event === "session.metadata") {
          const picks = (data.strategy_picks as Record<string, number> | undefined) ?? {};
          const paths = new Set<string>();
          for (const key of Object.keys(picks)) {
            const dot = key.indexOf("=");
            paths.add(dot > 0 ? key.slice(0, dot) : key);
          }
          setLastRunFieldPaths(paths);
        }

        // ── Checkpoint events (Feature 4) ───────────────────────────
        if (event === "session.checkpoint" || event === "checkpoint.saved") {
          const round = (data.round as number) ?? 0;
          const id =
            (data.checkpoint_id as string | undefined) ??
            `${round}-${Date.now()}`;
          const created_at =
            (data.created_at as string | undefined) ?? new Date().toISOString();
          setCheckpoints((prev) => {
            // Avoid duplicate entries for the same checkpoint id.
            if (prev.some((c) => c.id === id)) return prev;
            return [...prev, { id, round, created_at }];
          });
          addToast("info", "Checkpoint Saved", `Round ${round} checkpoint saved`, "✓");
          return;
        }

        // ── New event handlers ───────────────────────────────────────
        if (event === "agent.error") {
          const agent = (data.agent as string) || "Agent";
          const error = (data.error as string) || "An error occurred.";
          addToast("info", "Agent Error", `${agent}: ${error}`, "✖");
          setState((prev) => ({
            ...prev,
            agents: prev.agents.map((a) =>
              a.name === agent ? { ...a, state: "error" } : a,
            ),
          }));
          return;
        }

        if (event === "sandbox.run_started") {
          const agent = (data.agent as string) || "Agent";
          const language = (data.language as string) || "code";
          addToast("info", "Running Code", `${agent} is running ${language} code...`, "▶");
          setSandboxMetrics((prev) => ({ ...prev, runs: prev.runs + 1 }));
          return;
        }

        if (event === "sandbox.run_finished") {
          const agent = (data.agent as string) || "Agent";
          const language = (data.language as string) || "code";
          const exitCode = (data.exit_code as number) ?? 0;
          const ok = !!(data.ok);
          const timedOut = !!(data.timed_out);
          const duration = typeof data.duration === "number" ? data.duration : 0;
          void language;
          setSandboxMetrics((prev) => ({
            ...prev,
            failures: prev.failures + (!ok ? 1 : 0),
            timeouts: prev.timeouts + (timedOut ? 1 : 0),
            totalDurationMs: prev.totalDurationMs + duration * 1000,
          }));
          if (ok) {
            addToast("info", "Code Finished", `${agent} code ran OK`, "✓");
          } else if (timedOut) {
            addToast("info", "Code Timed Out", `${agent} code timed out`, "⏱");
          } else {
            addToast("info", "Code Failed", `${agent} code failed (exit ${exitCode})`, "✖");
          }
          return;
        }

        if (event === "swarm.ready") {
          setState((prev) => ({ ...prev, status: "running" }));
          return;
        }

        if (event === "director.plan_failed") {
          const error = (data.error as string) || "Unknown error";
          addToast("info", "Plan Failed", `Director plan failed: ${error}`, "✖");
          return;
        }

        if (event === "director.stall_escalated") {
          const message = (data.message as string) || "Director stalled.";
          addToast("info", "Stall Escalated", message, "⚠");
          return;
        }

        if (event === "director.review_auto_approved") {
          const count = (data.count as number) ?? 0;
          addToast("info", "Auto-Approved", `${count} stalled review(s) auto-approved`, "✓");
          return;
        }

        if (event === "help.requested") {
          // Already logged via addEvent above; no toast needed
          return;
        }

        if (event === "review.started") {
          const agent = (data.agent as string) || "Agent";
          const verdict = (data.verdict as string) || "";
          if (verdict) {
            addToast("info", "Review", `${agent}: ${verdict}`, "♥");
          }
          return;
        }

        if (event === "message.sent") {
          // Already logged via addEvent above; no toast needed
          return;
        }

        if (event === "workspace.complete") {
          const totalFiles = (data.total_files as number) ?? 0;
          addToast("info", "Workspace Complete", `Workspace complete: ${totalFiles} files`, "★");
          return;
        }

        if (event === "swarm.diagnostic") {
          // Log to console only
          console.debug("[swarm.diagnostic]", data);
          return;
        }

        if (event === "world.event") {
          const title = (data.title as string) || "World Event";
          const description = (data.description as string) || "";
          addToast("info", title, description, "~*~");
          // Fall through to setState below for event log
        }

        if (event === "pong") {
          // Silently ignore pong responses
          return;
        }

        // Generate toasts for important events
        switch (event) {
          case "agent.level_up": {
            const name = data.agent as string | undefined;
            if (!name) break;
            addToast("level_up", "LEVEL UP!", `${name} reached Lv${data.level}!`, "★");
            sfxRef.current.play("level_up");
            break;
          }
          case "achievement.earned": {
            const name = data.agent as string | undefined;
            const title = data.title as string | undefined;
            if (!name || !title) break;
            addToast("achievement", "ACHIEVEMENT UNLOCKED!", `${name}: ${title}`, "🏆");
            sfxRef.current.play("achievement");
            break;
          }
          case "achievement.tier_complete": {
            const name = data.agent as string | undefined;
            const tier = data.tier as string | undefined;
            if (!name || !tier) break;
            addToast(
              "achievement",
              `${tier.toUpperCase()} TIER COMPLETE!`,
              `${name} cleared every ${tier}-tier achievement`,
              "👑",
            );
            sfxRef.current.play("tier_complete");
            break;
          }
          case "boss.appeared": {
            const name = data.name as string | undefined;
            if (!name) break;
            addToast("boss", "BOSS APPEARED!", `${name} (Lv${data.level}) challenges the swarm!`, "☠");
            sfxRef.current.play("boss_appear");
            break;
          }
          case "boss.defeated": {
            const name = data.name as string | undefined;
            if (!name) break;
            addToast("boss", "BOSS DEFEATED!", `${name} has been vanquished! +${data.xp_reward}XP`, "★");
            sfxRef.current.play("boss_defeat");
            break;
          }
          case "guild.formed": {
            const name = data.name as string | undefined;
            if (!name) break;
            addToast("guild", "Guild Formed!", `${name}: ${(data.members as string[])?.join(", ")}`, "♥♥");
            sfxRef.current.play("guild_form");
            break;
          }
          case "fortune.given": {
            const name = data.agent as string | undefined;
            if (!name) break;
            addToast("fortune", "Fortune Cookie!", `${name}: ${data.fortune}`, "🥠");
            sfxRef.current.play("fortune");
            break;
          }
          case "fortune.pickup": {
            const name = data.agent as string | undefined;
            if (!name) break;
            addToast("fortune", "Fortune Opened!", `${name}: ${data.fortune}`, "🥠");
            sfxRef.current.play("fortune");
            break;
          }
          case "ghost.appears":
            addToast("ghost", "Ghost Sighting!", `${data.message}`, "👻");
            sfxRef.current.play("ghost");
            break;
          case "live.reaction": {
            // One-tap viewer reaction. We surface it via a transient
            // toast AND publish to a ref ring buffer so the Stage can
            // render a floating-emoji burst without re-running the
            // dispatcher on every frame.
            const emoji = data.emoji as string | undefined;
            const username = data.username as string | undefined;
            if (!emoji) break;
            addToast(
              "info",
              `${username ?? "viewer"}`,
              emoji,
              emoji,
            );
            // Best-effort: an animation hook can subscribe to
            // ``window.__autonoma_reactions`` and consume the queue.
            // Keeping it on a window-scoped ref avoids threading a
            // new state through every parent of <Stage>.
            try {
              const w = window as unknown as {
                __autonoma_reactions?: Array<{ emoji: string; ts: number }>;
              };
              if (!Array.isArray(w.__autonoma_reactions)) {
                w.__autonoma_reactions = [];
              }
              w.__autonoma_reactions.push({ emoji, ts: Date.now() });
              // Cap the buffer so a heavy raid doesn't eat memory.
              if (w.__autonoma_reactions.length > 100) {
                w.__autonoma_reactions.splice(0, w.__autonoma_reactions.length - 100);
              }
            } catch {
              /* SSR or sandboxed iframe — ignore */
            }
            break;
          }
          case "project.completed":
            addToast("achievement", "PROJECT COMPLETE!", "The swarm has finished its work!", "★★★");
            sfxRef.current.play("complete");
            break;
          case "agent.spawned": {
            const name = data.name as string | undefined;
            if (!name) break;
            addToast("info", "Agent Spawned", `${data.emoji} ${name} (${data.role})`, `${data.emoji}`);
            sfxRef.current.play("spawn");
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
                const role = (data.role as string) || "general";
                next.agents = [
                  ...prev.agents,
                  {
                    name,
                    emoji: (data.emoji as string) || "?",
                    role,
                    color: roleToColor(role),
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
              const speechText = data.text as string | undefined;
              if (agentName && speechText) {
                // Unified path: marks the agent speaking for the UI
                // spotlight immediately, and schedules Web Speech with a
                // short delay so server TTS (if configured) can pre-empt
                // the browser fallback via ``pushAudioEvent``.
                voiceRef.current.requestSpeak(agentName, speechText);
              }
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

            case "agent.mood": {
              const agentName = data.agent as string | undefined;
              const moodValue = data.mood as string | undefined;
              if (!agentName || !moodValue) break;
              next.agents = prev.agents.map((a) =>
                a.name === agentName ? { ...a, mood: moodValue } : a,
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
                const damage = (data.damage as number) || 0;
                if (typeof data.hp === "number") {
                  hp = data.hp as number;
                } else if (typeof data.message === "string") {
                  // Fallback: parse HP from message string if hp field is absent
                  const m = (data.message as string).match(/(\d+)\/\d+ HP/);
                  if (m) hp = parseInt(m[1], 10);
                }
                if (typeof data.max_hp === "number") maxHp = data.max_hp as number;
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

            case "fortune.pickup": {
              const agentName = data.agent as string | undefined;
              const bonus = (data.bonus_xp as number) || 0;
              if (!agentName || !bonus) break;
              next.agents = prev.agents.map((a) =>
                a.name === agentName
                  ? { ...a, xp: Math.min(a.xp + bonus, a.xp_to_next) }
                  : a,
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
    [addEvent, addToast],
  );

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    // Pull the room code off the URL once. We only do this on first
    // connect — manual joins via the "Join room" UI go through
    // joinRoom(code) instead.
    if (typeof window !== "undefined" && pendingJoinCodeRef.current === null) {
      const params = new URLSearchParams(window.location.search);
      const code = params.get("room");
      if (code) pendingJoinCodeRef.current = code.trim().toUpperCase();
    }

    const ws = new WebSocket(getWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setConnectionFailed(false);
      reconnectAttemptsRef.current = 0; // reset backoff on successful connect
      // Mocap bindings are broadcast-only — nothing in ``snapshot``
      // re-sends them on reconnect, so any edits that happened while
      // the socket was down would otherwise be missed. Bump the refresh
      // token so ``useMocapBindings`` does a full GET on reconnect.
      setMocapBindingsRefreshToken((n) => n + 1);
      // Same treatment for voice bindings.
      voiceEvents.bumpRefresh();
      // Set up heartbeat ping every 30 seconds
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ command: "ping" }));
        }
      }, 30_000);
      // Preemptive re-auth on reconnect: if we've already authenticated
      // this browser session, resend creds immediately so the server
      // doesn't sit in an unauthenticated state until the first user
      // action. Cookie-based auto-auth on the server still runs first,
      // but this covers admin-password flows that don't leave a cookie.
      if (typeof window !== "undefined") {
        try {
          const stored = sessionStorage.getItem(SESSION_KEY);
          if (stored) {
            const creds = JSON.parse(stored) as UserCredentials;
            ws.send(JSON.stringify({ command: "authenticate", ...creds }));
          }
        } catch {
          /* bad stored creds — let auth.status prompt re-login */
        }
      }
    };
    ws.onclose = () => {
      clearInterval(pingIntervalRef.current);
      setConnected(false);
      // The old session id is dead on the backend the moment the socket
      // drops — clear it so stale downloads can't hit the wrong session.
      setSessionId(null);
      // Exponential backoff with jitter: 1s, 2s, 4s, 8s … capped at 30s.
      // Jitter (±20%) prevents thundering-herd when many clients reconnect
      // simultaneously after a server restart.
      const attempt = reconnectAttemptsRef.current;
      const MAX_RECONNECT_ATTEMPTS = 15;
      if (attempt >= MAX_RECONNECT_ATTEMPTS) {
        setConnectionFailed(true);
        return; // Stop retrying
      }
      reconnectAttemptsRef.current = attempt + 1;
      const base = Math.min(30_000, 1_000 * 2 ** attempt);
      const jitter = base * 0.2 * (Math.random() * 2 - 1);
      reconnectRef.current = setTimeout(connect, Math.round(base + jitter));
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

  const pickupCookie = useCallback((recipient: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ command: "pickup_cookie", recipient }));
  }, []);

  /** Locally mark a fortune cookie as collected when the agent reaches it. */
  const collectCookie = useCallback(
    (recipient: string) => {
      // Notify the server first so the bus event fires even if the optimistic
      // local state update is superseded by a race with fortune.fulfilled.
      pickupCookie(recipient);
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
    },
    [pickupCookie],
  );

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

  const sendChat = useCallback((text: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;
    const trimmed = text.trim();
    if (!trimmed) return;
    wsRef.current.send(JSON.stringify({ command: "chat", text: trimmed }));
  }, []);

  const setDisplayName = useCallback((name: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ command: "set_name", name }));
  }, []);

  const joinRoom = useCallback((code: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(
      JSON.stringify({ command: "join_room", code: code.trim().toUpperCase() }),
    );
  }, []);

  const startSwarm = useCallback(
    (
      goal: string,
      opts?: {
        preset_id?: string;
        overrides?: Record<string, Record<string, unknown>>;
        template_id?: string;
      },
    ) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      const payload: Record<string, unknown> = { command: "start", goal };
      if (opts?.preset_id) payload.preset_id = opts.preset_id;
      if (opts?.overrides && Object.keys(opts.overrides).length > 0) {
        payload.overrides = opts.overrides;
      }
      if (opts?.template_id) payload.template_id = opts.template_id;
      wsRef.current.send(JSON.stringify(payload));
    },
    [],
  );

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
    setEmotes({});
    setSandboxMetrics(INITIAL_SANDBOX_METRICS);
    setCheckpoints([]);
    voiceRef.current.reset();
  }, []);

  /** Resume a previous run from a saved checkpoint. */
  const resumeFromCheckpoint = useCallback(
    async (sessionId: string, checkpointId: string): Promise<void> => {
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/session/${sessionId}/resume`,
          {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ checkpoint_id: checkpointId }),
          },
        );
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as Record<string, unknown>;
          const detail = (body.detail as string) ?? `HTTP ${res.status}`;
          addToast("info", "Resume Failed", detail, "✕");
          return;
        }
        // Reset local state so the resumed run starts fresh in the UI.
        setState(INITIAL_STATE);
        setEmotes({});
        setSandboxMetrics(INITIAL_SANDBOX_METRICS);
        setCheckpoints([]);
        voiceRef.current.reset();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        addToast("info", "Resume Failed", msg, "✕");
      }
    },
    [addToast],
  );

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectRef.current);
      clearInterval(pingIntervalRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  // Sweep expired emotes once every 500ms. The interval runs for the
  // whole mounted lifetime — restarting it whenever ``emotes`` changes
  // (as a naive dep array would) would cancel each sweep mid-flight.
  useEffect(() => {
    const t = setInterval(() => {
      const now = Date.now();
      setEmotes((prev) => {
        if (Object.keys(prev).length === 0) return prev;
        let changed = false;
        const next: Record<string, AgentEmote> = {};
        for (const [name, e] of Object.entries(prev)) {
          if (e.expiresAt > now) next[name] = e;
          else changed = true;
        }
        return changed ? next : prev;
      });
    }, 500);
    return () => clearInterval(t);
  }, []);

  // Free voice slots for agents that disappear from the state (room
  // switch, swarm reset). Prevents blob URL / analyser node build-up
  // across long sessions.
  const knownAgentNamesRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const current = new Set(state.agents.map((a) => a.name));
    for (const name of knownAgentNamesRef.current) {
      if (!current.has(name)) voiceRef.current.cleanupAgent(name);
    }
    knownAgentNamesRef.current = current;
  }, [state.agents]);

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
    connectionFailed,
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
    emotes,
    getMouthAmplitude: voice.getMouthAmplitude,
    speakingAgents: voice.speakingAgents,
    room,
    chat,
    sendChat,
    setDisplayName,
    joinRoom,
    lastRunFieldPaths,
    sandboxMetrics,
    checkpoints,
    resumeFromCheckpoint,
    mocapBindingsRefreshToken,
    mocapBindingEvent,
    mocapClipEvent,
    mocapTriggerFiredEvent,
    voiceBindingsRefreshToken,
    voiceBindingEvent,
    sfx,
  };
}

export interface AgentData {
  name: string;
  emoji: string;
  role: string;
  color: string;
  position: { x: number; y: number };
  state: string;
  mood: string;
  level: number;
  xp: number;
  xp_to_next: number;
  species?: string;
  species_emoji?: string;
  rarity?: string;
  catchphrase?: string;
  traits?: string[];
  stats?: Record<string, number>;
  speech?: string;
}

export interface TaskData {
  id: string;
  title: string;
  status: string;
  assigned_to: string;
}

export interface EventLogEntry {
  id: number;
  event: string;
  data: Record<string, unknown>;
  timestamp: number;
}

export interface FileEntry {
  path: string;
  size: number;
  description?: string;
  created_by?: string;
}

export interface SwarmState {
  status: "idle" | "running" | "finished";
  project_name: string;
  goal: string;
  round: number;
  max_rounds: number;
  agents: AgentData[];
  tasks: TaskData[];
  files: FileEntry[];
  sky: string;
  events: EventLogEntry[];
  boss: BossData | null;
  cookies: CookieData[];
  epilogue: string;
  leaderboard: string;
  multiverse: string;
  graveyard: string;
  relationships: RelationshipData[];
  final_answer: string;
  /** True only when every task finished DONE. False when the run hit
   *  max_rounds or was stopped externally while tasks were still open. */
  completed: boolean;
  /** Empty on clean completion. Otherwise the backend's exit reason
   *  (e.g. "max_rounds_reached", "stopped_externally"). */
  incompleteReason: string;
}

export interface BossData {
  name: string;
  species: string;
  level: number;
  hp: number;
  max_hp: number;
  /** Center x on the stage (percent space 0–100). */
  x: number;
  /** Center y on the stage (percent space 0–100). */
  y: number;
  /** Monotonic counter that bumps on each damage event — used to trigger
   *  the hit-flash and damage-number VFX without needing an event queue. */
  hitSeq: number;
  /** Damage amount from the most recent hit (for the floating number). */
  lastDamage: number;
  /** Name of the agent that dealt the most recent hit. */
  lastAttacker: string;
}

/** A fortune cookie currently resting on the stage, waiting for its
 *  recipient to walk over and open it. Position is chosen client-side
 *  (the backend doesn't know map coordinates). */
export interface CookieData {
  recipient: string;
  fortune: string;
  x: number;
  y: number;
  /** Set briefly when the cookie is fulfilled, so Stage can play a poof. */
  openedAt?: number;
}

/** A short-lived reaction icon floating above an agent's head. The
 *  backend emits one per `_say` whose mood maps to an icon; the
 *  frontend keeps it visible until `expiresAt` and then drops it. */
export interface AgentEmote {
  icon: string;
  expiresAt: number;
  /** Bumped each time the same agent re-emotes so the React `key` flips
   *  and the CSS animation restarts cleanly. */
  seq: number;
}

/** Multi-viewer room metadata. The host's swarm is shared with anyone
 *  who joins via the short code. */
export interface RoomState {
  /** Short code shareable with friends — `null` until the host starts. */
  code: string | null;
  /** True when this client is the room host (the only one who can
   *  start/stop/reset). */
  isOwner: boolean;
  /** Number of currently-connected viewers, including the host. */
  viewerCount: number;
  /** Display names of all viewers, in join order. */
  viewers: string[];
}

/** A single line of spectator chat. */
export interface ChatMessage {
  id: number;
  from: string;
  text: string;
  isOwner: boolean;
  timestamp: number;
}

export interface RelationshipData {
  from: string;
  to: string;
  trust: number;
  label?: string;
}

// ── Auth ──────────────────────────────────────────────────────────────────

export type LLMProvider = "anthropic" | "openai" | "vllm";

/** Credentials sent to the backend to authenticate a WebSocket session. */
export interface UserCredentials {
  type: "admin" | "user";
  // admin fields
  password?: string;
  // user fields
  provider?: LLMProvider;
  api_key?: string;
  model?: string;
  base_url?: string;
}

/** Current auth state for the frontend. */
export interface AuthState {
  status: "unknown" | "required" | "authenticated";
  isAdmin: boolean;
  provider: LLMProvider | null;
  model: string | null;
  error: string | null;
  /** Whether the server has an admin account configured. */
  hasAdmin: boolean;
  /** Provider the server is configured to use (shown in admin login hint). */
  serverProvider: LLMProvider | null;
  serverModel: string | null;
}

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

export interface RelationshipData {
  from: string;
  to: string;
  trust: number;
  label?: string;
}

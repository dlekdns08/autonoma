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

export interface SwarmState {
  status: "idle" | "running" | "finished";
  project_name: string;
  goal: string;
  round: number;
  max_rounds: number;
  agents: AgentData[];
  tasks: TaskData[];
  files: string[];
  sky: string;
  events: EventLogEntry[];
  boss: BossData | null;
  epilogue: string;
  leaderboard: string;
  multiverse: string;
  graveyard: string;
  relationships: RelationshipData[];
}

export interface BossData {
  name: string;
  species: string;
  level: number;
  hp: number;
  max_hp: number;
}

export interface RelationshipData {
  from: string;
  to: string;
  trust: number;
  label?: string;
}

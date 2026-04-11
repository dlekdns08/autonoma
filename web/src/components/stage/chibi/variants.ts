import type { ChibiMood, ChibiRole, ChibiSpecies, ChibiState } from "./types";

const SPECIES_MAP: Record<string, ChibiSpecies> = {
  cat: "cat",
  tiger: "cat",
  lion: "cat",
  rabbit: "rabbit",
  hare: "rabbit",
  jackalope: "rabbit",
  fox: "fox",
  wolf: "fox",
  kitsune: "fox",
  owl: "owl",
  eagle: "owl",
  phoenix: "owl",
  bear: "bear",
  grizzly: "bear",
  "polar bear": "bear",
  polarbear: "bear",
  penguin: "penguin",
  emperor: "penguin",
  "ice dragon": "penguin",
  icedragon: "penguin",
  hamster: "hamster",
  chinchilla: "hamster",
  capybara: "hamster",
  dog: "dog",
  husky: "dog",
  "dire wolf": "dog",
  direwolf: "dog",
  panda: "panda",
  "red panda": "panda",
  redpanda: "panda",
  "spirit bear": "panda",
  spiritbear: "panda",
  duck: "duck",
  swan: "duck",
  thunderbird: "duck",
};

const MOOD_WHITELIST = new Set<ChibiMood>([
  "happy",
  "excited",
  "proud",
  "inspired",
  "focused",
  "determined",
  "frustrated",
  "tired",
  "nostalgic",
  "worried",
  "curious",
  "mischievous",
  "relaxed",
  "neutral",
]);

const STATE_WHITELIST = new Set<ChibiState>([
  "idle",
  "walking",
  "talking",
  "thinking",
  "working",
  "celebrating",
]);

const ROLE_MAP: Record<string, ChibiRole> = {
  director: "director",
  manager: "director",
  lead: "director",
  orchestrator: "director",
  coordinator: "director",
  coder: "coder",
  engineer: "coder",
  developer: "coder",
  programmer: "coder",
  implementer: "coder",
  reviewer: "reviewer",
  auditor: "reviewer",
  inspector: "reviewer",
  tester: "tester",
  qa: "tester",
  verifier: "tester",
  validator: "tester",
  writer: "writer",
  documenter: "writer",
  scribe: "writer",
  designer: "designer",
  architect: "designer",
  planner: "designer",
};

export function resolveSpecies(species?: string): ChibiSpecies {
  if (!species) return "human";
  const k = species.toLowerCase().trim();
  return SPECIES_MAP[k] ?? "human";
}

export function resolveMood(mood?: string): ChibiMood {
  if (!mood) return "neutral";
  const k = mood.toLowerCase().trim() as ChibiMood;
  return MOOD_WHITELIST.has(k) ? k : "neutral";
}

export function resolveState(state?: string): ChibiState {
  if (!state) return "idle";
  const k = state.toLowerCase().trim() as ChibiState;
  return STATE_WHITELIST.has(k) ? k : "idle";
}

export function resolveRole(role?: string): ChibiRole {
  if (!role) return "generic";
  const k = role.toLowerCase().trim();
  if (ROLE_MAP[k]) return ROLE_MAP[k];
  for (const [kw, r] of Object.entries(ROLE_MAP)) {
    if (k.includes(kw)) return r;
  }
  return "generic";
}

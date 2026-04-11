export type ChibiRole =
  | "director"
  | "coder"
  | "reviewer"
  | "tester"
  | "writer"
  | "designer"
  | "generic";

export type ChibiSpecies =
  | "cat"
  | "rabbit"
  | "fox"
  | "owl"
  | "bear"
  | "penguin"
  | "hamster"
  | "dog"
  | "panda"
  | "duck"
  | "human";

export type ChibiMood =
  | "happy"
  | "excited"
  | "proud"
  | "inspired"
  | "focused"
  | "determined"
  | "frustrated"
  | "tired"
  | "nostalgic"
  | "worried"
  | "curious"
  | "mischievous"
  | "relaxed"
  | "neutral";

export type ChibiState =
  | "idle"
  | "walking"
  | "talking"
  | "thinking"
  | "working"
  | "celebrating";

export type ChibiRarity = "common" | "uncommon" | "rare" | "legendary";

export type HairStyle =
  | "shortBob"
  | "longStraight"
  | "twinTails"
  | "sidePonytail"
  | "braid"
  | "spiky"
  | "wavy"
  | "bun"
  | "hoodHidden";

export interface PaletteSlot {
  /** primary outfit body colour (jacket / robe) */
  outfitPrimary: string;
  /** secondary outfit accent (trim, inner shirt) */
  outfitSecondary: string;
  /** pants / skirt / lower body */
  outfitBottom: string;
  /** hair base colour */
  hair: string;
  /** hair highlight (lighter tint) */
  hairLight: string;
  /** eye iris colour */
  eye: string;
  /** magical/aura tone for effects tied to this slot */
  aura: string;
}

export interface ChibiProps {
  species?: string;
  mood?: string;
  state?: string;
  role?: string;
  facingLeft?: boolean;
  walkPhase?: number;
  rarity?: string;
  /** deterministic seed for hair style / palette slot selection. defaults to agent name. */
  seed?: string;
  /** legacy override, kept for compat */
  bodyColor?: string;
  size?: number;
}

/**
 * All chibi parts share this viewBox. 128 × 192, chibi proportions roughly
 * head : body = 1 : 1 (huge anime head). Key anchors:
 *
 *   head center     (64, 60)     radius ≈ 46
 *   neck            (64, 108)
 *   shoulder L/R    (40, 116) / (88, 116)
 *   torso box       (36..92, 112..158)
 *   hip L/R         (54, 158) / (74, 158)
 *   feet ground     y = 184
 */
export const CHIBI_VIEWBOX = {
  width: 128,
  height: 192,
  headCx: 64,
  headCy: 60,
  headR: 46,
  neckY: 108,
  shoulderLx: 40,
  shoulderRx: 88,
  shoulderY: 116,
  torsoTop: 112,
  torsoBottom: 158,
  hipLx: 54,
  hipRx: 74,
  hipY: 158,
  footY: 184,
  groundY: 188,
} as const;

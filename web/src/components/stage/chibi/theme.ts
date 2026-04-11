import type { HairStyle, PaletteSlot } from "./types";

/** Line-art colour used across every part. Chibi outline is dark navy,
 *  slightly softer than pure black so it reads as a "painted" character
 *  rather than cel-shaded cartoon. */
export const OUTLINE = "#2a1b3d";
export const OUTLINE_SOFT = "#3d2a52";

/** Skin tones. Chibi faces are pale and warm with pink undertones. */
export const SKIN_BASE = "#fde4cf";
export const SKIN_SHADE = "#f4c7a1";
export const SKIN_HIGHLIGHT = "#fff4ea";
export const BLUSH = "#ff9fb0";
export const LIP = "#e96977";

/** MapleStory-ish pastel palette. Each slot is a fully coordinated
 *  outfit + hair + aura combo. We pick slots deterministically from
 *  an agent's seed so each agent looks distinct but stable across
 *  re-renders. */
export const PALETTES: PaletteSlot[] = [
  // 0 — soft lavender mage
  {
    outfitPrimary: "#b79dff",
    outfitSecondary: "#e7d8ff",
    outfitBottom: "#5b4b8a",
    hair: "#c9b8ff",
    hairLight: "#e8dfff",
    eye: "#6d4fd1",
    aura: "#c4b0ff",
  },
  // 1 — cherry blossom idol
  {
    outfitPrimary: "#ff9ec1",
    outfitSecondary: "#ffd9e7",
    outfitBottom: "#c54f7a",
    hair: "#ffb8d1",
    hairLight: "#ffe3ee",
    eye: "#d4377a",
    aura: "#ff9fc2",
  },
  // 2 — sky knight
  {
    outfitPrimary: "#7fd1ff",
    outfitSecondary: "#d3efff",
    outfitBottom: "#2e6da8",
    hair: "#a7e3ff",
    hairLight: "#e0f4ff",
    eye: "#2b8bd6",
    aura: "#8ad7ff",
  },
  // 3 — spring ranger
  {
    outfitPrimary: "#9bd68a",
    outfitSecondary: "#e2f5cf",
    outfitBottom: "#4a7a3a",
    hair: "#d6e09b",
    hairLight: "#f0f4c4",
    eye: "#4a8530",
    aura: "#a8e590",
  },
  // 4 — sunset rogue
  {
    outfitPrimary: "#ffb27a",
    outfitSecondary: "#ffe2c4",
    outfitBottom: "#a14a22",
    hair: "#ffd1a0",
    hairLight: "#ffe8cf",
    eye: "#c24510",
    aura: "#ffc08a",
  },
  // 5 — mint priest
  {
    outfitPrimary: "#a4e8d7",
    outfitSecondary: "#deffef",
    outfitBottom: "#357b68",
    hair: "#c5f0e3",
    hairLight: "#e5fbf4",
    eye: "#1f8f74",
    aura: "#aef0de",
  },
  // 6 — royal gold
  {
    outfitPrimary: "#f2cd63",
    outfitSecondary: "#ffecab",
    outfitBottom: "#a87218",
    hair: "#ffe399",
    hairLight: "#fff4cd",
    eye: "#b3811a",
    aura: "#ffd96b",
  },
  // 7 — midnight sorceress
  {
    outfitPrimary: "#6a5acd",
    outfitSecondary: "#a594e8",
    outfitBottom: "#2a1f5a",
    hair: "#413780",
    hairLight: "#6a5eb3",
    eye: "#c9b8ff",
    aura: "#9b86ff",
  },
  // 8 — coral dancer
  {
    outfitPrimary: "#ff8b7a",
    outfitSecondary: "#ffd0c7",
    outfitBottom: "#b94236",
    hair: "#ff9a8a",
    hairLight: "#ffd6ce",
    eye: "#d4483a",
    aura: "#ff9c8a",
  },
  // 9 — glacier mage
  {
    outfitPrimary: "#c7d8ff",
    outfitSecondary: "#eaf0ff",
    outfitBottom: "#3f4d85",
    hair: "#dbe2ff",
    hairLight: "#f2f5ff",
    eye: "#3a52a8",
    aura: "#b5c8ff",
  },
  // 10 — forest druid
  {
    outfitPrimary: "#86b36a",
    outfitSecondary: "#c8dea7",
    outfitBottom: "#3a5524",
    hair: "#6e8b48",
    hairLight: "#a6c27c",
    eye: "#3f6b22",
    aura: "#9ad07d",
  },
  // 11 — candy assassin
  {
    outfitPrimary: "#ffa8d7",
    outfitSecondary: "#ffd1e8",
    outfitBottom: "#6a2a5a",
    hair: "#ffc8e0",
    hairLight: "#ffe5f1",
    eye: "#8a2e72",
    aura: "#ffb3dc",
  },
];

export const HAIR_STYLES: HairStyle[] = [
  "shortBob",
  "longStraight",
  "twinTails",
  "sidePonytail",
  "braid",
  "spiky",
  "wavy",
  "bun",
];

/** Cheap deterministic hash so `seed -> index` is stable across renders. */
export function seedHash(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0);
}

export function pickPalette(seed: string): PaletteSlot {
  return PALETTES[seedHash(seed) % PALETTES.length];
}

export function pickHairStyle(seed: string): HairStyle {
  return HAIR_STYLES[(seedHash(seed + "_hair")) % HAIR_STYLES.length];
}

/** Rarity → drop-shadow filter stack (used on the outermost <svg>). */
export const RARITY_GLOW: Record<string, string | undefined> = {
  common: undefined,
  uncommon: "drop-shadow(0 0 3px rgba(167,243,208,0.8))",
  rare: "drop-shadow(0 0 5px rgba(34,211,238,0.85)) drop-shadow(0 0 10px rgba(34,211,238,0.5))",
  legendary:
    "drop-shadow(0 0 6px rgba(251,191,36,0.95)) drop-shadow(0 0 14px rgba(245,158,11,0.65)) drop-shadow(0 0 22px rgba(251,191,36,0.35))",
};

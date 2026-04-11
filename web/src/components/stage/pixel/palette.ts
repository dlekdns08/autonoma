/**
 * Pixel-art palette system.
 *
 * Characters are authored with abstract colour keys (H = hair dark, h = hair
 * light, S = skin, E = eye, O = outfit dark, o = outfit light, P = pants dark,
 * p = pants light, B = boots dark, b = boots light, m = mouth). The role and
 * seed pick a concrete colour set below, so every agent is visually distinct
 * but locked to a consistent palette.
 */

import type { PixelPalette } from "./types";

// ── shared tones ──────────────────────────────────────────────────────────
export const OUTLINE = "#1f1633";
export const SKIN_LIGHT = "#f8d4a8";
export const SKIN_SHADE = "#d89868";
export const EYE_DARK = "#1f1633";
export const MOUTH = "#8a2b3c";
export const SHADOW = "#00000044";
export const EAR_INNER_HIGHLIGHT = "#ffb8cc";

// ── role outfit colours ───────────────────────────────────────────────────
export interface RoleColors {
  outfitDark: string;
  outfitLight: string;
  pantsDark: string;
  pantsLight: string;
  bootsDark: string;
  bootsLight: string;
}

export const ROLE_COLORS: Record<string, RoleColors> = {
  director: {
    outfitDark: "#6b1f4a",
    outfitLight: "#c23f7a",
    pantsDark: "#2a1a3a",
    pantsLight: "#4a2c5a",
    bootsDark: "#1a0f22",
    bootsLight: "#3a2244",
  },
  coder: {
    outfitDark: "#1a4b7a",
    outfitLight: "#3a8bd6",
    pantsDark: "#1a2230",
    pantsLight: "#34445a",
    bootsDark: "#0f1520",
    bootsLight: "#252c38",
  },
  reviewer: {
    outfitDark: "#2d5e3e",
    outfitLight: "#5ab973",
    pantsDark: "#26361e",
    pantsLight: "#4a5d33",
    bootsDark: "#13200c",
    bootsLight: "#2a3a1c",
  },
  tester: {
    outfitDark: "#7a4a18",
    outfitLight: "#e8a050",
    pantsDark: "#3a2510",
    pantsLight: "#664020",
    bootsDark: "#1e1208",
    bootsLight: "#3a2310",
  },
  writer: {
    outfitDark: "#5c2b7a",
    outfitLight: "#a55ad9",
    pantsDark: "#2a1a3a",
    pantsLight: "#4a2c5a",
    bootsDark: "#1a0f22",
    bootsLight: "#3a2244",
  },
  designer: {
    outfitDark: "#b8533a",
    outfitLight: "#f49a6e",
    pantsDark: "#2f2332",
    pantsLight: "#52404f",
    bootsDark: "#1e1420",
    bootsLight: "#3b2838",
  },
  generic: {
    outfitDark: "#2a5a80",
    outfitLight: "#5ea3d0",
    pantsDark: "#2a2038",
    pantsLight: "#463854",
    bootsDark: "#18121e",
    bootsLight: "#302436",
  },
};

// ── hair variants ─────────────────────────────────────────────────────────
export interface HairColors {
  dark: string;
  light: string;
}

// ── headwear variants (cap / beanie / wizard hat) ────────────────────────
export interface HeadwearColors {
  dark: string;
  light: string;
}

export const HEADWEAR_SLOTS: HeadwearColors[] = [
  { dark: "#7a2318", light: "#c84a32" }, // red
  { dark: "#1a4b7a", light: "#3a8bd6" }, // blue
  { dark: "#2d5e3e", light: "#5ab973" }, // green
  { dark: "#8b5a1f", light: "#e8b447" }, // gold
  { dark: "#1a1a22", light: "#3a3a44" }, // black
  { dark: "#5c2b7a", light: "#a55ad9" }, // purple
  { dark: "#a86020", light: "#e89058" }, // orange
  { dark: "#2a3a5a", light: "#4a6890" }, // navy
];

export function pickHeadwearColors(seed: string): HeadwearColors {
  return HEADWEAR_SLOTS[seedHash(seed + "_hw") % HEADWEAR_SLOTS.length];
}

export const HAIR_SLOTS: HairColors[] = [
  { dark: "#3a1810", light: "#7c3a1c" }, // brown
  { dark: "#1b1420", light: "#3a2844" }, // black-purple
  { dark: "#6b2410", light: "#c86432" }, // orange
  { dark: "#4b2e0f", light: "#b88444" }, // dirty blonde
  { dark: "#2a1848", light: "#5a3a9c" }, // deep purple
  { dark: "#703d1b", light: "#d69250" }, // auburn
  { dark: "#124a2a", light: "#2d8c5a" }, // moss green
  { dark: "#1b3d5e", light: "#3e7aa8" }, // navy blue
  { dark: "#5c1a3a", light: "#b23d6b" }, // magenta
  { dark: "#8b5a1f", light: "#e8b447" }, // gold blonde
  { dark: "#0f2a3a", light: "#2c5f7c" }, // teal
  { dark: "#3f0a1a", light: "#8a1f3a" }, // wine
];

// ── species skin / animal tint ────────────────────────────────────────────
export interface SpeciesColors {
  skin: string;
  skinShade: string;
  /** extra ear/tail colour (for future use) */
  accent: string;
}

export const SPECIES_COLORS: Record<string, SpeciesColors> = {
  human: { skin: SKIN_LIGHT, skinShade: SKIN_SHADE, accent: "#c23f7a" },
  cat: { skin: "#f3d2a0", skinShade: "#c48a54", accent: "#6b3a14" },
  fox: { skin: "#f4c79a", skinShade: "#c56a2a", accent: "#e56824" },
  rabbit: { skin: "#fbeedc", skinShade: "#d6b38d", accent: "#eaeaea" },
  dog: { skin: "#f0d0a0", skinShade: "#a86a34", accent: "#5a3214" },
  hamster: { skin: "#f7d88a", skinShade: "#c8963a", accent: "#e4b44a" },
  panda: { skin: "#f6eedc", skinShade: "#b8b0a0", accent: "#1a1a22" },
  bear: { skin: "#e8c290", skinShade: "#7a4a1e", accent: "#4a2c10" },
  owl: { skin: "#f4d6a0", skinShade: "#a87440", accent: "#7a4a18" },
  duck: { skin: "#fbe4a0", skinShade: "#c8942c", accent: "#f0c020" },
  penguin: { skin: "#f4e0c8", skinShade: "#a89070", accent: "#1a1f34" },
};

// ── deterministic seed hash ───────────────────────────────────────────────
export function seedHash(seed: string): number {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function resolveRole(role?: string): keyof typeof ROLE_COLORS {
  if (!role) return "generic";
  const k = role.toLowerCase();
  const keywords: Array<[string, keyof typeof ROLE_COLORS]> = [
    ["director", "director"], ["manager", "director"], ["lead", "director"],
    ["orchestrator", "director"], ["coordinator", "director"],
    ["coder", "coder"], ["engineer", "coder"], ["developer", "coder"],
    ["programmer", "coder"], ["implementer", "coder"],
    ["reviewer", "reviewer"], ["auditor", "reviewer"], ["inspector", "reviewer"],
    ["tester", "tester"], ["qa", "tester"], ["verifier", "tester"], ["validator", "tester"],
    ["writer", "writer"], ["documenter", "writer"], ["scribe", "writer"],
    ["designer", "designer"], ["architect", "designer"], ["planner", "designer"],
  ];
  for (const [kw, r] of keywords) if (k.includes(kw)) return r;
  return "generic";
}

export function resolveSpecies(species?: string): keyof typeof SPECIES_COLORS {
  if (!species) return "human";
  const k = species.toLowerCase().trim();
  const map: Record<string, keyof typeof SPECIES_COLORS> = {
    cat: "cat", tiger: "cat", lion: "cat",
    fox: "fox", wolf: "fox", kitsune: "fox",
    rabbit: "rabbit", hare: "rabbit", jackalope: "rabbit",
    dog: "dog", husky: "dog",
    hamster: "hamster", capybara: "hamster", chinchilla: "hamster",
    panda: "panda", redpanda: "panda",
    bear: "bear", grizzly: "bear",
    owl: "owl", eagle: "owl", phoenix: "owl",
    duck: "duck", swan: "duck", thunderbird: "duck",
    penguin: "penguin",
    human: "human",
  };
  return map[k] ?? "human";
}

export function pickHairColors(seed: string): HairColors {
  return HAIR_SLOTS[seedHash(seed + "_hair") % HAIR_SLOTS.length];
}

/** Build the concrete 13-char palette that a character sprite gets rendered with. */
export interface CharacterPaletteInput {
  role?: string;
  species?: string;
  seed: string;
  mood?: string;
}

export function buildCharacterPalette(input: CharacterPaletteInput): PixelPalette {
  const roleKey = resolveRole(input.role);
  const speciesKey = resolveSpecies(input.species);
  const hair = pickHairColors(input.seed);
  const headwear = pickHeadwearColors(input.seed);
  const outfit = ROLE_COLORS[roleKey];
  const skin = SPECIES_COLORS[speciesKey];

  // Mood tweaks eye colour subtly
  const eyeByMood: Record<string, string> = {
    excited: "#ffdf4a",
    inspired: "#9adfff",
    frustrated: "#ff5a4a",
    tired: "#8a8aa0",
    proud: "#ffb93a",
  };
  const mood = (input.mood ?? "").toLowerCase();
  const eye = eyeByMood[mood] ?? EYE_DARK;

  return {
    "#": OUTLINE,
    H: hair.dark,
    h: hair.light,
    S: skin.skin,
    s: skin.skinShade,
    E: eye,
    m: MOUTH,
    O: outfit.outfitDark,
    o: outfit.outfitLight,
    P: outfit.pantsDark,
    p: outfit.pantsLight,
    B: outfit.bootsDark,
    b: outfit.bootsLight,
    K: headwear.dark,
    k: headwear.light,
    A: skin.accent,
    a: EAR_INNER_HIGHLIGHT,
  };
}

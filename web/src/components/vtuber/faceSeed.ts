/**
 * Deterministic face parameters from the agent's name hash.
 *
 * The design principle: an agent's visual identity must survive across
 * sessions and be identical for the same name. Agents are already hashed
 * in the backend for species/traits/stats; we do the same on the
 * frontend side for purely-cosmetic knobs (hair style, eye color, etc.)
 * so the host and every spectator see the same face without needing to
 * stream the palette down the WebSocket.
 *
 * FNV-1a 32-bit: fast, zero-dependency, good-enough avalanche for the
 * tiny integer ranges we're drawing from here.
 */

function fnv1a(str: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h >>> 0;
}

/** Deterministic PRNG seeded by the hash — mulberry32 style. */
function mkRng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ── Palette ──────────────────────────────────────────────────────────
// Curated to look coherent together — soft anime tones, nothing neon,
// so a group of four agents still reads as a cast rather than a clown car.

export const HAIR_COLORS = [
  "#2a2030", // ink black
  "#3d2a1e", // dark brown
  "#6b3f2a", // auburn
  "#a9763b", // honey
  "#c9a97a", // ash blonde
  "#d4d4d4", // platinum
  "#6b5b8f", // dusty lavender
  "#8f4a6b", // rose
  "#4a6a8f", // steel blue
  "#2d6b5e", // deep teal
  "#a85050", // muted red
  "#f0dcaf", // pale gold
] as const;

export const EYE_COLORS = [
  "#2d4a6b", // indigo
  "#4a8f6b", // jade
  "#8f4a2d", // amber
  "#6b2d4a", // wine
  "#2d6b8f", // cerulean
  "#8f6b2d", // hazel
  "#4a2d6b", // violet
  "#3a3a3a", // near black
] as const;

export const SKIN_TONES = [
  "#f5dcc4",
  "#eccdb0",
  "#d9b89a",
  "#c39978",
  "#a37a5c",
  "#7a5a40",
] as const;

/** Six hairstyle templates — each is a pair of SVG paths (front/back). */
export type HairStyle =
  | "short"
  | "medium"
  | "long"
  | "twintails"
  | "ponytail"
  | "bob";

export const HAIR_STYLES: HairStyle[] = [
  "short",
  "medium",
  "long",
  "twintails",
  "ponytail",
  "bob",
];

/** Derived cosmetic params for a single agent. All fields are stable
 *  for the same `name`. */
export interface FaceSeed {
  hash: number;
  hairStyle: HairStyle;
  hairColor: string;
  eyeColor: string;
  skin: string;
  /** 0..1 — subtle per-agent head-size jitter so a group isn't uniform. */
  faceScale: number;
  /** 0..1 — blink phase offset so agents don't blink in lockstep. */
  blinkOffset: number;
  /** Random seconds between blinks (3–6s range). */
  blinkPeriod: number;
}

export function seedForAgent(name: string): FaceSeed {
  const h = fnv1a(name);
  const rng = mkRng(h);
  // Order of rng() calls is part of the contract — don't reorder or the
  // palette shifts for every character in a breaking way.
  const hairStyle = HAIR_STYLES[Math.floor(rng() * HAIR_STYLES.length)];
  const hairColor = HAIR_COLORS[Math.floor(rng() * HAIR_COLORS.length)];
  const eyeColor = EYE_COLORS[Math.floor(rng() * EYE_COLORS.length)];
  const skin = SKIN_TONES[Math.floor(rng() * SKIN_TONES.length)];
  const faceScale = 0.94 + rng() * 0.12; // ±6% size
  const blinkOffset = rng();
  const blinkPeriod = 3 + rng() * 3;
  return {
    hash: h,
    hairStyle,
    hairColor,
    eyeColor,
    skin,
    faceScale,
    blinkOffset,
    blinkPeriod,
  };
}

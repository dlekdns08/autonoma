/**
 * Pixel art core types — Pokemon Gen 3/4 style.
 *
 * A "grid" is an array of equal-length strings. Each character maps to
 * a colour key via a palette, or `.` for transparent. This keeps all
 * sprite art inline, human-editable, and diffable.
 */

export type PixelGrid = readonly string[];

export type PixelPalette = Readonly<Record<string, string>>;

export interface PixelSprite {
  readonly grid: PixelGrid;
  readonly palette: PixelPalette;
}

export type Direction = "right" | "left";

/** Four walk-cycle frames: 0 = stand, 1 = left-step, 2 = stand, 3 = right-step. */
export interface WalkFrames {
  readonly frames: readonly PixelGrid[];
}

export type SkyMode = "dawn" | "day" | "dusk" | "night";

export interface TintSpec {
  /** rgba overlay painted over the whole map at the given alpha */
  readonly overlay: string;
  /** 0..1 — how much to desaturate base tile colours before drawing */
  readonly desaturate: number;
}

/** Logical pixel dimensions of the whole stage in "game" units. */
export const STAGE = {
  width: 320,
  height: 192,
  tile: 16,
  cols: 20,
  rows: 12,
  /** y-row (in tiles) where the ground/grass horizon sits */
  horizonRow: 6,
  /** ground-level pixel Y (characters' feet sit between horizonRow*16 and rows*16) */
  groundY: 160,
} as const;

/** Character sprite canvas size. */
export const CHAR = {
  width: 16,
  height: 24,
} as const;

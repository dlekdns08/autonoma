/**
 * Pokemon-style overworld character sprite.
 *
 * A 16×24 front-facing character. Four walk frames alternate the legs
 * and bob the body up/down by one pixel — that's the whole Gen 3/4
 * overworld walk cycle secret.
 *
 * All art is defined with abstract palette keys (see palette.ts), so the
 * same sprite can be recoloured per-agent without regenerating grids.
 */

import type { PixelGrid } from "./types";
import { CHAR } from "./types";

type Cell = string;
type Mutable2D = Cell[][];

function blank(): Mutable2D {
  return Array.from({ length: CHAR.height }, () => Array<Cell>(CHAR.width).fill("."));
}

function put(g: Mutable2D, x: number, y: number, c: Cell): void {
  if (x < 0 || x >= CHAR.width || y < 0 || y >= CHAR.height) return;
  g[y][x] = c;
}

function fill(g: Mutable2D, x0: number, y0: number, x1: number, y1: number, c: Cell): void {
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) put(g, x, y, c);
  }
}

function toGrid(g: Mutable2D): PixelGrid {
  return g.map((row) => row.join(""));
}

/**
 * Draw the head + torso stack into the grid. `bodyOffset` shifts the
 * whole head/torso up by that many rows (used for the walk-cycle bob).
 */
function drawHeadTorso(g: Mutable2D, bodyOffset: number): void {
  const off = -bodyOffset;

  // ── HAIR crown ─────────────────────────────────────────────────────
  // row 1: narrow top
  fill(g, 5, 1 + off, 10, 1 + off, "H");
  // row 2: wider
  fill(g, 4, 2 + off, 11, 2 + off, "H");
  // row 3: full width with light highlight streak
  fill(g, 3, 3 + off, 12, 3 + off, "H");
  put(g, 6, 3 + off, "h");
  put(g, 7, 3 + off, "h");
  put(g, 8, 3 + off, "h");
  put(g, 9, 3 + off, "h");
  // row 4: full width, extra highlight tufts
  fill(g, 3, 4 + off, 12, 4 + off, "H");
  put(g, 4, 4 + off, "h");
  put(g, 5, 4 + off, "h");
  put(g, 10, 4 + off, "h");
  put(g, 11, 4 + off, "h");

  // ── FACE: skin interior rows 5-10, hair forms side frame ──────────
  for (let y = 5; y <= 10; y++) {
    put(g, 3, y + off, "H"); // left hair strand
    put(g, 12, y + off, "H"); // right hair strand
    for (let x = 4; x <= 11; x++) put(g, x, y + off, "S");
  }
  // eyes (row 7): two 2×1 eye blocks with gap in middle
  put(g, 5, 7 + off, "E");
  put(g, 6, 7 + off, "E");
  put(g, 9, 7 + off, "E");
  put(g, 10, 7 + off, "E");
  // nose shading row 8 middle
  put(g, 7, 8 + off, "s");
  put(g, 8, 8 + off, "s");
  // mouth row 9
  put(g, 7, 9 + off, "m");
  put(g, 8, 9 + off, "m");
  // chin shade row 10 outer
  put(g, 4, 10 + off, "s");
  put(g, 11, 10 + off, "s");

  // ── chin taper row 11 ────────────────────────────────────────────
  fill(g, 4, 11 + off, 11, 11 + off, "S");
  put(g, 4, 11 + off, "s");
  put(g, 11, 11 + off, "s");

  // ── neck row 12 ─────────────────────────────────────────────────
  fill(g, 6, 12 + off, 9, 12 + off, "s");

  // ── torso rows 13..17 ───────────────────────────────────────────
  for (let y = 13; y <= 17; y++) {
    put(g, 2, y + off, "O");
    put(g, 13, y + off, "O");
    for (let x = 3; x <= 12; x++) put(g, x, y + off, "o");
  }
  // collar v-neck on row 13
  put(g, 7, 13 + off, "O");
  put(g, 8, 13 + off, "O");
  // shirt mid-shade stripe (row 15 darker)
  for (let x = 3; x <= 12; x++) put(g, x, 15 + off, "O");
  for (let x = 4; x <= 11; x++) put(g, x, 15 + off, "o");
  // arm highlight stripe
  put(g, 3, 14 + off, "o");
  put(g, 12, 14 + off, "o");
  // hands (skin) peeking at bottom of torso sides
  put(g, 2, 17 + off, "O");
  put(g, 13, 17 + off, "O");
  put(g, 2, 18 + off, "S");
  put(g, 13, 18 + off, "S");

  // ── belt row 18 ─────────────────────────────────────────────────
  put(g, 3, 18 + off, "P");
  for (let x = 4; x <= 11; x++) put(g, x, 18 + off, "p");
  put(g, 12, 18 + off, "P");
}

/**
 * Draw one leg into the grid. `lifted=true` shortens the leg so the
 * boot sits 1 row higher (mid-stride). Leg occupies cols [xLeft..xRight].
 */
function drawLeg(g: Mutable2D, xLeft: number, xRight: number, lifted: boolean): void {
  if (lifted) {
    // pants rows 19-20, boot row 21, rows 22-23 empty
    fill(g, xLeft, 19, xRight, 20, "p");
    put(g, xLeft, 19, "P");
    put(g, xRight, 19, "P");
    put(g, xLeft, 20, "P");
    put(g, xRight, 20, "P");
    // boot
    fill(g, xLeft, 21, xRight, 21, "B");
    put(g, xLeft + 1, 21, "b");
  } else {
    // pants rows 19-21, boot rows 22-23
    fill(g, xLeft, 19, xRight, 21, "p");
    put(g, xLeft, 19, "P");
    put(g, xRight, 19, "P");
    put(g, xLeft, 20, "P");
    put(g, xRight, 20, "P");
    put(g, xLeft, 21, "P");
    put(g, xRight, 21, "P");
    // boot
    fill(g, xLeft, 22, xRight, 22, "B");
    put(g, xLeft + 1, 22, "b");
    fill(g, xLeft, 23, xRight, 23, "B");
  }
}

interface FrameParams {
  leftLegLifted: boolean;
  rightLegLifted: boolean;
  bodyBob: number; // extra rows to lift head/torso (0 or 1)
}

function makeFrame(p: FrameParams): PixelGrid {
  const g = blank();
  drawHeadTorso(g, p.bodyBob);
  drawLeg(g, 4, 6, p.leftLegLifted);
  drawLeg(g, 9, 11, p.rightLegLifted);
  return toGrid(g);
}

/**
 * Four-frame walk cycle in the canonical Gen 3/4 order:
 *   0 = stand
 *   1 = right foot up, body up 1
 *   2 = stand
 *   3 = left foot up, body up 1
 */
export const WALK_FRAMES: PixelGrid[] = [
  makeFrame({ leftLegLifted: false, rightLegLifted: false, bodyBob: 0 }),
  makeFrame({ leftLegLifted: false, rightLegLifted: true, bodyBob: 1 }),
  makeFrame({ leftLegLifted: false, rightLegLifted: false, bodyBob: 0 }),
  makeFrame({ leftLegLifted: true, rightLegLifted: false, bodyBob: 1 }),
];

/** Static idle pose = walk frame 0. */
export const IDLE_FRAME: PixelGrid = WALK_FRAMES[0];

/** Map a normalized walk phase (0..1) to a walk-cycle frame index. */
export function walkPhaseToFrame(phase: number): number {
  const p = ((phase % 1) + 1) % 1;
  return Math.floor(p * WALK_FRAMES.length) % WALK_FRAMES.length;
}

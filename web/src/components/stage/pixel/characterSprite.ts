/**
 * Pokemon-style overworld character sprite.
 *
 * 16×24 front-facing, four-frame walk cycle, but now feature-driven so each
 * seed produces a visually distinct character: hair styles, headwear,
 * species ears, glasses, and facial hair are composed on top of a shared
 * face/body skeleton. All art uses abstract palette keys (see palette.ts).
 */

import type { PixelGrid } from "./types";
import { CHAR } from "./types";
import { seedHash } from "./palette";

type Cell = string;
type G = Cell[][];

// ── Feature model ─────────────────────────────────────────────────────────
export type HairStyle =
  | "short"
  | "spiky"
  | "bob"
  | "long"
  | "ponytail"
  | "buzz"
  | "messy"
  | "bald";

export type Headwear = "none" | "cap" | "beanie" | "wizardHat" | "hood";

export type EarType =
  | "none"
  | "cat"
  | "fox"
  | "rabbit"
  | "bear"
  | "panda"
  | "owl"
  | "dog"
  | "hamster";

export type FacialHair = "none" | "mustache" | "beard";

export interface CharacterFeatures {
  hairStyle: HairStyle;
  headwear: Headwear;
  ears: EarType;
  glasses: boolean;
  facialHair: FacialHair;
}

// ── grid helpers ──────────────────────────────────────────────────────────
function blank(): G {
  return Array.from({ length: CHAR.height }, () =>
    Array<Cell>(CHAR.width).fill("."),
  );
}

function put(g: G, x: number, y: number, c: Cell): void {
  if (x < 0 || x >= CHAR.width || y < 0 || y >= CHAR.height) return;
  g[y][x] = c;
}

function fill(
  g: G,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  c: Cell,
): void {
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) put(g, x, y, c);
  }
}

function toGrid(g: G): PixelGrid {
  return g.map((r) => r.join(""));
}

// ── face + body (shared skeleton) ─────────────────────────────────────────
function drawFace(g: G, off: number): void {
  for (let y = 5; y <= 10; y++) {
    for (let x = 4; x <= 11; x++) put(g, x, y + off, "S");
  }
  // eyes row 7
  put(g, 5, 7 + off, "E");
  put(g, 6, 7 + off, "E");
  put(g, 9, 7 + off, "E");
  put(g, 10, 7 + off, "E");
  // nose row 8
  put(g, 7, 8 + off, "s");
  put(g, 8, 8 + off, "s");
  // mouth row 9
  put(g, 7, 9 + off, "m");
  put(g, 8, 9 + off, "m");
  // cheek shade row 10
  put(g, 4, 10 + off, "s");
  put(g, 11, 10 + off, "s");
  // chin taper row 11
  fill(g, 4, 11 + off, 11, 11 + off, "S");
  put(g, 4, 11 + off, "s");
  put(g, 11, 11 + off, "s");
  // neck row 12
  fill(g, 6, 12 + off, 9, 12 + off, "s");
}

function drawTorso(g: G, off: number): void {
  for (let y = 13; y <= 17; y++) {
    put(g, 2, y + off, "O");
    put(g, 13, y + off, "O");
    for (let x = 3; x <= 12; x++) put(g, x, y + off, "o");
  }
  // v-neck row 13
  put(g, 7, 13 + off, "O");
  put(g, 8, 13 + off, "O");
  // shirt shade row 15
  for (let x = 3; x <= 12; x++) put(g, x, 15 + off, "O");
  for (let x = 4; x <= 11; x++) put(g, x, 15 + off, "o");
  // arm highlights
  put(g, 3, 14 + off, "o");
  put(g, 12, 14 + off, "o");
  // hands
  put(g, 2, 17 + off, "O");
  put(g, 13, 17 + off, "O");
  put(g, 2, 18 + off, "S");
  put(g, 13, 18 + off, "S");
  // belt row 18
  put(g, 3, 18 + off, "P");
  for (let x = 4; x <= 11; x++) put(g, x, 18 + off, "p");
  put(g, 12, 18 + off, "P");
}

// ── hair styles ───────────────────────────────────────────────────────────
function drawHairFrame(g: G, off: number): void {
  for (let y = 5; y <= 10; y++) {
    put(g, 3, y + off, "H");
    put(g, 12, y + off, "H");
  }
}

function drawHairShort(g: G, off: number): void {
  fill(g, 5, 1 + off, 10, 1 + off, "H");
  fill(g, 4, 2 + off, 11, 2 + off, "H");
  fill(g, 3, 3 + off, 12, 3 + off, "H");
  put(g, 6, 3 + off, "h");
  put(g, 7, 3 + off, "h");
  put(g, 8, 3 + off, "h");
  put(g, 9, 3 + off, "h");
  fill(g, 3, 4 + off, 12, 4 + off, "H");
  put(g, 4, 4 + off, "h");
  put(g, 5, 4 + off, "h");
  put(g, 10, 4 + off, "h");
  put(g, 11, 4 + off, "h");
  drawHairFrame(g, off);
}

function drawHairSpiky(g: G, off: number): void {
  put(g, 4, 1 + off, "H");
  put(g, 6, 1 + off, "H");
  put(g, 8, 1 + off, "H");
  put(g, 10, 1 + off, "H");
  fill(g, 3, 2 + off, 12, 2 + off, "H");
  put(g, 5, 2 + off, "h");
  put(g, 9, 2 + off, "h");
  fill(g, 3, 3 + off, 12, 3 + off, "H");
  put(g, 6, 3 + off, "h");
  put(g, 9, 3 + off, "h");
  fill(g, 3, 4 + off, 12, 4 + off, "H");
  put(g, 5, 4 + off, "h");
  put(g, 10, 4 + off, "h");
  drawHairFrame(g, off);
}

function drawHairBob(g: G, off: number): void {
  drawHairShort(g, off);
  // bulge rows 5-10 cols 2, 13
  for (let y = 5; y <= 10; y++) {
    put(g, 2, y + off, "H");
    put(g, 13, y + off, "H");
  }
  // tails row 11
  put(g, 3, 11 + off, "H");
  put(g, 12, 11 + off, "H");
}

function drawHairLong(g: G, off: number): void {
  fill(g, 4, 1 + off, 11, 1 + off, "H");
  fill(g, 3, 2 + off, 12, 2 + off, "H");
  fill(g, 2, 3 + off, 13, 3 + off, "H");
  put(g, 6, 3 + off, "h");
  put(g, 9, 3 + off, "h");
  fill(g, 2, 4 + off, 13, 4 + off, "H");
  put(g, 5, 4 + off, "h");
  put(g, 10, 4 + off, "h");
  // long falling sides rows 5-13
  for (let y = 5; y <= 13; y++) {
    put(g, 1, y + off, "H");
    put(g, 2, y + off, "H");
    put(g, 3, y + off, "H");
    put(g, 12, y + off, "H");
    put(g, 13, y + off, "H");
    put(g, 14, y + off, "H");
  }
  // highlights
  for (let y = 5; y <= 12; y++) {
    put(g, 2, y + off, "h");
    put(g, 13, y + off, "h");
  }
}

function drawHairPonytail(g: G, off: number): void {
  drawHairShort(g, off);
  // tuft above crown
  put(g, 7, 0 + off, "H");
  put(g, 8, 0 + off, "H");
}

function drawHairBuzz(g: G, off: number): void {
  fill(g, 4, 3 + off, 11, 3 + off, "H");
  fill(g, 4, 4 + off, 11, 4 + off, "H");
  put(g, 5, 4 + off, "h");
  put(g, 10, 4 + off, "h");
}

function drawHairMessy(g: G, off: number): void {
  put(g, 4, 1 + off, "H");
  put(g, 7, 1 + off, "H");
  put(g, 8, 1 + off, "H");
  put(g, 11, 1 + off, "H");
  fill(g, 3, 2 + off, 12, 2 + off, "H");
  put(g, 5, 2 + off, "h");
  put(g, 10, 2 + off, "h");
  fill(g, 3, 3 + off, 12, 3 + off, "H");
  put(g, 6, 3 + off, "h");
  put(g, 9, 3 + off, "h");
  fill(g, 3, 4 + off, 12, 4 + off, "H");
  put(g, 4, 4 + off, "h");
  put(g, 8, 4 + off, "h");
  put(g, 11, 4 + off, "h");
  drawHairFrame(g, off);
}

function drawHairBald(g: G, off: number): void {
  // bare skin top so the head still reads
  for (let y = 3; y <= 4; y++) {
    fill(g, 4, y + off, 11, y + off, "S");
  }
  put(g, 3, 4 + off, "s");
  put(g, 12, 4 + off, "s");
}

function drawHair(g: G, off: number, style: HairStyle): void {
  switch (style) {
    case "short":
      drawHairShort(g, off);
      return;
    case "spiky":
      drawHairSpiky(g, off);
      return;
    case "bob":
      drawHairBob(g, off);
      return;
    case "long":
      drawHairLong(g, off);
      return;
    case "ponytail":
      drawHairPonytail(g, off);
      return;
    case "buzz":
      drawHairBuzz(g, off);
      return;
    case "messy":
      drawHairMessy(g, off);
      return;
    case "bald":
      drawHairBald(g, off);
      return;
  }
}

// ── headwear ──────────────────────────────────────────────────────────────
function drawCap(g: G, off: number): void {
  fill(g, 5, 1 + off, 10, 1 + off, "K");
  fill(g, 4, 2 + off, 11, 2 + off, "K");
  fill(g, 3, 3 + off, 12, 3 + off, "K");
  put(g, 6, 3 + off, "k");
  put(g, 7, 3 + off, "k");
  put(g, 8, 3 + off, "k");
  put(g, 9, 3 + off, "k");
  // brim row 4 (wider)
  fill(g, 2, 4 + off, 13, 4 + off, "K");
  put(g, 5, 4 + off, "k");
  put(g, 10, 4 + off, "k");
}

function drawBeanie(g: G, off: number): void {
  fill(g, 5, 1 + off, 10, 1 + off, "K");
  fill(g, 4, 2 + off, 11, 2 + off, "K");
  fill(g, 3, 3 + off, 12, 3 + off, "K");
  fill(g, 3, 4 + off, 12, 4 + off, "K");
  // fold stripe
  for (let x = 4; x <= 11; x++) put(g, x, 4 + off, "k");
  // pom pom
  put(g, 7, 0 + off, "k");
  put(g, 8, 0 + off, "k");
}

function drawWizardHat(g: G, off: number): void {
  // tall pointed crown
  put(g, 7, 0 + off, "K");
  put(g, 8, 0 + off, "K");
  put(g, 6, 1 + off, "K");
  put(g, 7, 1 + off, "K");
  put(g, 8, 1 + off, "K");
  put(g, 9, 1 + off, "K");
  fill(g, 5, 2 + off, 10, 2 + off, "K");
  fill(g, 4, 3 + off, 11, 3 + off, "K");
  // wide brim
  fill(g, 1, 4 + off, 14, 4 + off, "K");
  put(g, 6, 2 + off, "k");
  put(g, 9, 3 + off, "k");
}

function drawHood(g: G, off: number): void {
  // drapes over head with outfit colours
  fill(g, 4, 1 + off, 11, 1 + off, "O");
  fill(g, 3, 2 + off, 12, 2 + off, "O");
  fill(g, 2, 3 + off, 13, 3 + off, "O");
  fill(g, 2, 4 + off, 13, 4 + off, "O");
  for (let y = 5; y <= 11; y++) {
    put(g, 2, y + off, "O");
    put(g, 3, y + off, "O");
    put(g, 12, y + off, "O");
    put(g, 13, y + off, "O");
  }
  // highlights
  put(g, 3, 2 + off, "o");
  put(g, 12, 2 + off, "o");
  for (let y = 6; y <= 10; y++) {
    put(g, 3, y + off, "o");
    put(g, 12, y + off, "o");
  }
}

function drawHeadwear(g: G, off: number, hw: Headwear): void {
  switch (hw) {
    case "cap":
      drawCap(g, off);
      return;
    case "beanie":
      drawBeanie(g, off);
      return;
    case "wizardHat":
      drawWizardHat(g, off);
      return;
    case "hood":
      drawHood(g, off);
      return;
    case "none":
      return;
  }
}

// ── ears (drawn after hair so they win the overlap) ──────────────────────
function drawCatEars(g: G, off: number): void {
  put(g, 3, 0 + off, "A");
  put(g, 3, 1 + off, "A");
  put(g, 4, 1 + off, "A");
  put(g, 4, 0 + off, "a");
  put(g, 12, 0 + off, "A");
  put(g, 11, 1 + off, "A");
  put(g, 12, 1 + off, "A");
  put(g, 11, 0 + off, "a");
}

function drawFoxEars(g: G, off: number): void {
  put(g, 3, 0 + off, "A");
  put(g, 3, 1 + off, "A");
  put(g, 4, 1 + off, "A");
  put(g, 3, 2 + off, "A");
  put(g, 4, 2 + off, "A");
  put(g, 5, 2 + off, "A");
  put(g, 4, 1 + off, "a");
  put(g, 12, 0 + off, "A");
  put(g, 11, 1 + off, "A");
  put(g, 12, 1 + off, "A");
  put(g, 10, 2 + off, "A");
  put(g, 11, 2 + off, "A");
  put(g, 12, 2 + off, "A");
  put(g, 11, 1 + off, "a");
}

function drawRabbitEars(g: G, off: number): void {
  for (let y = 0; y <= 3; y++) {
    put(g, 5, y + off, "A");
    put(g, 10, y + off, "A");
  }
  put(g, 5, 1 + off, "a");
  put(g, 10, 1 + off, "a");
}

function drawBearEars(g: G, off: number): void {
  put(g, 3, 0 + off, "A");
  put(g, 4, 0 + off, "A");
  put(g, 3, 1 + off, "A");
  put(g, 4, 1 + off, "A");
  put(g, 4, 0 + off, "a");
  put(g, 11, 0 + off, "A");
  put(g, 12, 0 + off, "A");
  put(g, 11, 1 + off, "A");
  put(g, 12, 1 + off, "A");
  put(g, 11, 0 + off, "a");
}

function drawOwlEars(g: G, off: number): void {
  put(g, 4, 0 + off, "A");
  put(g, 4, 1 + off, "A");
  put(g, 11, 0 + off, "A");
  put(g, 11, 1 + off, "A");
}

function drawDogEars(g: G, off: number): void {
  // floppy, hanging down the sides
  for (let y = 2; y <= 6; y++) {
    put(g, 2, y + off, "A");
    put(g, 13, y + off, "A");
  }
  put(g, 2, 2 + off, "a");
  put(g, 13, 2 + off, "a");
}

function drawHamsterEars(g: G, off: number): void {
  put(g, 4, 0 + off, "A");
  put(g, 5, 0 + off, "A");
  put(g, 10, 0 + off, "A");
  put(g, 11, 0 + off, "A");
  put(g, 5, 0 + off, "a");
  put(g, 10, 0 + off, "a");
}

function drawEars(g: G, off: number, ears: EarType): void {
  switch (ears) {
    case "cat":
      drawCatEars(g, off);
      return;
    case "fox":
      drawFoxEars(g, off);
      return;
    case "rabbit":
      drawRabbitEars(g, off);
      return;
    case "bear":
    case "panda":
      drawBearEars(g, off);
      return;
    case "owl":
      drawOwlEars(g, off);
      return;
    case "dog":
      drawDogEars(g, off);
      return;
    case "hamster":
      drawHamsterEars(g, off);
      return;
    case "none":
      return;
  }
}

// ── facial accessories ───────────────────────────────────────────────────
function drawGlasses(g: G, off: number): void {
  // left lens around col 5-6, right lens around col 9-10, bridge row 7
  put(g, 4, 7 + off, "#");
  put(g, 7, 7 + off, "#");
  put(g, 5, 6 + off, "#");
  put(g, 6, 6 + off, "#");
  put(g, 5, 8 + off, "#");
  put(g, 6, 8 + off, "#");
  put(g, 8, 7 + off, "#");
  put(g, 11, 7 + off, "#");
  put(g, 9, 6 + off, "#");
  put(g, 10, 6 + off, "#");
  put(g, 9, 8 + off, "#");
  put(g, 10, 8 + off, "#");
}

function drawMustache(g: G, off: number): void {
  put(g, 5, 9 + off, "H");
  put(g, 6, 9 + off, "H");
  put(g, 9, 9 + off, "H");
  put(g, 10, 9 + off, "H");
}

function drawBeard(g: G, off: number): void {
  fill(g, 4, 10 + off, 11, 10 + off, "H");
  fill(g, 4, 11 + off, 11, 11 + off, "H");
  // preserve the mouth so the face reads
  put(g, 7, 9 + off, "m");
  put(g, 8, 9 + off, "m");
}

// ── legs (never bob) ─────────────────────────────────────────────────────
function drawLeg(g: G, xLeft: number, xRight: number, lifted: boolean): void {
  if (lifted) {
    fill(g, xLeft, 19, xRight, 20, "p");
    put(g, xLeft, 19, "P");
    put(g, xRight, 19, "P");
    put(g, xLeft, 20, "P");
    put(g, xRight, 20, "P");
    fill(g, xLeft, 21, xRight, 21, "B");
    put(g, xLeft + 1, 21, "b");
  } else {
    fill(g, xLeft, 19, xRight, 21, "p");
    put(g, xLeft, 19, "P");
    put(g, xRight, 19, "P");
    put(g, xLeft, 20, "P");
    put(g, xRight, 20, "P");
    put(g, xLeft, 21, "P");
    put(g, xRight, 21, "P");
    fill(g, xLeft, 22, xRight, 22, "B");
    put(g, xLeft + 1, 22, "b");
    fill(g, xLeft, 23, xRight, 23, "B");
  }
}

// ── frame assembly ───────────────────────────────────────────────────────
interface FrameParams {
  leftLegLifted: boolean;
  rightLegLifted: boolean;
  bodyBob: number;
  features: CharacterFeatures;
}

function makeFrame(p: FrameParams): PixelGrid {
  const g = blank();
  const off = -p.bodyBob;
  const f = p.features;

  drawFace(g, off);

  if (f.headwear === "none") {
    drawHair(g, off, f.hairStyle);
  } else if (f.headwear === "hood" || f.hairStyle === "bald") {
    // hood covers everything; bald stays bald under headwear
  } else {
    // keep side strands peeking out from under cap/beanie/wizardHat
    drawHairFrame(g, off);
    if (f.hairStyle === "long") {
      for (let y = 5; y <= 13; y++) {
        put(g, 2, y + off, "H");
        put(g, 13, y + off, "H");
      }
    }
  }

  drawHeadwear(g, off, f.headwear);
  drawEars(g, off, f.ears);

  if (f.facialHair === "mustache") drawMustache(g, off);
  else if (f.facialHair === "beard") drawBeard(g, off);

  if (f.glasses) drawGlasses(g, off);

  drawTorso(g, off);
  drawLeg(g, 4, 6, p.leftLegLifted);
  drawLeg(g, 9, 11, p.rightLegLifted);
  return toGrid(g);
}

export function buildFrames(features: CharacterFeatures): PixelGrid[] {
  return [
    makeFrame({
      leftLegLifted: false,
      rightLegLifted: false,
      bodyBob: 0,
      features,
    }),
    makeFrame({
      leftLegLifted: false,
      rightLegLifted: true,
      bodyBob: 1,
      features,
    }),
    makeFrame({
      leftLegLifted: false,
      rightLegLifted: false,
      bodyBob: 0,
      features,
    }),
    makeFrame({
      leftLegLifted: true,
      rightLegLifted: false,
      bodyBob: 1,
      features,
    }),
  ];
}

// Default-feature frames kept for backwards compatibility.
const DEFAULT_FEATURES: CharacterFeatures = {
  hairStyle: "short",
  headwear: "none",
  ears: "none",
  glasses: false,
  facialHair: "none",
};

export const WALK_FRAMES: PixelGrid[] = buildFrames(DEFAULT_FEATURES);
export const IDLE_FRAME: PixelGrid = WALK_FRAMES[0];

export function walkPhaseToFrame(phase: number): number {
  const p = ((phase % 1) + 1) % 1;
  return Math.floor(p * WALK_FRAMES.length) % WALK_FRAMES.length;
}

// ── seed-driven feature resolution ───────────────────────────────────────
const HAIR_STYLES: HairStyle[] = [
  "short",
  "spiky",
  "bob",
  "long",
  "ponytail",
  "buzz",
  "messy",
  "bald",
];

function speciesEars(species?: string): EarType {
  const k = (species ?? "human").toLowerCase();
  if (k === "human") return "none";
  if (k.includes("cat") || k === "tiger" || k === "lion") return "cat";
  if (k.includes("fox") || k === "wolf" || k === "kitsune") return "fox";
  if (k.includes("rabbit") || k === "hare" || k === "jackalope") return "rabbit";
  if (k.includes("panda")) return "panda";
  if (k.includes("bear") || k === "grizzly") return "bear";
  if (k.includes("owl") || k === "eagle" || k === "phoenix") return "owl";
  if (k.includes("dog") || k === "husky") return "dog";
  if (k.includes("hamster") || k === "capybara" || k === "chinchilla")
    return "hamster";
  return "none";
}

export function resolveFeatures(
  seed: string,
  species?: string,
  role?: string,
): CharacterFeatures {
  const h = seedHash(seed + "_features");
  const ears = speciesEars(species);
  const hairStyle = HAIR_STYLES[h % HAIR_STYLES.length];

  const hwRoll = (h >>> 4) & 0xff;
  const roleKey = (role ?? "").toLowerCase();
  let headwear: Headwear = "none";
  if (roleKey.includes("director") || roleKey.includes("lead")) {
    if (hwRoll < 140) headwear = "cap";
  } else if (roleKey.includes("writer") || roleKey.includes("designer")) {
    if (hwRoll < 80) headwear = "wizardHat";
    else if (hwRoll < 140) headwear = "beanie";
  } else if (roleKey.includes("reviewer") || roleKey.includes("tester")) {
    if (hwRoll < 80) headwear = "cap";
    else if (hwRoll < 140) headwear = "beanie";
  } else {
    if (hwRoll < 50) headwear = "beanie";
    else if (hwRoll < 80) headwear = "cap";
    else if (hwRoll < 100) headwear = "hood";
  }

  const glasses = ((h >>> 12) & 0x3) === 0; // ~25%

  const facialHairRoll = (h >>> 16) & 0xff;
  let facialHair: FacialHair = "none";
  if (facialHairRoll < 28) facialHair = "mustache";
  else if (facialHairRoll < 56) facialHair = "beard";

  return { hairStyle, headwear, ears, glasses, facialHair };
}

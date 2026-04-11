/**
 * Map layouts for the 20×12 tile stage (320×192 logical pixels).
 *
 * A map is: (a) a tile grid for the ground/floor art, (b) a painter-ordered
 * list of props drawn above it, and (c) a pixel-resolution `walkable` grid
 * (0 = blocked, 1 = walkable) computed from tile kinds and per-object
 * collision footprints. The motion hook queries the walkable grid to steer
 * characters around obstacles.
 *
 * The default theme is "hq" — a top-down cutaway of the swarm's
 * headquarters with three distinct interior rooms (coder lab, war room,
 * design lounge) separated by brick walls, connected by door openings.
 */

import { STAGE } from "./types";
import type { TileKind } from "./tileSprites";

export type MapTheme =
  | "hq"
  | "meadow"
  | "forest"
  | "town";

export type ObjectKind =
  // exterior
  | "tree"
  | "bush"
  | "rock"
  | "houseRed"
  | "houseBlue"
  | "houseGreen"
  | "sign"
  | "lamp"
  | "flowerPatch"
  | "fence"
  | "fountain"
  // interior
  | "desk"
  | "chair"
  | "bookshelf"
  | "meetingTable"
  | "whiteboard"
  | "couch"
  | "plant"
  | "crate"
  | "computer"
  | "door"
  | "bed";

export interface SceneObject {
  kind: ObjectKind;
  /** bottom-center anchor x in pixels */
  x: number;
  /** bottom anchor y in pixels */
  y: number;
  /** extra data (e.g. flower patch colour) */
  color?: string;
  /** only used by lamp: whether it is lit (night) */
  lit?: boolean;
}

export interface MapLayout {
  tiles: TileKind[][]; // [row][col]
  objects: SceneObject[];
  /** Pixel-resolution walkable mask. 1 = walkable, 0 = blocked. */
  walkable: Uint8Array;
  width: number;
  height: number;
  /** If true, the map is an interior — skip sky gradient in the renderer. */
  interior: boolean;
}

// ── collision footprints ─────────────────────────────────────────────────
interface Footprint {
  w: number;
  h: number;
  blocks: boolean;
}

function footprintFor(kind: ObjectKind): Footprint {
  switch (kind) {
    case "tree":
      return { w: 6, h: 4, blocks: true };
    case "bush":
      return { w: 12, h: 4, blocks: true };
    case "rock":
      return { w: 12, h: 4, blocks: true };
    case "houseRed":
    case "houseBlue":
    case "houseGreen":
      return { w: 36, h: 24, blocks: true };
    case "sign":
      return { w: 8, h: 4, blocks: true };
    case "lamp":
      return { w: 4, h: 4, blocks: true };
    case "fence":
      return { w: 14, h: 6, blocks: true };
    case "fountain":
      return { w: 28, h: 10, blocks: true };
    case "flowerPatch":
      return { w: 10, h: 2, blocks: false };
    // interior
    case "desk":
      return { w: 20, h: 7, blocks: true };
    case "chair":
      return { w: 8, h: 4, blocks: true };
    case "bookshelf":
      return { w: 18, h: 5, blocks: true };
    case "meetingTable":
      return { w: 28, h: 10, blocks: true };
    case "whiteboard":
      // mounted on wall — don't block the floor in front of it
      return { w: 28, h: 2, blocks: false };
    case "couch":
      return { w: 24, h: 6, blocks: true };
    case "plant":
      return { w: 8, h: 4, blocks: true };
    case "crate":
      return { w: 12, h: 8, blocks: true };
    case "computer":
      return { w: 10, h: 5, blocks: true };
    case "door":
      // door is just a visual — the walkable gap in the wall handles passage
      return { w: 10, h: 2, blocks: false };
    case "bed":
      return { w: 24, h: 10, blocks: true };
  }
}

// ── walkable-grid helpers ────────────────────────────────────────────────
function makeWalkable(): Uint8Array {
  const W = STAGE.width;
  const H = STAGE.height;
  return new Uint8Array(W * H);
}

function isTileWalkable(kind: TileKind): boolean {
  switch (kind) {
    case "sky":
    case "water":
    case "waterEdge":
    case "wallTop":
    case "wallFront":
    case "roofTile":
      return false;
    default:
      return true;
  }
}

function stampTileGrid(
  walk: Uint8Array,
  tiles: TileKind[][],
  skyRows: number,
): void {
  const W = STAGE.width;
  const T = STAGE.tile;
  for (let r = 0; r < STAGE.rows; r++) {
    const row = tiles[r];
    for (let c = 0; c < STAGE.cols; c++) {
      const w = isTileWalkable(row[c]) ? 1 : 0;
      if (!w) continue;
      if (r < skyRows) continue;
      const x0 = c * T;
      const y0 = r * T;
      for (let y = 0; y < T; y++) {
        const base = (y0 + y) * W + x0;
        for (let x = 0; x < T; x++) walk[base + x] = 1;
      }
    }
  }
}

function stampObstacle(walk: Uint8Array, obj: SceneObject): void {
  const fp = footprintFor(obj.kind);
  if (!fp.blocks) return;
  const W = STAGE.width;
  const H = STAGE.height;
  const x0 = Math.max(0, Math.round(obj.x - fp.w / 2));
  const x1 = Math.min(W, x0 + fp.w);
  const y0 = Math.max(0, obj.y - fp.h);
  const y1 = Math.min(H, obj.y);
  for (let y = y0; y < y1; y++) {
    const base = y * W;
    for (let x = x0; x < x1; x++) walk[base + x] = 0;
  }
}

interface BuildOpts {
  interior?: boolean;
  skyRows?: number;
}

function buildLayout(
  tiles: TileKind[][],
  objects: SceneObject[],
  opts: BuildOpts = {},
): MapLayout {
  const walkable = makeWalkable();
  const skyRows = opts.skyRows ?? (opts.interior ? 0 : STAGE.horizonRow);
  stampTileGrid(walkable, tiles, skyRows);
  for (const obj of objects) stampObstacle(walkable, obj);
  return {
    tiles,
    objects,
    walkable,
    width: STAGE.width,
    height: STAGE.height,
    interior: opts.interior ?? false,
  };
}

/** True if the given pixel is inside the walkable area of the map. */
export function isWalkable(
  map: MapLayout,
  px: number,
  py: number,
): boolean {
  if (px < 0 || py < 0 || px >= map.width || py >= map.height) return false;
  return map.walkable[py * map.width + px] === 1;
}

function fillGrid(kind: TileKind): TileKind[][] {
  return Array.from({ length: STAGE.rows }, () =>
    Array.from({ length: STAGE.cols }, () => kind),
  );
}

// ═══════════════════════════════════════════════════════════════════════
// HQ — multi-room interior (DEFAULT)
// ═══════════════════════════════════════════════════════════════════════
//
// Layout (20 cols × 12 rows):
//
//  row 0  ██████████████████████  roof tiles
//  row 1  ██████████████████████  roof tiles
//  row 2  ░░░░░░░║░░░░░║░░░░░░░░  wallTop (top of interior walls)
//  row 3  [wood ]║[tile]║[carpet] — rooms start
//  row 4  [desks]║[tabl]║[couch ]
//  row 5  [     ]║[e   ]║[       ]
//  row 6  [     ]║[    ]║[       ]
//  row 7  [     ]║[    ]║[       ]
//  row 8  [     ]║[    ]║[       ]
//  row 9  [ shelf]║[ whiteboard]║[plant] — wall decos
//  row 10 ████[ ]█████[ ]█████[ ]██ — bottom wall with door gaps
//  row 11 ▓▓▓▓[ ]▓▓▓▓▓[ ]▓▓▓▓▓[ ]▓▓ — doormat strip in front of each door
//
// Vertical walls sit at cols 7 and 13. Doors cut out openings at
// specific rows so agents can travel between rooms.

function buildHq(): MapLayout {
  const tiles = fillGrid("floorWood");

  // ── Roof band (rows 0-1) ─────────────────────────────────
  for (let c = 0; c < STAGE.cols; c++) {
    tiles[0][c] = "roofTile";
    tiles[1][c] = "roofTile";
  }
  // ── Top interior wall (row 2) ────────────────────────────
  for (let c = 0; c < STAGE.cols; c++) tiles[2][c] = "wallTop";

  // ── Room floors ──────────────────────────────────────────
  // Room A (cols 0..6): Coder Lab — wood floor
  // Room B (cols 8..12): War Room — tile floor
  // Room C (cols 14..19): Design Lounge — carpet
  for (let r = 3; r <= 9; r++) {
    for (let c = 0; c <= 6; c++) tiles[r][c] = "floorWood";
    for (let c = 8; c <= 12; c++) tiles[r][c] = "floorTile";
    for (let c = 14; c <= 19; c++) tiles[r][c] = "carpet";
  }

  // ── Vertical interior walls (cols 7, 13) ─────────────────
  // Solid walls from row 3..9 with a walkable door gap at row 6-7.
  for (let r = 3; r <= 9; r++) {
    tiles[r][7] = "wallFront";
    tiles[r][13] = "wallFront";
  }
  // Door gap: walkable floor at row 6-7 on both inner walls
  tiles[6][7] = "floorWood";
  tiles[7][7] = "floorTile";
  tiles[6][13] = "floorTile";
  tiles[7][13] = "carpet";

  // ── Bottom wall with door gaps (row 10) ──────────────────
  for (let c = 0; c < STAGE.cols; c++) tiles[10][c] = "wallFront";
  // carve three doorways
  for (const dc of [3, 10, 16]) {
    tiles[10][dc] = "floorWood";
  }
  // ── Door mat strip (row 11) in front of each door ───────
  for (let c = 0; c < STAGE.cols; c++) tiles[11][c] = "wallFront";
  for (const dc of [3, 10, 16]) tiles[11][dc] = "doormat";

  // ── Objects ──────────────────────────────────────────────
  const objects: SceneObject[] = [];

  // --- Room A: Coder Lab ---
  // two desk+chair pairs along the back
  // Desk anchor is bottom-center; desk is 20w × 16h. Back row ~ y=90
  objects.push({ kind: "desk", x: 28, y: 76 });
  objects.push({ kind: "chair", x: 28, y: 86 });
  objects.push({ kind: "desk", x: 84, y: 76 });
  objects.push({ kind: "chair", x: 84, y: 86 });
  // a bookshelf against the left wall
  objects.push({ kind: "bookshelf", x: 10, y: 118 });
  // a stack of crates in the corner
  objects.push({ kind: "crate", x: 104, y: 140 });
  objects.push({ kind: "crate", x: 104, y: 128 });
  // extra computer on a side table (just a computer sprite)
  objects.push({ kind: "computer", x: 60, y: 128 });

  // --- Room B: War Room ---
  // big meeting table in the centre of the room
  // Room B spans cols 8..12 = px 128..207. Centre ~x=168, bottom ~y=130
  objects.push({ kind: "meetingTable", x: 168, y: 136 });
  // chairs around the table
  objects.push({ kind: "chair", x: 144, y: 140 });
  objects.push({ kind: "chair", x: 192, y: 140 });
  objects.push({ kind: "chair", x: 168, y: 148 });
  // whiteboard mounted near the top wall
  objects.push({ kind: "whiteboard", x: 168, y: 64 });
  // small plant in the corner
  objects.push({ kind: "plant", x: 138, y: 90 });

  // --- Room C: Design Lounge ---
  // Room C spans cols 14..19 = px 224..319
  objects.push({ kind: "couch", x: 266, y: 140 });
  objects.push({ kind: "plant", x: 238, y: 90 });
  objects.push({ kind: "plant", x: 304, y: 90 });
  // a bed (for all-nighters)
  objects.push({ kind: "bed", x: 266, y: 90 });
  // bookshelf on the right wall
  objects.push({ kind: "bookshelf", x: 310, y: 140 });

  // --- Door visuals on the bottom wall ---
  // The doorways are at cols 3, 10, 16 → x = 56, 168, 264
  objects.push({ kind: "door", x: 56, y: 176 });
  objects.push({ kind: "door", x: 168, y: 176 });
  objects.push({ kind: "door", x: 264, y: 176 });

  return buildLayout(tiles, objects, { interior: true, skyRows: 2 });
}

// ═══════════════════════════════════════════════════════════════════════
// MEADOW / FOREST / TOWN (legacy exterior themes — kept for reference)
// ═══════════════════════════════════════════════════════════════════════

function buildMeadowTiles(): TileKind[][] {
  const t = fillGrid("sky");
  const HZ = STAGE.horizonRow;

  for (let r = HZ; r < STAGE.rows; r++) {
    for (let c = 0; c < STAGE.cols; c++) t[r][c] = "grass";
  }
  for (let x = 0; x < STAGE.cols; x++) t[HZ][x] = "grassDark";

  const pathRow = STAGE.rows - 3;
  for (let x = 0; x < STAGE.cols; x++) {
    t[pathRow][x] = "path";
    if (pathRow - 1 >= HZ) t[pathRow - 1][x] = "pathEdge";
  }
  for (let r = HZ + 1; r < pathRow; r++) {
    t[r][10] = "path";
    if (10 > 0) t[r][9] = "pathEdge";
  }

  for (const [r, c] of [
    [HZ + 1, 3],
    [HZ + 1, 15],
    [HZ + 2, 6],
    [HZ + 2, 13],
    [pathRow + 1, 4],
    [pathRow + 1, 15],
  ]) {
    if (r < STAGE.rows) t[r][c] = "flowerGrass";
  }

  const pondCells: Array<[number, number]> = [
    [HZ + 1, 16],
    [HZ + 1, 17],
    [HZ + 1, 18],
    [HZ + 2, 15],
    [HZ + 2, 16],
    [HZ + 2, 17],
    [HZ + 2, 18],
    [HZ + 2, 19],
  ];
  for (const [r, c] of pondCells) {
    if (r < STAGE.rows && c < STAGE.cols) t[r][c] = "water";
  }

  return t;
}

function buildMeadow(): MapLayout {
  const tiles = buildMeadowTiles();
  const objects: SceneObject[] = [];
  const horizon = STAGE.horizonRow * STAGE.tile;
  const backY = horizon + 22;
  const midY = horizon + 52;
  const frontY = STAGE.groundY + 24;

  objects.push({ kind: "tree", x: 14, y: backY });
  objects.push({ kind: "tree", x: 42, y: backY + 3 });
  objects.push({ kind: "tree", x: 78, y: backY });
  objects.push({ kind: "tree", x: 110, y: backY + 2 });
  objects.push({ kind: "tree", x: 144, y: backY });
  objects.push({ kind: "fountain", x: 168, y: midY });
  objects.push({ kind: "lamp", x: 136, y: midY + 4 });
  objects.push({ kind: "lamp", x: 200, y: midY + 4 });
  objects.push({ kind: "bush", x: 62, y: midY + 4 });
  objects.push({ kind: "bush", x: 108, y: midY + 4 });
  objects.push({ kind: "rock", x: 82, y: midY + 6 });
  objects.push({ kind: "rock", x: 216, y: midY + 6 });
  objects.push({ kind: "flowerPatch", x: 30, y: frontY, color: "#ff6a8a" });
  objects.push({ kind: "flowerPatch", x: 64, y: frontY, color: "#ffe24a" });
  objects.push({ kind: "flowerPatch", x: 102, y: frontY, color: "#a85aff" });
  objects.push({ kind: "flowerPatch", x: 216, y: frontY, color: "#ff9140" });
  objects.push({ kind: "flowerPatch", x: 254, y: frontY, color: "#ffffff" });
  objects.push({ kind: "flowerPatch", x: 294, y: frontY, color: "#ff6a8a" });
  objects.push({ kind: "sign", x: 18, y: STAGE.groundY - 4 });

  return buildLayout(tiles, objects);
}

function buildForest(): MapLayout {
  const tiles = buildMeadowTiles();
  for (let r = 0; r < STAGE.rows; r++) {
    for (let c = 0; c < STAGE.cols; c++) {
      if (tiles[r][c] === "grass" || tiles[r][c] === "flowerGrass") {
        tiles[r][c] = "grassDark";
      }
    }
  }
  const objects: SceneObject[] = [];
  const horizon = STAGE.horizonRow * STAGE.tile;
  const backY = horizon + 22;
  const midY = horizon + 52;
  const frontY = STAGE.groundY + 24;

  for (let i = 0; i < 10; i++) {
    const x = 14 + i * 32;
    if (Math.abs(x - 160) < 18) continue;
    objects.push({ kind: "tree", x, y: backY + ((i * 5) % 4) });
  }
  objects.push({ kind: "bush", x: 40, y: midY + 4 });
  objects.push({ kind: "bush", x: 96, y: midY + 2 });
  objects.push({ kind: "bush", x: 228, y: midY + 4 });
  objects.push({ kind: "bush", x: 286, y: midY + 2 });
  objects.push({ kind: "tree", x: 70, y: midY + 10 });
  objects.push({ kind: "tree", x: 252, y: midY + 10 });
  objects.push({ kind: "rock", x: 120, y: midY + 6 });
  objects.push({ kind: "rock", x: 198, y: midY + 6 });
  objects.push({ kind: "lamp", x: 160, y: midY + 4 });
  objects.push({ kind: "flowerPatch", x: 44, y: frontY, color: "#ff7a9e" });
  objects.push({ kind: "flowerPatch", x: 282, y: frontY, color: "#ffe24a" });

  return buildLayout(tiles, objects);
}

function buildTown(): MapLayout {
  const tiles = buildMeadowTiles();
  const plazaRows = [STAGE.horizonRow + 1, STAGE.horizonRow + 2];
  for (const r of plazaRows) {
    for (let c = 1; c < STAGE.cols - 1; c++) tiles[r][c] = "stone";
  }

  const objects: SceneObject[] = [];
  const horizon = STAGE.horizonRow * STAGE.tile;
  const houseY = horizon + 44;
  const midY = horizon + 60;
  const frontY = STAGE.groundY + 22;

  objects.push({ kind: "houseRed", x: 46, y: houseY });
  objects.push({ kind: "houseBlue", x: 160, y: houseY });
  objects.push({ kind: "houseGreen", x: 274, y: houseY });
  objects.push({ kind: "lamp", x: 82, y: houseY + 6 });
  objects.push({ kind: "lamp", x: 196, y: houseY + 6 });
  objects.push({ kind: "lamp", x: 248, y: houseY + 6 });
  objects.push({ kind: "fence", x: 98, y: houseY - 2 });
  objects.push({ kind: "fence", x: 114, y: houseY - 2 });
  objects.push({ kind: "fence", x: 208, y: houseY - 2 });
  objects.push({ kind: "fence", x: 224, y: houseY - 2 });
  objects.push({ kind: "tree", x: 14, y: houseY + 6 });
  objects.push({ kind: "tree", x: 308, y: houseY + 6 });
  objects.push({ kind: "rock", x: 120, y: midY + 4 });
  objects.push({ kind: "rock", x: 200, y: midY + 4 });
  objects.push({ kind: "bush", x: 22, y: frontY });
  objects.push({ kind: "bush", x: 302, y: frontY });
  objects.push({ kind: "flowerPatch", x: 140, y: frontY, color: "#ff7a9e" });
  objects.push({ kind: "flowerPatch", x: 188, y: frontY, color: "#ffe24a" });
  objects.push({ kind: "sign", x: 18, y: STAGE.groundY - 4 });

  return buildLayout(tiles, objects);
}

export function buildMap(theme: MapTheme): MapLayout {
  switch (theme) {
    case "meadow":
      return buildMeadow();
    case "forest":
      return buildForest();
    case "town":
      return buildTown();
    case "hq":
    default:
      return buildHq();
  }
}

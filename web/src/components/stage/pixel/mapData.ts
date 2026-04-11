/**
 * Map layout for the 20×12 tile stage (320×192 logical pixels).
 *
 * A map is: (a) a tile grid for the ground art, (b) a painter-ordered
 * list of props drawn above it, and (c) a pixel-resolution `walkable`
 * grid (0 = blocked, 1 = walkable) computed from tile kinds and per-
 * object collision footprints. The motion hook queries the walkable
 * grid to steer characters around obstacles and keep them on ground.
 */

import { STAGE } from "./types";
import type { TileKind } from "./tileSprites";

export type MapTheme = "meadow" | "forest" | "town";

export type ObjectKind =
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
  | "fountain";

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
}

// ── collision footprints ─────────────────────────────────────────────────
// Each object has a ground footprint (bottom rectangle in local object
// coordinates) that blocks walking. Decorative objects (flower patches,
// bushes) are passable. The footprint is smaller than the sprite for
// props like trees where the canopy is above the walk plane.
interface Footprint {
  /** width in pixels, centred on x */
  w: number;
  /** height in pixels, rising from y */
  h: number;
  /** if true, the footprint blocks walking */
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
      return false;
    case "water":
    case "waterEdge":
      return false;
    default:
      return true;
  }
}

function stampTileGrid(walk: Uint8Array, tiles: TileKind[][]): void {
  const W = STAGE.width;
  const T = STAGE.tile;
  for (let r = 0; r < STAGE.rows; r++) {
    const row = tiles[r];
    for (let c = 0; c < STAGE.cols; c++) {
      const w = isTileWalkable(row[c]) ? 1 : 0;
      if (!w) continue;
      // Only the ground portion of the map is walkable at all — sky rows
      // (above horizon) stay zero regardless of tile kind.
      if (r < STAGE.horizonRow) continue;
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

function buildLayout(tiles: TileKind[][], objects: SceneObject[]): MapLayout {
  const walkable = makeWalkable();
  stampTileGrid(walkable, tiles);
  for (const obj of objects) stampObstacle(walkable, obj);
  return {
    tiles,
    objects,
    walkable,
    width: STAGE.width,
    height: STAGE.height,
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

/** Build a fresh tile grid filled with a single kind. */
function fillGrid(kind: TileKind): TileKind[][] {
  return Array.from({ length: STAGE.rows }, () =>
    Array.from({ length: STAGE.cols }, () => kind),
  );
}

// ── meadow base ──────────────────────────────────────────────────────────
function buildMeadowTiles(): TileKind[][] {
  const t = fillGrid("sky");
  const HZ = STAGE.horizonRow;

  for (let r = HZ; r < STAGE.rows; r++) {
    for (let c = 0; c < STAGE.cols; c++) t[r][c] = "grass";
  }
  for (let x = 0; x < STAGE.cols; x++) t[HZ][x] = "grassDark";

  // A winding dirt path that cuts horizontally and branches north
  const pathRow = STAGE.rows - 3; // row 9 — higher up so there's room below
  for (let x = 0; x < STAGE.cols; x++) {
    t[pathRow][x] = "path";
    if (pathRow - 1 >= HZ) t[pathRow - 1][x] = "pathEdge";
  }
  // northward branch in the middle
  for (let r = HZ + 1; r < pathRow; r++) {
    t[r][10] = "path";
    if (10 > 0) t[r][9] = "pathEdge";
  }

  // flower-grass scatter for variety
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

  // small pond in the far-back-right
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
  for (const c of [15, 16, 17, 18, 19]) {
    if (HZ + 2 < STAGE.rows) t[HZ + 2][c] = "waterEdge";
  }
  for (const [r, c] of [
    [HZ, 16],
    [HZ, 17],
    [HZ, 18],
    [HZ + 1, 15],
    [HZ + 1, 19],
  ]) {
    if (r < STAGE.rows && c < STAGE.cols) t[r][c] = "sand";
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

  // back-row trees (avoid the pond area cols ~15..19)
  objects.push({ kind: "tree", x: 14, y: backY });
  objects.push({ kind: "tree", x: 42, y: backY + 3 });
  objects.push({ kind: "tree", x: 78, y: backY });
  objects.push({ kind: "tree", x: 110, y: backY + 2 });
  objects.push({ kind: "tree", x: 144, y: backY });

  // fountain centre
  objects.push({ kind: "fountain", x: 168, y: midY });
  objects.push({ kind: "lamp", x: 136, y: midY + 4 });
  objects.push({ kind: "lamp", x: 200, y: midY + 4 });

  // mid bushes + rocks
  objects.push({ kind: "bush", x: 62, y: midY + 4 });
  objects.push({ kind: "bush", x: 108, y: midY + 4 });
  objects.push({ kind: "rock", x: 82, y: midY + 6 });
  objects.push({ kind: "rock", x: 216, y: midY + 6 });

  // front-row flora (below characters)
  objects.push({ kind: "flowerPatch", x: 30, y: frontY, color: "#ff6a8a" });
  objects.push({ kind: "flowerPatch", x: 64, y: frontY, color: "#ffe24a" });
  objects.push({ kind: "flowerPatch", x: 102, y: frontY, color: "#a85aff" });
  objects.push({ kind: "flowerPatch", x: 216, y: frontY, color: "#ff9140" });
  objects.push({ kind: "flowerPatch", x: 254, y: frontY, color: "#ffffff" });
  objects.push({ kind: "flowerPatch", x: 294, y: frontY, color: "#ff6a8a" });

  // signpost at the path start
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

  // dense back-row canopy — keep a clear corridor in the middle for the path
  for (let i = 0; i < 10; i++) {
    const x = 14 + i * 32;
    if (Math.abs(x - 160) < 18) continue;
    objects.push({ kind: "tree", x, y: backY + ((i * 5) % 4) });
  }
  // scattered mid-ground obstacles
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
  // swap back-row grass strip for a stone plaza in front of the houses
  const plazaRows = [STAGE.horizonRow + 1, STAGE.horizonRow + 2];
  for (const r of plazaRows) {
    for (let c = 1; c < STAGE.cols - 1; c++) tiles[r][c] = "stone";
  }

  const objects: SceneObject[] = [];
  const horizon = STAGE.horizonRow * STAGE.tile;
  const houseY = horizon + 44;
  const midY = horizon + 60;
  const frontY = STAGE.groundY + 22;

  // three houses across the back with gaps for the agents to walk through
  objects.push({ kind: "houseRed", x: 46, y: houseY });
  objects.push({ kind: "houseBlue", x: 160, y: houseY });
  objects.push({ kind: "houseGreen", x: 274, y: houseY });

  // lamps flanking each house
  objects.push({ kind: "lamp", x: 82, y: houseY + 6 });
  objects.push({ kind: "lamp", x: 196, y: houseY + 6 });
  objects.push({ kind: "lamp", x: 248, y: houseY + 6 });

  // low fence rails between the houses (with gaps at centre cols for walk-through)
  objects.push({ kind: "fence", x: 98, y: houseY - 2 });
  objects.push({ kind: "fence", x: 114, y: houseY - 2 });
  objects.push({ kind: "fence", x: 208, y: houseY - 2 });
  objects.push({ kind: "fence", x: 224, y: houseY - 2 });

  // corner trees
  objects.push({ kind: "tree", x: 14, y: houseY + 6 });
  objects.push({ kind: "tree", x: 308, y: houseY + 6 });

  // street benches (repurpose rocks as stools near lamps)
  objects.push({ kind: "rock", x: 120, y: midY + 4 });
  objects.push({ kind: "rock", x: 200, y: midY + 4 });

  // front bushes + flowers
  objects.push({ kind: "bush", x: 22, y: frontY });
  objects.push({ kind: "bush", x: 302, y: frontY });
  objects.push({ kind: "flowerPatch", x: 140, y: frontY, color: "#ff7a9e" });
  objects.push({ kind: "flowerPatch", x: 188, y: frontY, color: "#ffe24a" });

  // signpost at path start
  objects.push({ kind: "sign", x: 18, y: STAGE.groundY - 4 });

  return buildLayout(tiles, objects);
}

export function buildMap(theme: MapTheme): MapLayout {
  switch (theme) {
    case "forest":
      return buildForest();
    case "town":
      return buildTown();
    default:
      return buildMeadow();
  }
}

/**
 * Map layout for the 20×12 tile stage (320×192 logical pixels).
 *
 * The tile grid holds the ground tiles. `objects` is a painter-ordered
 * list of props rendered on top of the tile layer, sorted by their
 * bottom-y so further-back things draw first.
 */

import { STAGE } from "./types";
import type { TileKind } from "./tileSprites";

export type MapTheme = "meadow" | "forest" | "town";

export interface SceneObject {
  kind:
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
}

/** Build a fresh tile grid filled with a single kind. */
function fill(kind: TileKind): TileKind[][] {
  return Array.from({ length: STAGE.rows }, () =>
    Array.from({ length: STAGE.cols }, () => kind),
  );
}

/** Shared meadow base: sky above the horizon, grass below, path + pond. */
function buildMeadowTiles(): TileKind[][] {
  const t = fill("sky");
  const HZ = STAGE.horizonRow; // rows >= HZ are ground tiles

  // Ground fill (grass)
  for (let r = HZ; r < STAGE.rows; r++) {
    for (let c = 0; c < STAGE.cols; c++) t[r][c] = "grass";
  }

  // Darker grass band at the horizon strip for depth
  for (let x = 0; x < STAGE.cols; x++) {
    t[HZ][x] = "grassDark";
  }

  // Horizontal dirt path crossing the ground (foreground)
  const pathRow = STAGE.rows - 2; // row 10
  for (let x = 0; x < STAGE.cols; x++) {
    t[pathRow][x] = "path";
    t[pathRow - 1][x] = "pathEdge";
  }

  // Flower-grass scatter in the middle ground
  for (const [r, c] of [
    [HZ + 1, 3],
    [HZ + 1, 10],
    [HZ + 1, 16],
    [HZ + 2, 6],
    [HZ + 2, 13],
    [HZ + 2, 18],
  ]) {
    if (r < STAGE.rows) t[r][c] = "flowerGrass";
  }

  // Small pond in the bottom-right (below horizon strip)
  const pondCells: Array<[number, number]> = [
    [HZ + 2, 16], [HZ + 2, 17], [HZ + 2, 18],
    [HZ + 3, 15], [HZ + 3, 16], [HZ + 3, 17], [HZ + 3, 18], [HZ + 3, 19],
  ];
  for (const [r, c] of pondCells) {
    if (r < STAGE.rows && c < STAGE.cols) t[r][c] = "water";
  }
  // pond shore (bottom edge has foam)
  for (const c of [15, 16, 17, 18, 19]) {
    if (HZ + 3 < STAGE.rows) t[HZ + 3][c] = "waterEdge";
  }
  // sand ring around pond
  for (const [r, c] of [
    [HZ + 1, 16], [HZ + 1, 17], [HZ + 1, 18],
    [HZ + 2, 15], [HZ + 2, 19],
  ]) {
    if (r < STAGE.rows && c < STAGE.cols) t[r][c] = "sand";
  }

  return t;
}

function buildMeadow(): MapLayout {
  const tiles = buildMeadowTiles();
  const objects: SceneObject[] = [];

  const horizon = STAGE.horizonRow * STAGE.tile; // 96
  const backY = horizon + 22; // back-row trees sit just below horizon
  const midY = horizon + 48;
  const frontFlowerY = STAGE.groundY + 18; // foreground flora, below characters

  // back-row trees
  objects.push({ kind: "tree", x: 18, y: backY });
  objects.push({ kind: "tree", x: 46, y: backY + 2 });
  objects.push({ kind: "tree", x: 226, y: backY + 2 });
  objects.push({ kind: "tree", x: 258, y: backY });
  objects.push({ kind: "tree", x: 294, y: backY + 2 });

  // fountain center back
  objects.push({ kind: "fountain", x: 160, y: midY });

  // mid bushes
  objects.push({ kind: "bush", x: 92, y: midY });
  objects.push({ kind: "bush", x: 208, y: midY });

  // rocks flanking the fountain
  objects.push({ kind: "rock", x: 124, y: midY + 2 });
  objects.push({ kind: "rock", x: 196, y: midY + 2 });

  // front-row flowers (below the path)
  objects.push({ kind: "flowerPatch", x: 30, y: frontFlowerY, color: "#ff6a8a" });
  objects.push({ kind: "flowerPatch", x: 64, y: frontFlowerY, color: "#ffe24a" });
  objects.push({ kind: "flowerPatch", x: 102, y: frontFlowerY, color: "#a85aff" });
  objects.push({ kind: "flowerPatch", x: 216, y: frontFlowerY, color: "#ff9140" });
  objects.push({ kind: "flowerPatch", x: 254, y: frontFlowerY, color: "#ffffff" });
  objects.push({ kind: "flowerPatch", x: 294, y: frontFlowerY, color: "#ff6a8a" });

  // sign at path start
  objects.push({ kind: "sign", x: 18, y: STAGE.groundY - 4 });

  // lamps flanking the fountain
  objects.push({ kind: "lamp", x: 130, y: midY + 4 });
  objects.push({ kind: "lamp", x: 190, y: midY + 4 });

  return { tiles, objects };
}

function buildForest(): MapLayout {
  const tiles = buildMeadowTiles();
  // darken all grass
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
  const midY = horizon + 48;

  // dense back-row tree canopy
  for (let i = 0; i < 10; i++) {
    objects.push({
      kind: "tree",
      x: 14 + i * 32,
      y: backY + ((i * 5) % 4),
    });
  }
  // mid-ground bushes + extra trees
  for (let i = 0; i < 5; i++) {
    objects.push({
      kind: "bush",
      x: 30 + i * 62,
      y: midY + (i % 2) * 4,
    });
  }
  objects.push({ kind: "tree", x: 70, y: midY + 10 });
  objects.push({ kind: "tree", x: 248, y: midY + 10 });
  objects.push({ kind: "rock", x: 120, y: midY + 4 });
  objects.push({ kind: "rock", x: 198, y: midY + 4 });

  // front flora
  const frontY = STAGE.groundY + 18;
  objects.push({ kind: "flowerPatch", x: 44, y: frontY, color: "#ff7a9e" });
  objects.push({ kind: "flowerPatch", x: 282, y: frontY, color: "#ffe24a" });

  // cozy lamp
  objects.push({ kind: "lamp", x: 160, y: midY + 4 });
  return { tiles, objects };
}

function buildTown(): MapLayout {
  const tiles = buildMeadowTiles();
  const objects: SceneObject[] = [];
  const horizon = STAGE.horizonRow * STAGE.tile;
  const backY = horizon + 40;

  // three houses across the back
  objects.push({ kind: "houseRed", x: 50, y: backY });
  objects.push({ kind: "houseBlue", x: 160, y: backY });
  objects.push({ kind: "houseGreen", x: 272, y: backY });

  // fence between houses
  for (let i = 0; i < 3; i++) {
    objects.push({ kind: "fence", x: 98 + i * 16, y: backY - 4 });
  }
  for (let i = 0; i < 3; i++) {
    objects.push({ kind: "fence", x: 208 + i * 16, y: backY - 4 });
  }

  // trees flanking
  objects.push({ kind: "tree", x: 14, y: backY + 4 });
  objects.push({ kind: "tree", x: 308, y: backY + 4 });

  // front row bushes
  const frontY = STAGE.groundY + 16;
  objects.push({ kind: "bush", x: 22, y: frontY });
  objects.push({ kind: "bush", x: 302, y: frontY });

  // lamps lining the path
  objects.push({ kind: "lamp", x: 90, y: backY + 8 });
  objects.push({ kind: "lamp", x: 228, y: backY + 8 });

  // sign at path start
  objects.push({ kind: "sign", x: 18, y: STAGE.groundY - 4 });

  // a few flower patches
  objects.push({ kind: "flowerPatch", x: 140, y: frontY, color: "#ff7a9e" });
  objects.push({ kind: "flowerPatch", x: 188, y: frontY, color: "#ffe24a" });

  return { tiles, objects };
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

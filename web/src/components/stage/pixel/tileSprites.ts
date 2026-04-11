/**
 * Tile brushes. Each TileKind is a 16×16 patch drawn procedurally onto
 * the map canvas, with a deterministic per-cell hash so neighbouring
 * tiles don't look copy-pasted.
 */

export type TileKind =
  | "sky"
  | "grass"
  | "grassDark"
  | "path"
  | "pathEdge"
  | "water"
  | "waterEdge"
  | "sand"
  | "stone"
  | "flowerGrass"
  // ── interior kinds ─────────────────────────────────────────────
  | "floorWood"
  | "floorTile"
  | "carpet"
  | "wallTop"
  | "wallFront"
  | "doormat"
  | "roofTile";

const TILE = 16;

interface TilePalette {
  base: string;
  shade: string;
  highlight: string;
  accent: string;
}

const GRASS: TilePalette = {
  base: "#4fa05a",
  shade: "#2e7a40",
  highlight: "#7cc87a",
  accent: "#1f4a26",
};

const GRASS_DARK: TilePalette = {
  base: "#2e7a40",
  shade: "#1b5a28",
  highlight: "#4fa05a",
  accent: "#0f3a18",
};

const PATH: TilePalette = {
  base: "#c89a5f",
  shade: "#8a5a22",
  highlight: "#e2b77a",
  accent: "#5a3a12",
};

const WATER: TilePalette = {
  base: "#3d84d8",
  shade: "#1f5aa8",
  highlight: "#9fcbf0",
  accent: "#bfe4ff",
};

const SAND: TilePalette = {
  base: "#f0d69a",
  shade: "#c8a25c",
  highlight: "#fff1c0",
  accent: "#a87820",
};

const STONE: TilePalette = {
  base: "#8a8f9c",
  shade: "#4a4e5c",
  highlight: "#c0c5d0",
  accent: "#2a2d38",
};

/** Deterministic 32-bit hash for (x,y) that tile brushes use to scatter
 *  texture without looking repetitive. */
function cellHash(tx: number, ty: number): number {
  let h = (tx * 374761393 + ty * 668265263) >>> 0;
  h = (h ^ (h >>> 13)) >>> 0;
  h = Math.imul(h, 1274126177) >>> 0;
  return (h ^ (h >>> 16)) >>> 0;
}

function rand01(seed: number, idx: number): number {
  let h = (seed + idx * 2654435761) >>> 0;
  h = (h ^ (h >>> 15)) >>> 0;
  h = Math.imul(h, 2246822519) >>> 0;
  h = (h ^ (h >>> 13)) >>> 0;
  return (h >>> 0) / 4294967296;
}

function paintBase(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  palette: TilePalette,
): void {
  ctx.fillStyle = palette.base;
  ctx.fillRect(ox, oy, TILE, TILE);
}

function sprinkle(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  color: string,
  seed: number,
  count: number,
): void {
  ctx.fillStyle = color;
  for (let i = 0; i < count; i++) {
    const x = Math.floor(rand01(seed, i * 2 + 1) * TILE);
    const y = Math.floor(rand01(seed, i * 2 + 2) * TILE);
    ctx.fillRect(ox + x, oy + y, 1, 1);
  }
}

function drawGrass(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
  palette: TilePalette,
): void {
  paintBase(ctx, ox, oy, palette);
  // Scatter shade specks
  sprinkle(ctx, ox, oy, palette.shade, seed ^ 0x1a, 10);
  // Scatter a few small tufts (vertical 2-pixel blades)
  ctx.fillStyle = palette.accent;
  const tufts = 3;
  for (let i = 0; i < tufts; i++) {
    const x = Math.floor(rand01(seed, 100 + i * 3) * (TILE - 2));
    const y = Math.floor(rand01(seed, 200 + i * 3) * (TILE - 2));
    ctx.fillRect(ox + x, oy + y, 1, 1);
    ctx.fillRect(ox + x, oy + y + 1, 1, 1);
  }
  // Highlights
  sprinkle(ctx, ox, oy, palette.highlight, seed ^ 0x2b, 5);
}

function drawPath(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  paintBase(ctx, ox, oy, PATH);
  sprinkle(ctx, ox, oy, PATH.shade, seed ^ 0x33, 8);
  sprinkle(ctx, ox, oy, PATH.highlight, seed ^ 0x4c, 6);
  // Few tiny pebbles
  ctx.fillStyle = PATH.accent;
  for (let i = 0; i < 2; i++) {
    const x = Math.floor(rand01(seed, 300 + i * 5) * (TILE - 2)) + 1;
    const y = Math.floor(rand01(seed, 400 + i * 5) * (TILE - 2)) + 1;
    ctx.fillRect(ox + x, oy + y, 2, 1);
  }
}

function drawPathEdge(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  // path body with darker edge band along top 2 rows
  drawPath(ctx, ox, oy, seed);
  ctx.fillStyle = PATH.shade;
  for (let x = 0; x < TILE; x++) {
    if (((x + (seed & 1)) & 1) === 0) ctx.fillRect(ox + x, oy, 1, 1);
    ctx.fillRect(ox + x, oy + 1, 1, 1);
  }
  ctx.fillStyle = PATH.accent;
  for (let x = 0; x < TILE; x += 3) ctx.fillRect(ox + x, oy, 1, 1);
}

function drawWater(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  paintBase(ctx, ox, oy, WATER);
  // Horizontal wavelet lines at deterministic rows
  ctx.fillStyle = WATER.shade;
  for (let y = 2; y < TILE; y += 5) {
    for (let x = 0; x < TILE; x++) {
      if (((x + seed) & 3) === 0) ctx.fillRect(ox + x, oy + y, 1, 1);
    }
  }
  ctx.fillStyle = WATER.highlight;
  for (let y = 4; y < TILE; y += 5) {
    for (let x = 0; x < TILE; x++) {
      if (((x + seed + 2) & 5) === 0) ctx.fillRect(ox + x, oy + y, 1, 1);
    }
  }
  // Occasional sparkle
  if ((seed & 7) === 0) {
    ctx.fillStyle = WATER.accent;
    const sx = (seed >> 3) % (TILE - 2);
    const sy = (seed >> 6) % (TILE - 2);
    ctx.fillRect(ox + sx, oy + sy, 2, 1);
    ctx.fillRect(ox + sx, oy + sy + 1, 1, 1);
  }
}

function drawWaterEdge(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  drawWater(ctx, ox, oy, seed);
  // foam along bottom row (lake shore)
  ctx.fillStyle = WATER.accent;
  for (let x = 0; x < TILE; x++) {
    if (((x + seed) & 1) === 0) ctx.fillRect(ox + x, oy + TILE - 1, 1, 1);
  }
  ctx.fillStyle = "#ffffff";
  for (let x = 0; x < TILE; x += 3) ctx.fillRect(ox + x, oy + TILE - 1, 1, 1);
}

function drawSand(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  paintBase(ctx, ox, oy, SAND);
  sprinkle(ctx, ox, oy, SAND.shade, seed ^ 0x5d, 10);
  sprinkle(ctx, ox, oy, SAND.highlight, seed ^ 0x6e, 6);
}

function drawStone(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  paintBase(ctx, ox, oy, STONE);
  sprinkle(ctx, ox, oy, STONE.shade, seed ^ 0x77, 15);
  sprinkle(ctx, ox, oy, STONE.highlight, seed ^ 0x88, 8);
  // crack line
  ctx.fillStyle = STONE.accent;
  for (let i = 0; i < 4; i++) {
    const x = (seed + i * 3) % TILE;
    const y = (seed * 7 + i * 2) % TILE;
    ctx.fillRect(ox + x, oy + y, 1, 1);
  }
}

// ── interior tile palettes & drawers ────────────────────────────────────

const WOOD_FLOOR: TilePalette = {
  base: "#a47148",
  shade: "#6b4424",
  highlight: "#c48a5a",
  accent: "#3a2210",
};

const TILE_FLOOR: TilePalette = {
  base: "#d4d0c4",
  shade: "#9b9688",
  highlight: "#f0ecde",
  accent: "#5a564a",
};

const CARPET_FLOOR: TilePalette = {
  base: "#8a2c4e",
  shade: "#5a1a2e",
  highlight: "#c04a74",
  accent: "#2a0a14",
};

const WALL_TOP: TilePalette = {
  base: "#6b5a4a",
  shade: "#3a2f24",
  highlight: "#8a7660",
  accent: "#1e140a",
};

const WALL_FRONT: TilePalette = {
  base: "#c8a070",
  shade: "#7a5a34",
  highlight: "#e4bf94",
  accent: "#3a2612",
};

const DOORMAT: TilePalette = {
  base: "#4a3620",
  shade: "#2a1e0e",
  highlight: "#7a5a34",
  accent: "#1a1004",
};

const ROOF_TILE: TilePalette = {
  base: "#7a2a1a",
  shade: "#4a1608",
  highlight: "#b84830",
  accent: "#2a0a04",
};

function drawFloorWood(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  paintBase(ctx, ox, oy, WOOD_FLOOR);
  // horizontal plank lines every 4 rows
  ctx.fillStyle = WOOD_FLOOR.shade;
  for (let y = 3; y < TILE; y += 4) {
    for (let x = 0; x < TILE; x++) ctx.fillRect(ox + x, oy + y, 1, 1);
  }
  // grain specks
  ctx.fillStyle = WOOD_FLOOR.accent;
  for (let i = 0; i < 4; i++) {
    const x = Math.floor(rand01(seed, 80 + i * 3) * TILE);
    const y = Math.floor(rand01(seed, 90 + i * 3) * TILE);
    ctx.fillRect(ox + x, oy + y, 1, 1);
  }
  // plank-end verticals (deterministic per row)
  ctx.fillStyle = WOOD_FLOOR.shade;
  for (let band = 0; band < 4; band++) {
    const rowY = band * 4;
    const sx = (seed >> (band * 2)) % TILE;
    ctx.fillRect(ox + sx, oy + rowY, 1, 3);
  }
  // highlight
  sprinkle(ctx, ox, oy, WOOD_FLOOR.highlight, seed ^ 0xa3, 3);
}

function drawFloorTile(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  paintBase(ctx, ox, oy, TILE_FLOOR);
  // grout lines forming 2×2 sub-tiles (8px each)
  ctx.fillStyle = TILE_FLOOR.shade;
  for (let x = 0; x < TILE; x++) {
    ctx.fillRect(ox + x, oy + 7, 1, 1);
    ctx.fillRect(ox + x, oy + 15, 1, 1);
  }
  for (let y = 0; y < TILE; y++) {
    ctx.fillRect(ox + 7, oy + y, 1, 1);
    ctx.fillRect(ox + 15, oy + y, 1, 1);
  }
  // highlight corners
  ctx.fillStyle = TILE_FLOOR.highlight;
  ctx.fillRect(ox + 0, oy + 0, 2, 1);
  ctx.fillRect(ox + 8, oy + 0, 2, 1);
  ctx.fillRect(ox + 0, oy + 8, 2, 1);
  ctx.fillRect(ox + 8, oy + 8, 2, 1);
  // occasional speck of dirt
  if ((seed & 3) === 0) {
    ctx.fillStyle = TILE_FLOOR.accent;
    ctx.fillRect(ox + ((seed >> 2) % 6) + 2, oy + ((seed >> 5) % 6) + 2, 1, 1);
  }
}

function drawCarpet(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  paintBase(ctx, ox, oy, CARPET_FLOOR);
  // woven pattern — repeating diamond
  ctx.fillStyle = CARPET_FLOOR.shade;
  for (let y = 0; y < TILE; y++) {
    for (let x = 0; x < TILE; x++) {
      if (((x + y) & 3) === 0) ctx.fillRect(ox + x, oy + y, 1, 1);
    }
  }
  ctx.fillStyle = CARPET_FLOOR.highlight;
  for (let y = 1; y < TILE; y += 4) {
    for (let x = 1; x < TILE; x += 4) {
      ctx.fillRect(ox + x, oy + y, 1, 1);
    }
  }
  // gold trim glints
  if ((seed & 1) === 0) {
    ctx.fillStyle = "#e6b04a";
    ctx.fillRect(ox + 3, oy + 3, 1, 1);
    ctx.fillRect(ox + 11, oy + 11, 1, 1);
  }
}

function drawWallTop(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
): void {
  // top face of a wall (darker), used for the top edge of each room
  paintBase(ctx, ox, oy, WALL_TOP);
  ctx.fillStyle = WALL_TOP.highlight;
  ctx.fillRect(ox + 0, oy + 0, TILE, 1);
  ctx.fillStyle = WALL_TOP.shade;
  ctx.fillRect(ox + 0, oy + TILE - 1, TILE, 1);
  // brick seams
  ctx.fillStyle = WALL_TOP.accent;
  for (let x = 3; x < TILE; x += 6) ctx.fillRect(ox + x, oy + 6, 1, 4);
}

function drawWallFront(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  // front face of the wall — brick pattern
  paintBase(ctx, ox, oy, WALL_FRONT);
  ctx.fillStyle = WALL_FRONT.shade;
  // horizontal mortar lines
  for (let y = 3; y < TILE; y += 5) {
    for (let x = 0; x < TILE; x++) ctx.fillRect(ox + x, oy + y, 1, 1);
  }
  // vertical mortar lines (offset every other row for brick bond)
  for (let row = 0; row < 3; row++) {
    const yTop = row * 5;
    const offset = (row & 1) === 0 ? 0 : 4;
    for (let x = offset; x < TILE; x += 8) {
      ctx.fillRect(ox + x, oy + yTop, 1, 3);
    }
  }
  // highlight top edge
  ctx.fillStyle = WALL_FRONT.highlight;
  ctx.fillRect(ox + 0, oy + 0, TILE, 1);
  // weathering speck
  if ((seed & 3) === 0) {
    ctx.fillStyle = WALL_FRONT.accent;
    ctx.fillRect(ox + 2 + ((seed >> 2) % 10), oy + 1 + ((seed >> 5) % 12), 1, 1);
  }
}

function drawDoormat(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  paintBase(ctx, ox, oy, DOORMAT);
  ctx.fillStyle = DOORMAT.highlight;
  // bristle lines
  for (let y = 2; y < TILE; y += 3) {
    for (let x = 0; x < TILE; x += 2) {
      if (((x + seed) & 1) === 0) ctx.fillRect(ox + x, oy + y, 1, 1);
    }
  }
  ctx.fillStyle = DOORMAT.accent;
  ctx.fillRect(ox + 0, oy + 0, TILE, 1);
  ctx.fillRect(ox + 0, oy + TILE - 1, TILE, 1);
}

function drawRoofTile(ctx: Ctx, ox: number, oy: number, seed: number): void {
  paintBase(ctx, ox, oy, ROOF_TILE);
  // shingles: scalloped rows
  ctx.fillStyle = ROOF_TILE.shade;
  for (let y = 3; y < TILE; y += 4) {
    for (let x = 0; x < TILE; x++) ctx.fillRect(ox + x, oy + y, 1, 1);
  }
  ctx.fillStyle = ROOF_TILE.highlight;
  for (let row = 0; row < 4; row++) {
    const yTop = row * 4;
    const off = (row & 1) * 2;
    for (let x = off; x < TILE; x += 4) {
      ctx.fillRect(ox + x, oy + yTop, 2, 1);
    }
  }
  ctx.fillStyle = ROOF_TILE.accent;
  for (let i = 0; i < 3; i++) {
    const x = (seed + i * 5) % TILE;
    const y = (seed + i * 3) % TILE;
    ctx.fillRect(ox + x, oy + y, 1, 1);
  }
}

// ── helper renamed to keep the Ctx alias local to interior funcs ────────
type Ctx = CanvasRenderingContext2D;

function drawFlowerGrass(
  ctx: CanvasRenderingContext2D,
  ox: number,
  oy: number,
  seed: number,
): void {
  drawGrass(ctx, ox, oy, seed, GRASS);
  // scatter 2-3 small flower dots
  const colors = ["#ffe24a", "#ff7a9e", "#b07aff", "#ffffff"];
  for (let i = 0; i < 3; i++) {
    const cx = 2 + Math.floor(rand01(seed, 500 + i * 7) * (TILE - 4));
    const cy = 2 + Math.floor(rand01(seed, 600 + i * 7) * (TILE - 4));
    const col = colors[Math.floor(rand01(seed, 700 + i * 7) * colors.length)];
    ctx.fillStyle = col;
    ctx.fillRect(ox + cx, oy + cy, 2, 1);
    ctx.fillRect(ox + cx, oy + cy + 1, 2, 1);
    ctx.fillStyle = "#ffd64a";
    ctx.fillRect(ox + cx, oy + cy, 1, 1);
  }
}

/** Draw a single 16×16 tile at the given cell coordinates. */
export function drawTile(
  ctx: CanvasRenderingContext2D,
  kind: TileKind,
  tx: number,
  ty: number,
): void {
  const ox = tx * TILE;
  const oy = ty * TILE;
  const seed = cellHash(tx, ty);
  switch (kind) {
    case "sky":
      // sky is painted separately as a full-canvas gradient; nothing to draw here
      return;
    case "grass":
      drawGrass(ctx, ox, oy, seed, GRASS);
      return;
    case "grassDark":
      drawGrass(ctx, ox, oy, seed, GRASS_DARK);
      return;
    case "path":
      drawPath(ctx, ox, oy, seed);
      return;
    case "pathEdge":
      drawPathEdge(ctx, ox, oy, seed);
      return;
    case "water":
      drawWater(ctx, ox, oy, seed);
      return;
    case "waterEdge":
      drawWaterEdge(ctx, ox, oy, seed);
      return;
    case "sand":
      drawSand(ctx, ox, oy, seed);
      return;
    case "stone":
      drawStone(ctx, ox, oy, seed);
      return;
    case "flowerGrass":
      drawFlowerGrass(ctx, ox, oy, seed);
      return;
  }
}

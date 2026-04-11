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
  | "flowerGrass";

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

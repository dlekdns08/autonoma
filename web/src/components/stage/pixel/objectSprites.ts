/**
 * Static scene objects drawn on top of the tile layer.
 *
 * Each object is a thin procedural draw routine so we can position them
 * at arbitrary pixel coordinates without baking them into the tile grid.
 * All of them expose their pixel footprint via `width`/`height` so the
 * map layout code can reason about collision and depth sort.
 */

type Ctx = CanvasRenderingContext2D;

const px = (ctx: Ctx, x: number, y: number, w: number, h: number, c: string) => {
  ctx.fillStyle = c;
  ctx.fillRect(x, y, w, h);
};

// ── Tree (oak-ish): 16w × 26h, anchor at bottom-center ────────────────
export const TREE_W = 16;
export const TREE_H = 26;

export function drawTree(ctx: Ctx, ox: number, oy: number, seed = 0): void {
  // trunk
  const trunkDark = "#3a1e0a";
  const trunkLight = "#6b3a14";
  const leafDark = "#1f4a1e";
  const leafMid = "#2f7a30";
  const leafLight = "#58b04e";
  const leafHi = "#9fe07c";

  // canopy — oval mass, rows 0..17
  // base outline shadow
  px(ctx, ox + 3, oy + 0, 10, 1, leafDark);
  px(ctx, ox + 2, oy + 1, 12, 1, leafDark);
  px(ctx, ox + 1, oy + 2, 14, 2, leafDark);
  px(ctx, ox + 0, oy + 4, 16, 6, leafDark);
  px(ctx, ox + 1, oy + 10, 14, 3, leafDark);
  px(ctx, ox + 2, oy + 13, 12, 2, leafDark);
  px(ctx, ox + 3, oy + 15, 10, 1, leafDark);
  // inner mid green
  px(ctx, ox + 3, oy + 1, 10, 1, leafMid);
  px(ctx, ox + 2, oy + 2, 12, 1, leafMid);
  px(ctx, ox + 2, oy + 3, 12, 1, leafMid);
  px(ctx, ox + 1, oy + 4, 14, 5, leafMid);
  px(ctx, ox + 2, oy + 9, 12, 4, leafMid);
  px(ctx, ox + 3, oy + 13, 10, 1, leafMid);
  // lit green lobe (left side)
  px(ctx, ox + 3, oy + 3, 7, 2, leafLight);
  px(ctx, ox + 2, oy + 5, 9, 3, leafLight);
  px(ctx, ox + 3, oy + 8, 7, 2, leafLight);
  // highlight sparkle
  px(ctx, ox + 4, oy + 4, 3, 1, leafHi);
  px(ctx, ox + 3, oy + 6, 2, 1, leafHi);
  px(ctx, ox + 6, oy + 7, 2, 1, leafHi);
  // tiny dark speckles for texture
  const speckles = [
    [5, 2], [10, 3], [12, 6], [4, 9], [11, 10], [8, 12], [13, 8],
  ];
  for (const [dx, dy] of speckles) {
    if ((seed + dx * 7 + dy * 5) & 1) px(ctx, ox + dx, oy + dy, 1, 1, leafDark);
  }

  // trunk rows 16..22
  px(ctx, ox + 6, oy + 16, 4, 6, trunkDark);
  px(ctx, ox + 7, oy + 16, 2, 6, trunkLight);
  // knot
  px(ctx, ox + 7, oy + 18, 1, 1, trunkDark);
  // roots fan at base
  px(ctx, ox + 5, oy + 22, 6, 1, trunkDark);
  px(ctx, ox + 4, oy + 23, 8, 1, trunkDark);
  px(ctx, ox + 5, oy + 23, 6, 1, trunkLight);
  // shadow oval under tree
  px(ctx, ox + 3, oy + 24, 10, 1, "#00000055");
  px(ctx, ox + 4, oy + 25, 8, 1, "#00000033");
}

// ── Small bush: 14w × 10h, bottom-center anchor ───────────────────────
export const BUSH_W = 14;
export const BUSH_H = 10;

export function drawBush(ctx: Ctx, ox: number, oy: number): void {
  const dark = "#1f4a1e";
  const mid = "#3a7a30";
  const light = "#6cb84a";
  const hi = "#a8e068";
  // base outline
  px(ctx, ox + 2, oy + 1, 10, 1, dark);
  px(ctx, ox + 1, oy + 2, 12, 1, dark);
  px(ctx, ox + 0, oy + 3, 14, 4, dark);
  px(ctx, ox + 1, oy + 7, 12, 1, dark);
  px(ctx, ox + 2, oy + 8, 10, 1, dark);
  // mid fill
  px(ctx, ox + 2, oy + 2, 10, 1, mid);
  px(ctx, ox + 1, oy + 3, 12, 3, mid);
  px(ctx, ox + 2, oy + 6, 10, 1, mid);
  // light top lobes
  px(ctx, ox + 2, oy + 2, 4, 2, light);
  px(ctx, ox + 7, oy + 1, 4, 2, light);
  px(ctx, ox + 3, oy + 4, 3, 1, light);
  // highlight
  px(ctx, ox + 3, oy + 2, 2, 1, hi);
  px(ctx, ox + 8, oy + 1, 2, 1, hi);
  // shadow
  px(ctx, ox + 2, oy + 9, 10, 1, "#00000044");
}

// ── Rock: 14w × 10h ───────────────────────────────────────────────────
export const ROCK_W = 14;
export const ROCK_H = 10;

export function drawRock(ctx: Ctx, ox: number, oy: number): void {
  const dark = "#2c3140";
  const mid = "#5a5f72";
  const light = "#8a90a4";
  const hi = "#b8bdce";
  // outline
  px(ctx, ox + 3, oy + 1, 8, 1, dark);
  px(ctx, ox + 1, oy + 2, 12, 1, dark);
  px(ctx, ox + 0, oy + 3, 14, 5, dark);
  px(ctx, ox + 1, oy + 8, 12, 1, dark);
  // mid
  px(ctx, ox + 2, oy + 2, 10, 1, mid);
  px(ctx, ox + 1, oy + 3, 12, 5, mid);
  // light top
  px(ctx, ox + 3, oy + 2, 6, 2, light);
  px(ctx, ox + 2, oy + 4, 5, 2, light);
  // hi glint
  px(ctx, ox + 4, oy + 2, 2, 1, hi);
  px(ctx, ox + 3, oy + 4, 1, 1, hi);
  // shadow
  px(ctx, ox + 2, oy + 9, 10, 1, "#00000055");
}

// ── House: 40w × 36h, bottom-center anchor ────────────────────────────
export const HOUSE_W = 40;
export const HOUSE_H = 36;

export function drawHouse(
  ctx: Ctx,
  ox: number,
  oy: number,
  variant: "red" | "blue" | "green" = "red",
): void {
  const roofDark = variant === "red" ? "#7a1f14" : variant === "blue" ? "#1f3a7a" : "#2a5a2e";
  const roofMid = variant === "red" ? "#c6392e" : variant === "blue" ? "#3362c9" : "#4a8d4a";
  const roofHi = variant === "red" ? "#ea6a4e" : variant === "blue" ? "#5a8adf" : "#6ab06a";
  const wallDark = "#6a4020";
  const wallMid = "#c28a4a";
  const wallHi = "#e4b97a";
  const wood = "#3a1e0a";
  const door = "#4a2610";
  const doorFrame = "#2a1408";
  const window = "#9fd4ea";
  const windowDark = "#3a6a88";

  // Roof triangle — rows 0..11, cols 0..40
  // pitched shape built from rectangles
  px(ctx, ox + 18, oy + 0, 4, 1, roofDark);
  px(ctx, ox + 16, oy + 1, 8, 1, roofDark);
  px(ctx, ox + 14, oy + 2, 12, 1, roofDark);
  px(ctx, ox + 12, oy + 3, 16, 1, roofDark);
  px(ctx, ox + 10, oy + 4, 20, 1, roofDark);
  px(ctx, ox + 8, oy + 5, 24, 1, roofDark);
  px(ctx, ox + 6, oy + 6, 28, 1, roofDark);
  px(ctx, ox + 4, oy + 7, 32, 1, roofDark);
  px(ctx, ox + 2, oy + 8, 36, 1, roofDark);
  px(ctx, ox + 0, oy + 9, 40, 1, roofDark);
  // roof mid fill
  px(ctx, ox + 17, oy + 1, 6, 1, roofMid);
  px(ctx, ox + 15, oy + 2, 10, 1, roofMid);
  px(ctx, ox + 13, oy + 3, 14, 1, roofMid);
  px(ctx, ox + 11, oy + 4, 18, 1, roofMid);
  px(ctx, ox + 9, oy + 5, 22, 1, roofMid);
  px(ctx, ox + 7, oy + 6, 26, 1, roofMid);
  px(ctx, ox + 5, oy + 7, 30, 1, roofMid);
  px(ctx, ox + 3, oy + 8, 34, 1, roofMid);
  px(ctx, ox + 1, oy + 9, 38, 1, roofMid);
  // roof highlight stripe
  px(ctx, ox + 17, oy + 2, 3, 1, roofHi);
  px(ctx, ox + 14, oy + 3, 4, 1, roofHi);
  px(ctx, ox + 11, oy + 4, 5, 1, roofHi);
  px(ctx, ox + 8, oy + 5, 6, 1, roofHi);
  // eave row 10
  px(ctx, ox + 0, oy + 10, 40, 1, wood);
  px(ctx, ox + 1, oy + 11, 38, 1, wallDark);

  // Wall rows 12..33
  px(ctx, ox + 2, oy + 12, 36, 22, wallMid);
  // wall outline
  px(ctx, ox + 1, oy + 12, 1, 22, wood);
  px(ctx, ox + 38, oy + 12, 1, 22, wood);
  px(ctx, ox + 2, oy + 33, 36, 1, wood);
  // wall shade band on right
  px(ctx, ox + 34, oy + 13, 4, 20, wallDark);
  // wall highlight on left
  px(ctx, ox + 2, oy + 13, 2, 19, wallHi);

  // Windows: two, symmetric
  const drawWindow = (wx: number, wy: number) => {
    px(ctx, wx, wy, 7, 6, wood);
    px(ctx, wx + 1, wy + 1, 5, 4, window);
    px(ctx, wx + 1, wy + 3, 5, 1, windowDark);
    px(ctx, wx + 3, wy + 1, 1, 4, windowDark);
    // highlight
    px(ctx, wx + 1, wy + 1, 1, 1, "#ffffff");
  };
  drawWindow(ox + 6, oy + 16);
  drawWindow(ox + 27, oy + 16);

  // Door center-bottom
  const dx = ox + 17;
  const dy = oy + 22;
  px(ctx, dx, dy, 6, 12, doorFrame);
  px(ctx, dx + 1, dy + 1, 4, 11, door);
  px(ctx, dx + 1, dy + 1, 1, 11, "#2e1808");
  // door knob
  px(ctx, dx + 4, dy + 7, 1, 1, "#f5c447");

  // Ground shadow
  px(ctx, ox + 2, oy + 34, 36, 1, "#00000044");
  px(ctx, ox + 4, oy + 35, 32, 1, "#00000022");
}

// ── Sign post: 12w × 16h ──────────────────────────────────────────────
export const SIGN_W = 12;
export const SIGN_H = 16;

export function drawSign(ctx: Ctx, ox: number, oy: number): void {
  const wood = "#4a2610";
  const plank = "#8a5a22";
  const plankHi = "#c89050";
  // post
  px(ctx, ox + 5, oy + 8, 2, 8, wood);
  // plank frame
  px(ctx, ox + 0, oy + 0, 12, 8, wood);
  px(ctx, ox + 1, oy + 1, 10, 6, plank);
  // highlight
  px(ctx, ox + 1, oy + 1, 10, 1, plankHi);
  px(ctx, ox + 1, oy + 1, 1, 6, plankHi);
  // text marks (3 dots suggesting writing)
  px(ctx, ox + 3, oy + 3, 2, 1, wood);
  px(ctx, ox + 6, oy + 3, 2, 1, wood);
  px(ctx, ox + 3, oy + 5, 4, 1, wood);
}

// ── Lamppost: 8w × 28h ────────────────────────────────────────────────
export const LAMP_W = 8;
export const LAMP_H = 28;

export function drawLamp(ctx: Ctx, ox: number, oy: number, lit: boolean): void {
  const metal = "#2a2432";
  const metalHi = "#5a526a";
  const glass = lit ? "#ffe48a" : "#4a4858";
  const glow = "#fff7c8";

  // base
  px(ctx, ox + 2, oy + 26, 4, 2, metal);
  px(ctx, ox + 1, oy + 27, 6, 1, metal);
  // post
  px(ctx, ox + 3, oy + 6, 2, 20, metal);
  px(ctx, ox + 3, oy + 6, 1, 20, metalHi);
  // lantern cage
  px(ctx, ox + 1, oy + 1, 6, 1, metal);
  px(ctx, ox + 0, oy + 2, 8, 1, metal);
  px(ctx, ox + 1, oy + 3, 6, 3, glass);
  px(ctx, ox + 0, oy + 6, 8, 1, metal);
  // glass bars
  px(ctx, ox + 2, oy + 3, 1, 3, metal);
  px(ctx, ox + 5, oy + 3, 1, 3, metal);
  // lit highlight
  if (lit) {
    px(ctx, ox + 3, oy + 4, 1, 1, glow);
    // aura
    ctx.fillStyle = "#ffe48a30";
    ctx.fillRect(ox - 2, oy + 0, 12, 10);
  }
}

// ── Flower patch: 10w × 5h ────────────────────────────────────────────
export const FLOWER_W = 10;
export const FLOWER_H = 5;

export function drawFlowerPatch(
  ctx: Ctx,
  ox: number,
  oy: number,
  color: string,
): void {
  const stem = "#1f4a18";
  const center = "#ffd64a";
  // 3 flowers
  const pos = [0, 4, 8];
  for (const x of pos) {
    px(ctx, ox + x + 1, oy + 3, 1, 2, stem);
    px(ctx, ox + x, oy + 1, 1, 1, color);
    px(ctx, ox + x + 2, oy + 1, 1, 1, color);
    px(ctx, ox + x + 1, oy + 0, 1, 1, color);
    px(ctx, ox + x + 1, oy + 2, 1, 1, color);
    px(ctx, ox + x + 1, oy + 1, 1, 1, center);
  }
}

// ── Fence segment: 16w × 10h ──────────────────────────────────────────
export const FENCE_W = 16;
export const FENCE_H = 10;

export function drawFence(ctx: Ctx, ox: number, oy: number): void {
  const dark = "#3a1e0a";
  const mid = "#8a5a22";
  const hi = "#c89050";
  // top rail
  px(ctx, ox + 0, oy + 2, 16, 2, mid);
  px(ctx, ox + 0, oy + 2, 16, 1, hi);
  px(ctx, ox + 0, oy + 4, 16, 1, dark);
  // bottom rail
  px(ctx, ox + 0, oy + 7, 16, 2, mid);
  px(ctx, ox + 0, oy + 7, 16, 1, hi);
  px(ctx, ox + 0, oy + 9, 16, 1, dark);
  // posts
  for (const x of [1, 8]) {
    px(ctx, ox + x, oy + 0, 2, 10, dark);
    px(ctx, ox + x, oy + 0, 1, 10, mid);
    px(ctx, ox + x, oy + 0, 1, 1, hi);
  }
}

// ── Fountain: 32w × 24h ───────────────────────────────────────────────
export const FOUNTAIN_W = 32;
export const FOUNTAIN_H = 24;

export function drawFountain(ctx: Ctx, ox: number, oy: number): void {
  const stoneDark = "#4a4e5c";
  const stoneMid = "#8a8f9c";
  const stoneHi = "#c0c5d0";
  const water = "#3d84d8";
  const waterHi = "#9fcbf0";
  // outer basin
  px(ctx, ox + 4, oy + 14, 24, 2, stoneDark);
  px(ctx, ox + 2, oy + 16, 28, 6, stoneDark);
  px(ctx, ox + 3, oy + 17, 26, 4, stoneMid);
  px(ctx, ox + 3, oy + 17, 26, 1, stoneHi);
  // water ring
  px(ctx, ox + 5, oy + 15, 22, 1, water);
  px(ctx, ox + 6, oy + 16, 20, 1, water);
  px(ctx, ox + 8, oy + 15, 4, 1, waterHi);
  px(ctx, ox + 16, oy + 15, 4, 1, waterHi);
  // center pillar
  px(ctx, ox + 13, oy + 6, 6, 10, stoneDark);
  px(ctx, ox + 14, oy + 6, 4, 9, stoneMid);
  px(ctx, ox + 14, oy + 6, 1, 9, stoneHi);
  // top bowl
  px(ctx, ox + 12, oy + 4, 8, 1, stoneDark);
  px(ctx, ox + 11, oy + 5, 10, 2, stoneMid);
  px(ctx, ox + 11, oy + 5, 10, 1, stoneHi);
  // water spout
  px(ctx, ox + 15, oy + 2, 2, 3, waterHi);
  px(ctx, ox + 14, oy + 3, 4, 1, water);
  px(ctx, ox + 13, oy + 4, 6, 1, water);
  // bottom shadow
  px(ctx, ox + 3, oy + 22, 26, 1, "#00000044");
}

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

// ─────────────────────────────────────────────────────────────────────
// INTERIOR FURNITURE & PROPS
// ─────────────────────────────────────────────────────────────────────

// ── Desk with monitor: 20w × 16h ─────────────────────────────────────
export const DESK_W = 20;
export const DESK_H = 16;

export function drawDesk(ctx: Ctx, ox: number, oy: number): void {
  const legDark = "#2a1a0a";
  const woodDark = "#4a2e14";
  const woodMid = "#8a5a28";
  const woodHi = "#c88a48";
  const screenFrame = "#18182a";
  const screen = "#3a6aa8";
  const screenGlow = "#a8d4ff";
  const keyboard = "#1a1a22";

  // monitor
  px(ctx, ox + 6, oy + 0, 8, 1, screenFrame);
  px(ctx, ox + 5, oy + 1, 10, 5, screenFrame);
  px(ctx, ox + 6, oy + 2, 8, 3, screen);
  px(ctx, ox + 6, oy + 2, 3, 1, screenGlow);
  px(ctx, ox + 7, oy + 3, 1, 1, screenGlow);
  // stand
  px(ctx, ox + 9, oy + 6, 2, 2, screenFrame);
  px(ctx, ox + 7, oy + 8, 6, 1, screenFrame);

  // desk top
  px(ctx, ox + 0, oy + 9, 20, 1, woodHi);
  px(ctx, ox + 0, oy + 10, 20, 2, woodMid);
  px(ctx, ox + 0, oy + 12, 20, 1, woodDark);
  // keyboard
  px(ctx, ox + 4, oy + 9, 10, 1, keyboard);
  for (let k = 5; k < 14; k += 2) px(ctx, ox + k, oy + 9, 1, 1, "#3a3a44");
  // legs
  px(ctx, ox + 1, oy + 13, 2, 3, legDark);
  px(ctx, ox + 17, oy + 13, 2, 3, legDark);
  // shadow
  px(ctx, ox + 1, oy + 15, 18, 1, "#00000044");
}

// ── Office chair: 10w × 14h ──────────────────────────────────────────
export const CHAIR_W = 10;
export const CHAIR_H = 14;

export function drawChair(ctx: Ctx, ox: number, oy: number): void {
  const dark = "#181822";
  const mid = "#3a3a48";
  const hi = "#5a5a6a";
  const metal = "#6a6e7a";
  // backrest
  px(ctx, ox + 2, oy + 0, 6, 1, dark);
  px(ctx, ox + 1, oy + 1, 8, 5, dark);
  px(ctx, ox + 2, oy + 2, 6, 3, mid);
  px(ctx, ox + 2, oy + 2, 6, 1, hi);
  // seat
  px(ctx, ox + 1, oy + 6, 8, 1, dark);
  px(ctx, ox + 1, oy + 7, 8, 2, mid);
  px(ctx, ox + 1, oy + 7, 8, 1, hi);
  // post
  px(ctx, ox + 4, oy + 9, 2, 3, metal);
  // wheel base
  px(ctx, ox + 1, oy + 12, 8, 1, metal);
  px(ctx, ox + 0, oy + 13, 3, 1, dark);
  px(ctx, ox + 7, oy + 13, 3, 1, dark);
  px(ctx, ox + 4, oy + 13, 2, 1, dark);
  // shadow
  px(ctx, ox + 1, oy + 13, 8, 1, "#00000033");
}

// ── Tall bookshelf: 18w × 30h ────────────────────────────────────────
export const BOOKSHELF_W = 18;
export const BOOKSHELF_H = 30;

export function drawBookshelf(ctx: Ctx, ox: number, oy: number, seed = 0): void {
  const frameDark = "#2a1608";
  const frameMid = "#6a3e18";
  const frameHi = "#9c5a24";
  const bookCols = ["#c83a2c", "#2c6cc8", "#2e9a3c", "#c89024", "#8a2cc8", "#1a4a7a"];

  // frame
  px(ctx, ox + 0, oy + 0, BOOKSHELF_W, 1, frameDark);
  px(ctx, ox + 0, oy + 0, 1, BOOKSHELF_H, frameDark);
  px(ctx, ox + BOOKSHELF_W - 1, oy + 0, 1, BOOKSHELF_H, frameDark);
  px(ctx, ox + 0, oy + BOOKSHELF_H - 1, BOOKSHELF_W, 1, frameDark);
  // inner back
  px(ctx, ox + 1, oy + 1, BOOKSHELF_W - 2, BOOKSHELF_H - 2, frameMid);
  px(ctx, ox + 1, oy + 1, BOOKSHELF_W - 2, 1, frameHi);

  // shelves (4 rows)
  const shelfYs = [7, 14, 21, 27];
  for (const sy of shelfYs) {
    px(ctx, ox + 1, oy + sy, BOOKSHELF_W - 2, 1, frameDark);
    px(ctx, ox + 1, oy + sy - 1, BOOKSHELF_W - 2, 1, frameHi);
  }

  // books per shelf — staggered widths & heights, deterministic by seed
  const shelfTops = [1, 8, 15, 22];
  for (let s = 0; s < shelfTops.length; s++) {
    let x = 2;
    let idx = 0;
    while (x < BOOKSHELF_W - 3) {
      const wBook = 1 + (((seed + s * 7 + idx * 3) >> 1) & 1) + 1;
      const h = 4 + ((seed + s * 5 + idx * 11) & 1);
      const topY = shelfTops[s] + (6 - h);
      const color = bookCols[(seed + s * 3 + idx) % bookCols.length];
      px(ctx, ox + x, oy + topY, wBook, h, color);
      // spine highlight
      px(ctx, ox + x, oy + topY, 1, h, "#ffffff22");
      x += wBook + 1;
      idx++;
    }
  }

  // base shadow
  px(ctx, ox + 0, oy + BOOKSHELF_H, BOOKSHELF_W, 1, "#00000044");
}

// ── Meeting / round table: 28w × 16h ─────────────────────────────────
export const TABLE_W = 28;
export const TABLE_H = 16;

export function drawMeetingTable(ctx: Ctx, ox: number, oy: number): void {
  const top = "#7a4a20";
  const topHi = "#b87a3a";
  const topShade = "#4a2a0e";
  const legDark = "#2a1608";
  const paper = "#f0e4c0";
  const paperEdge = "#a08a54";

  // ellipse top
  px(ctx, ox + 6, oy + 0, 16, 1, topShade);
  px(ctx, ox + 3, oy + 1, 22, 1, topShade);
  px(ctx, ox + 1, oy + 2, 26, 2, top);
  px(ctx, ox + 0, oy + 4, 28, 4, top);
  px(ctx, ox + 1, oy + 8, 26, 1, topShade);
  px(ctx, ox + 3, oy + 9, 22, 1, topShade);
  px(ctx, ox + 6, oy + 10, 16, 1, topShade);
  // top highlight
  px(ctx, ox + 4, oy + 2, 20, 1, topHi);
  px(ctx, ox + 2, oy + 3, 24, 1, topHi);

  // paper/coffee scattered on top
  px(ctx, ox + 6, oy + 4, 3, 2, paper);
  px(ctx, ox + 6, oy + 4, 3, 1, paperEdge);
  px(ctx, ox + 16, oy + 5, 4, 2, paper);
  px(ctx, ox + 16, oy + 5, 4, 1, paperEdge);
  // coffee cup
  px(ctx, ox + 21, oy + 4, 2, 2, "#ffffff");
  px(ctx, ox + 21, oy + 4, 2, 1, "#3a1a08");

  // legs
  px(ctx, ox + 4, oy + 11, 2, 4, legDark);
  px(ctx, ox + 22, oy + 11, 2, 4, legDark);
  px(ctx, ox + 13, oy + 11, 2, 4, legDark);
  // shadow
  px(ctx, ox + 2, oy + 15, 24, 1, "#00000055");
}

// ── Whiteboard: 28w × 16h ─────────────────────────────────────────────
export const WHITEBOARD_W = 28;
export const WHITEBOARD_H = 16;

export function drawWhiteboard(ctx: Ctx, ox: number, oy: number): void {
  const frame = "#2a2a32";
  const frameHi = "#5a5a68";
  const board = "#f4f4ee";
  const boardShade = "#c8c8c0";
  const marker1 = "#c83a2c";
  const marker2 = "#2c6cc8";
  const marker3 = "#1a9a3c";

  px(ctx, ox + 0, oy + 0, WHITEBOARD_W, 1, frame);
  px(ctx, ox + 0, oy + 0, 1, WHITEBOARD_H - 3, frame);
  px(ctx, ox + WHITEBOARD_W - 1, oy + 0, 1, WHITEBOARD_H - 3, frame);
  px(ctx, ox + 0, oy + WHITEBOARD_H - 4, WHITEBOARD_W, 1, frame);
  // inner board
  px(ctx, ox + 1, oy + 1, WHITEBOARD_W - 2, WHITEBOARD_H - 5, board);
  px(ctx, ox + 1, oy + 1, WHITEBOARD_W - 2, 1, frameHi);
  px(ctx, ox + 1, oy + WHITEBOARD_H - 5, WHITEBOARD_W - 2, 1, boardShade);

  // scribbles — arrows, boxes, diagrams
  // box 1
  px(ctx, ox + 3, oy + 3, 5, 3, marker1);
  px(ctx, ox + 4, oy + 4, 3, 1, board);
  // arrow
  px(ctx, ox + 8, oy + 4, 3, 1, marker2);
  px(ctx, ox + 10, oy + 3, 1, 3, marker2);
  // box 2
  px(ctx, ox + 12, oy + 3, 5, 4, marker3);
  px(ctx, ox + 13, oy + 4, 3, 2, board);
  // arrow2
  px(ctx, ox + 17, oy + 5, 3, 1, marker1);
  // box 3
  px(ctx, ox + 20, oy + 3, 5, 3, marker2);
  px(ctx, ox + 21, oy + 4, 3, 1, board);
  // bottom notes
  px(ctx, ox + 3, oy + 8, 8, 1, marker1);
  px(ctx, ox + 3, oy + 10, 6, 1, marker3);
  px(ctx, ox + 14, oy + 8, 10, 1, marker2);

  // tray
  px(ctx, ox + 2, oy + WHITEBOARD_H - 3, WHITEBOARD_W - 4, 1, frameHi);
  px(ctx, ox + 2, oy + WHITEBOARD_H - 2, WHITEBOARD_W - 4, 1, frame);
  // markers in tray
  px(ctx, ox + 5, oy + WHITEBOARD_H - 3, 3, 1, marker1);
  px(ctx, ox + 10, oy + WHITEBOARD_H - 3, 3, 1, marker2);
  px(ctx, ox + 15, oy + WHITEBOARD_H - 3, 3, 1, marker3);
  // shadow
  px(ctx, ox + 1, oy + WHITEBOARD_H - 1, WHITEBOARD_W - 2, 1, "#00000044");
}

// ── Couch: 24w × 14h ─────────────────────────────────────────────────
export const COUCH_W = 24;
export const COUCH_H = 14;

export function drawCouch(ctx: Ctx, ox: number, oy: number): void {
  const dark = "#1f2a48";
  const mid = "#3a4a78";
  const hi = "#5a6aa0";
  const cushionMid = "#4a5a88";
  const feet = "#181820";

  // back
  px(ctx, ox + 0, oy + 0, COUCH_W, 1, dark);
  px(ctx, ox + 0, oy + 1, COUCH_W, 5, mid);
  px(ctx, ox + 0, oy + 1, COUCH_W, 1, hi);
  // arm rests
  px(ctx, ox + 0, oy + 5, 3, 5, dark);
  px(ctx, ox + 0, oy + 6, 3, 3, mid);
  px(ctx, ox + COUCH_W - 3, oy + 5, 3, 5, dark);
  px(ctx, ox + COUCH_W - 3, oy + 6, 3, 3, mid);
  // seat cushions (2)
  px(ctx, ox + 3, oy + 6, 9, 4, cushionMid);
  px(ctx, ox + 3, oy + 6, 9, 1, hi);
  px(ctx, ox + 12, oy + 6, 9, 4, cushionMid);
  px(ctx, ox + 12, oy + 6, 9, 1, hi);
  // seam
  px(ctx, ox + 12, oy + 6, 1, 4, dark);
  // skirt
  px(ctx, ox + 0, oy + 10, COUCH_W, 2, dark);
  // feet
  px(ctx, ox + 1, oy + 12, 2, 2, feet);
  px(ctx, ox + COUCH_W - 3, oy + 12, 2, 2, feet);
  // shadow
  px(ctx, ox + 1, oy + 13, COUCH_W - 2, 1, "#00000044");
}

// ── Potted plant: 12w × 20h ──────────────────────────────────────────
export const PLANT_W = 12;
export const PLANT_H = 20;

export function drawPlant(ctx: Ctx, ox: number, oy: number): void {
  const leafDark = "#1a4a1a";
  const leafMid = "#2e7a2e";
  const leafHi = "#5ab04a";
  const potDark = "#4a2a10";
  const potMid = "#8a4a20";
  const potHi = "#c87a3a";

  // leafy top
  px(ctx, ox + 4, oy + 0, 4, 1, leafDark);
  px(ctx, ox + 2, oy + 1, 8, 1, leafDark);
  px(ctx, ox + 1, oy + 2, 10, 1, leafDark);
  px(ctx, ox + 0, oy + 3, 12, 4, leafDark);
  px(ctx, ox + 1, oy + 7, 10, 1, leafDark);
  px(ctx, ox + 2, oy + 8, 8, 1, leafDark);
  // mid green
  px(ctx, ox + 3, oy + 2, 6, 1, leafMid);
  px(ctx, ox + 2, oy + 3, 8, 3, leafMid);
  px(ctx, ox + 3, oy + 6, 6, 1, leafMid);
  // highlight
  px(ctx, ox + 3, oy + 3, 3, 1, leafHi);
  px(ctx, ox + 7, oy + 4, 2, 1, leafHi);
  px(ctx, ox + 4, oy + 5, 2, 1, leafHi);

  // stem peeking
  px(ctx, ox + 5, oy + 9, 2, 3, "#3a5a1a");

  // pot (trapezoid)
  px(ctx, ox + 2, oy + 12, 8, 1, potDark);
  px(ctx, ox + 3, oy + 13, 6, 5, potMid);
  px(ctx, ox + 3, oy + 13, 6, 1, potHi);
  px(ctx, ox + 3, oy + 18, 6, 1, potDark);
  // shadow
  px(ctx, ox + 3, oy + 19, 6, 1, "#00000055");
}

// ── Crate: 12w × 12h ─────────────────────────────────────────────────
export const CRATE_W = 12;
export const CRATE_H = 12;

export function drawCrate(ctx: Ctx, ox: number, oy: number): void {
  const dark = "#2a1608";
  const mid = "#6a3e18";
  const hi = "#9c5a24";
  const strap = "#4a2a10";
  // outline
  px(ctx, ox + 0, oy + 0, 12, 1, dark);
  px(ctx, ox + 0, oy + 0, 1, 12, dark);
  px(ctx, ox + 11, oy + 0, 1, 12, dark);
  px(ctx, ox + 0, oy + 11, 12, 1, dark);
  // fill
  px(ctx, ox + 1, oy + 1, 10, 10, mid);
  // planks
  px(ctx, ox + 1, oy + 1, 10, 1, hi);
  px(ctx, ox + 1, oy + 4, 10, 1, strap);
  px(ctx, ox + 1, oy + 7, 10, 1, strap);
  // X-brace
  for (let i = 0; i < 9; i++) {
    px(ctx, ox + 1 + i, oy + 2 + i, 1, 1, hi);
    px(ctx, ox + 10 - i, oy + 2 + i, 1, 1, hi);
  }
  // shadow
  px(ctx, ox + 1, oy + 12, 10, 1, "#00000044");
}

// ── Computer tower & monitor combo: 12w × 16h ────────────────────────
export const COMPUTER_W = 12;
export const COMPUTER_H = 16;

export function drawComputer(ctx: Ctx, ox: number, oy: number): void {
  const frame = "#18181f";
  const frameHi = "#3a3a48";
  const screen = "#0a3a7a";
  const screenGlow = "#6ac4ff";
  const text = "#8aff8a";

  // monitor
  px(ctx, ox + 1, oy + 0, 10, 8, frame);
  px(ctx, ox + 2, oy + 1, 8, 6, screen);
  px(ctx, ox + 2, oy + 1, 8, 1, screenGlow);
  // fake text lines (terminal)
  px(ctx, ox + 3, oy + 3, 4, 1, text);
  px(ctx, ox + 3, oy + 4, 3, 1, text);
  px(ctx, ox + 3, oy + 5, 5, 1, text);
  // stand
  px(ctx, ox + 5, oy + 8, 2, 2, frame);
  px(ctx, ox + 3, oy + 10, 6, 1, frame);
  // tower (CPU)
  px(ctx, ox + 7, oy + 11, 4, 5, frameHi);
  px(ctx, ox + 7, oy + 11, 4, 1, frame);
  px(ctx, ox + 8, oy + 12, 2, 1, "#ffcc00"); // power LED
  // shadow
  px(ctx, ox + 0, oy + 15, 12, 1, "#00000033");
}

// ── Door (wall-mounted, for room transitions): 12w × 20h ─────────────
export const DOOR_W = 12;
export const DOOR_H = 20;

export function drawDoor(ctx: Ctx, ox: number, oy: number): void {
  const frameDark = "#2a1608";
  const doorMid = "#6a3e18";
  const doorHi = "#9c5a24";
  const handle = "#e4c848";

  px(ctx, ox + 0, oy + 0, DOOR_W, 1, frameDark);
  px(ctx, ox + 0, oy + 0, 1, DOOR_H, frameDark);
  px(ctx, ox + DOOR_W - 1, oy + 0, 1, DOOR_H, frameDark);
  px(ctx, ox + 1, oy + 1, DOOR_W - 2, DOOR_H - 1, doorMid);
  px(ctx, ox + 1, oy + 1, 1, DOOR_H - 1, doorHi);
  // panels
  px(ctx, ox + 3, oy + 3, 6, 5, frameDark);
  px(ctx, ox + 4, oy + 4, 4, 3, doorHi);
  px(ctx, ox + 3, oy + 10, 6, 6, frameDark);
  px(ctx, ox + 4, oy + 11, 4, 4, doorHi);
  // handle
  px(ctx, ox + 9, oy + 11, 1, 2, handle);
}

// ── Bed: 24w × 14h ───────────────────────────────────────────────────
export const BED_W = 24;
export const BED_H = 14;

export function drawBed(ctx: Ctx, ox: number, oy: number): void {
  const frame = "#2a1608";
  const frameHi = "#6a3e18";
  const mattress = "#f0e4c0";
  const mattressShade = "#c8b880";
  const sheet = "#4a6aa8";
  const sheetHi = "#7a9ad8";
  const pillow = "#ffffff";
  const pillowShade = "#c8c8d8";

  // frame
  px(ctx, ox + 0, oy + 2, BED_W, 1, frame);
  px(ctx, ox + 0, oy + 2, 1, 10, frame);
  px(ctx, ox + BED_W - 1, oy + 2, 1, 10, frame);
  px(ctx, ox + 0, oy + 11, BED_W, 1, frame);
  // headboard
  px(ctx, ox + 0, oy + 0, 5, 2, frame);
  px(ctx, ox + 1, oy + 0, 3, 2, frameHi);
  // mattress
  px(ctx, ox + 1, oy + 3, BED_W - 2, 8, mattress);
  px(ctx, ox + 1, oy + 10, BED_W - 2, 1, mattressShade);
  // blanket (lower 2/3)
  px(ctx, ox + 6, oy + 5, BED_W - 7, 6, sheet);
  px(ctx, ox + 6, oy + 5, BED_W - 7, 1, sheetHi);
  // pillow (top)
  px(ctx, ox + 2, oy + 3, 4, 3, pillow);
  px(ctx, ox + 2, oy + 5, 4, 1, pillowShade);
  // legs
  px(ctx, ox + 0, oy + 12, 1, 2, frame);
  px(ctx, ox + BED_W - 1, oy + 12, 1, 2, frame);
  // shadow
  px(ctx, ox + 1, oy + 13, BED_W - 2, 1, "#00000044");
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

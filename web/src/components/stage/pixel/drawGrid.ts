import type { PixelGrid, PixelPalette } from "./types";

/** Draw a pixel grid onto a Canvas 2D context at (ox, oy), one rect per pixel.
 *  Palette chars not in the map (including `.`) are treated as transparent. */
export function drawGrid(
  ctx: CanvasRenderingContext2D,
  grid: PixelGrid,
  palette: PixelPalette,
  ox: number,
  oy: number,
): void {
  for (let y = 0; y < grid.length; y++) {
    const row = grid[y];
    for (let x = 0; x < row.length; x++) {
      const ch = row.charCodeAt(x);
      // fast transparent check for "." (46)
      if (ch === 46) continue;
      const color = palette[row[x]];
      if (!color) continue;
      ctx.fillStyle = color;
      ctx.fillRect(ox + x, oy + y, 1, 1);
    }
  }
}

/** Draw a grid flipped horizontally (for facing-left sprites). */
export function drawGridFlipped(
  ctx: CanvasRenderingContext2D,
  grid: PixelGrid,
  palette: PixelPalette,
  ox: number,
  oy: number,
): void {
  for (let y = 0; y < grid.length; y++) {
    const row = grid[y];
    const w = row.length;
    for (let x = 0; x < w; x++) {
      const ch = row.charCodeAt(x);
      if (ch === 46) continue;
      const color = palette[row[x]];
      if (!color) continue;
      ctx.fillStyle = color;
      ctx.fillRect(ox + (w - 1 - x), oy + y, 1, 1);
    }
  }
}

/** Blit a grid onto a target pixel buffer (Uint32Array, little-endian RGBA).
 *  Used by the map renderer to compose many tiles fast. */
export function blitGridToBuffer(
  buf: Uint32Array,
  bufW: number,
  bufH: number,
  grid: PixelGrid,
  palette: Record<string, number>,
  ox: number,
  oy: number,
): void {
  for (let y = 0; y < grid.length; y++) {
    const py = oy + y;
    if (py < 0 || py >= bufH) continue;
    const row = grid[y];
    for (let x = 0; x < row.length; x++) {
      const px = ox + x;
      if (px < 0 || px >= bufW) continue;
      const ch = row.charCodeAt(x);
      if (ch === 46) continue;
      const c = palette[row[x]];
      if (c === undefined) continue;
      buf[py * bufW + px] = c;
    }
  }
}

/** Convert a hex colour (#rrggbb or #rgb) to a little-endian ABGR integer
 *  suitable for assignment into a Uint32Array-backed ImageData buffer. */
export function hexToAbgr(hex: string, alpha = 0xff): number {
  let h = hex.trim();
  if (h.startsWith("#")) h = h.slice(1);
  if (h.length === 3) {
    h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
  }
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  // Canvas ImageData is RGBA in memory order; on little-endian machines
  // that maps to 0xAABBGGRR when viewed as Uint32.
  return ((alpha & 0xff) << 24) | ((b & 0xff) << 16) | ((g & 0xff) << 8) | (r & 0xff);
}

export function palettesToAbgr(p: Record<string, string>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const k of Object.keys(p)) out[k] = hexToAbgr(p[k]);
  return out;
}

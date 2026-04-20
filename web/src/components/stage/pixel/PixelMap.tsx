"use client";

import React, { useEffect, useRef, useState } from "react";

import { STAGE, type SkyMode } from "./types";
import { drawTile } from "./tileSprites";
import { buildMap, type MapTheme, type SceneObject } from "./mapData";
import {
  drawTree,
  drawBush,
  drawRock,
  drawHouse,
  drawSign,
  drawLamp,
  drawFlowerPatch,
  drawFence,
  drawFountain,
  drawDesk,
  drawChair,
  drawBookshelf,
  drawMeetingTable,
  drawWhiteboard,
  drawCouch,
  drawPlant,
  drawCrate,
  drawComputer,
  drawDoor,
  drawBed,
  TREE_H,
  BUSH_H,
  ROCK_H,
  HOUSE_H,
  SIGN_H,
  LAMP_H,
  FLOWER_H,
  FENCE_H,
  FOUNTAIN_H,
  DESK_H,
  CHAIR_H,
  BOOKSHELF_H,
  TABLE_H,
  WHITEBOARD_H,
  COUCH_H,
  PLANT_H,
  CRATE_H,
  COMPUTER_H,
  DOOR_H,
  BED_H,
} from "./objectSprites";

interface Props {
  sky?: SkyMode;
  theme?: MapTheme;
  children?: React.ReactNode;
}

interface SkyStops {
  s0: string;
  s25: string;
  s50: string;
  s75: string;
  s100: string;
}

const SKY: Record<SkyMode, SkyStops> = {
  dawn: {
    s0: "#1a0a28", s25: "#4a1a3a", s50: "#b84422", s75: "#ff7733", s100: "#ffcc88",
  },
  day: {
    // "day" maps to morning palette
    s0: "#0d2240", s25: "#1a4a7a", s50: "#3388cc", s75: "#77bbee", s100: "#cceefc",
  },
  dusk: {
    s0: "#0a0622", s25: "#280e42", s50: "#6a1f5a", s75: "#cc4466", s100: "#ff8855",
  },
  night: {
    s0: "#030310", s25: "#07071e", s50: "#0c0d2e", s75: "#141548", s100: "#1c1e5a",
  },
};

const NIGHT_TINT = "rgba(15, 10, 75, 0.38)";
const DUSK_TINT = "rgba(160, 45, 80, 0.22)";
const DAWN_TINT = "rgba(255, 120, 60, 0.16)";
const MORNING_TINT = "rgba(120, 180, 255, 0.08)";

// Warm ambient tint painted over interior scenes so they feel lit by lamps
// rather than flat pixel blocks.
const INTERIOR_TINT = "rgba(255, 195, 120, 0.09)";

function objectHeight(kind: SceneObject["kind"]): number {
  switch (kind) {
    case "tree": return TREE_H;
    case "bush": return BUSH_H;
    case "rock": return ROCK_H;
    case "houseRed":
    case "houseBlue":
    case "houseGreen": return HOUSE_H;
    case "sign": return SIGN_H;
    case "lamp": return LAMP_H;
    case "flowerPatch": return FLOWER_H;
    case "fence": return FENCE_H;
    case "fountain": return FOUNTAIN_H;
    case "desk": return DESK_H;
    case "chair": return CHAIR_H;
    case "bookshelf": return BOOKSHELF_H;
    case "meetingTable": return TABLE_H;
    case "whiteboard": return WHITEBOARD_H;
    case "couch": return COUCH_H;
    case "plant": return PLANT_H;
    case "crate": return CRATE_H;
    case "computer": return COMPUTER_H;
    case "door": return DOOR_H;
    case "bed": return BED_H;
  }
}

function objectWidth(kind: SceneObject["kind"]): number {
  switch (kind) {
    case "tree": return 16;
    case "bush": return 14;
    case "rock": return 14;
    case "houseRed":
    case "houseBlue":
    case "houseGreen": return 40;
    case "sign": return 12;
    case "lamp": return 8;
    case "flowerPatch": return 10;
    case "fence": return 16;
    case "fountain": return 32;
    case "desk": return 20;
    case "chair": return 10;
    case "bookshelf": return 18;
    case "meetingTable": return 28;
    case "whiteboard": return 28;
    case "couch": return 24;
    case "plant": return 12;
    case "crate": return 12;
    case "computer": return 12;
    case "door": return 12;
    case "bed": return 24;
  }
}

function drawObject(
  ctx: CanvasRenderingContext2D,
  obj: SceneObject,
  sky: SkyMode,
): void {
  const h = objectHeight(obj.kind);
  const topY = obj.y - h;
  const topX = Math.round(obj.x - objectWidth(obj.kind) / 2);
  switch (obj.kind) {
    case "tree": drawTree(ctx, topX, topY, Math.floor(obj.x * 13)); return;
    case "bush": drawBush(ctx, topX, topY); return;
    case "rock": drawRock(ctx, topX, topY); return;
    case "houseRed": drawHouse(ctx, topX, topY, "red"); return;
    case "houseBlue": drawHouse(ctx, topX, topY, "blue"); return;
    case "houseGreen": drawHouse(ctx, topX, topY, "green"); return;
    case "sign": drawSign(ctx, topX, topY); return;
    case "lamp": drawLamp(ctx, topX, topY, sky === "night" || sky === "dusk"); return;
    case "flowerPatch": drawFlowerPatch(ctx, topX, topY, obj.color ?? "#ff7a9e"); return;
    case "fence": drawFence(ctx, topX, topY); return;
    case "fountain": drawFountain(ctx, topX, topY); return;
    case "desk": drawDesk(ctx, topX, topY); return;
    case "chair": drawChair(ctx, topX, topY); return;
    case "bookshelf": drawBookshelf(ctx, topX, topY, Math.floor(obj.x * 17)); return;
    case "meetingTable": drawMeetingTable(ctx, topX, topY); return;
    case "whiteboard": drawWhiteboard(ctx, topX, topY); return;
    case "couch": drawCouch(ctx, topX, topY); return;
    case "plant": drawPlant(ctx, topX, topY); return;
    case "crate": drawCrate(ctx, topX, topY); return;
    case "computer": drawComputer(ctx, topX, topY); return;
    case "door": drawDoor(ctx, topX, topY); return;
    case "bed": drawBed(ctx, topX, topY); return;
  }
}

function paintSky(ctx: CanvasRenderingContext2D, sky: SkyMode): void {
  const stops = SKY[sky];
  const horizonPx = STAGE.horizonRow * STAGE.tile;
  const grad = ctx.createLinearGradient(0, 0, 0, horizonPx);
  grad.addColorStop(0,    stops.s0);
  grad.addColorStop(0.25, stops.s25);
  grad.addColorStop(0.5,  stops.s50);
  grad.addColorStop(0.75, stops.s75);
  grad.addColorStop(1,    stops.s100);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, STAGE.width, horizonPx);

  // --- Horizon haze: thin 2px gradient at the treeline ---
  const hazeGrad = ctx.createLinearGradient(0, horizonPx - 2, 0, horizonPx);
  hazeGrad.addColorStop(0, "rgba(0,0,0,0)");
  hazeGrad.addColorStop(1, `${stops.s100}88`);
  ctx.fillStyle = hazeGrad;
  ctx.fillRect(0, horizonPx - 2, STAGE.width, 2);

  if (sky === "night") {
    // --- Large bright stars (2×2) ---
    const largeBright: [number, number][] = [
      [18, 5], [72, 9], [148, 4], [222, 13], [290, 7],
      [44, 20], [106, 3], [196, 18], [260, 5], [316, 15],
    ];
    for (const [x, y] of largeBright) {
      ctx.fillStyle = (x + y) % 3 === 0
        ? "rgba(200,220,255,0.9)"
        : "rgba(255,255,255,0.95)";
      ctx.fillRect(x, y, 2, 2);
    }

    // --- Medium stars (2×1 or 1×2) ---
    const medium: [number, number, number, number][] = [
      [10, 14, 2, 1], [38, 8, 1, 2], [60, 22, 2, 1], [88, 6, 1, 2],
      [120, 17, 2, 1], [162, 12, 1, 2], [184, 24, 2, 1], [208, 4, 2, 1],
      [238, 16, 1, 2], [268, 10, 2, 1], [296, 21, 1, 2], [308, 3, 2, 1],
      [54, 28, 2, 1], [170, 28, 1, 2], [246, 25, 2, 1],
    ];
    ctx.fillStyle = "rgba(255,255,255,0.75)";
    for (const [x, y, w, h] of medium) ctx.fillRect(x, y, w, h);

    // --- Small dim stars (1×1) ---
    const small: [number, number][] = [
      [5, 10], [24, 18], [35, 4], [50, 14], [65, 26], [80, 10],
      [96, 20], [112, 8], [128, 22], [140, 14], [156, 6], [172, 20],
      [188, 10], [202, 26], [216, 8], [230, 18], [244, 4], [254, 24],
      [274, 14], [282, 28], [300, 12], [312, 22], [320, 6], [328, 18],
      [334, 10], [144, 26], [26, 28], [116, 28], [276, 28], [192, 24],
    ];
    for (const [x, y] of small) {
      const alpha = 0.4 + ((x * 7 + y * 13) % 20) / 100;
      ctx.fillStyle = `rgba(255,255,255,${alpha.toFixed(2)})`;
      ctx.fillRect(x, y, 1, 1);
    }

    // --- Warm yellowish stars (1×1) ---
    const warm: [number, number][] = [
      [32, 16], [102, 12], [214, 22], [286, 8], [330, 20],
    ];
    ctx.fillStyle = "rgba(255,220,160,0.8)";
    for (const [x, y] of warm) ctx.fillRect(x, y, 1, 1);

    // --- Moon ---
    ctx.fillStyle = "#f5e9c0";
    ctx.fillRect(268, 20, 6, 6);
    ctx.fillRect(267, 22, 1, 2);
    ctx.fillRect(274, 22, 1, 2);
    ctx.fillStyle = "#c8b886";
    ctx.fillRect(272, 22, 2, 2);
  } else if (sky === "dawn" || sky === "dusk") {
    ctx.fillStyle = sky === "dawn" ? "#ffe8a6" : "#ffb36a";
    const sx = sky === "dawn" ? 54 : 260;
    const sy = 40;
    ctx.fillRect(sx + 1, sy, 8, 1);
    ctx.fillRect(sx, sy + 1, 10, 8);
    ctx.fillRect(sx + 1, sy + 9, 8, 1);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(sx + 2, sy + 2, 2, 1);
  } else {
    ctx.fillStyle = "#ffffff";
    const clouds = [
      [30, 14], [32, 14], [33, 15], [28, 15], [34, 16], [29, 16],
      [132, 22], [134, 22], [135, 23], [130, 23], [136, 24], [131, 24],
      [240, 10], [242, 10], [243, 11], [238, 11], [244, 12], [239, 12],
    ];
    for (const [x, y] of clouds) ctx.fillRect(x, y, 2, 1);
  }
}

function tintGround(
  ctx: CanvasRenderingContext2D,
  sky: SkyMode,
  interior: boolean,
): void {
  if (interior) {
    ctx.fillStyle = INTERIOR_TINT;
    ctx.fillRect(0, 0, STAGE.width, STAGE.height);
    // slight night darkening inside
    if (sky === "night") {
      ctx.fillStyle = "rgba(20, 20, 60, 0.18)";
      ctx.fillRect(0, 0, STAGE.width, STAGE.height);
    }
    return;
  }

  const groundTop = STAGE.horizonRow * STAGE.tile;

  // Sky-wide atmospheric tint
  let skyTint: string | null = null;
  if (sky === "night") skyTint = NIGHT_TINT;
  else if (sky === "dusk") skyTint = DUSK_TINT;
  else if (sky === "dawn") skyTint = DAWN_TINT;
  else if (sky === "day") skyTint = MORNING_TINT; // morning (day) gets subtle cool wash

  if (skyTint) {
    ctx.fillStyle = skyTint;
    ctx.fillRect(0, 0, STAGE.width, STAGE.height);
  }
  // afternoon ("day" = afternoon palette): very subtle blue, only over ground
  // (sky already has the gradient; no full-canvas tint)
  if (sky === "day") {
    ctx.fillStyle = "rgba(100,160,255,0.04)";
    ctx.fillRect(0, groundTop, STAGE.width, STAGE.height - groundTop);
  }
}

export default function PixelMap({
  sky = "day",
  theme = "hq",
  children,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const outerRef = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState<{ width: number; height: number } | null>(
    null,
  );

  useEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    const update = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (w <= 0 || h <= 0) return;
      const targetAspect = STAGE.width / STAGE.height;
      const parentAspect = w / h;
      let iw: number;
      let ih: number;
      if (parentAspect > targetAspect) {
        ih = h;
        iw = h * targetAspect;
      } else {
        iw = w;
        ih = w / targetAspect;
      }
      setBox({ width: iw, height: ih });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, STAGE.width, STAGE.height);

    const map = buildMap(theme);
    const { tiles, objects, interior } = map;

    if (!interior) {
      paintSky(ctx, sky);
    } else {
      // fill everything with solid dark — tiles on top will cover it
      ctx.fillStyle = "#0b0810";
      ctx.fillRect(0, 0, STAGE.width, STAGE.height);
    }

    for (let r = 0; r < STAGE.rows; r++) {
      for (let c = 0; c < STAGE.cols; c++) {
        const kind = tiles[r][c];
        if (kind === "sky") continue;
        drawTile(ctx, kind, c, r);
      }
    }

    const sorted = [...objects].sort((a, b) => a.y - b.y);
    for (const obj of sorted) drawObject(ctx, obj, sky);

    tintGround(ctx, sky, interior);
  }, [sky, theme]);

  return (
    <div
      ref={outerRef}
      className="relative w-full h-full overflow-hidden flex items-center justify-center bg-black"
    >
      {box && (
        <div
          className="relative"
          style={{ width: box.width, height: box.height }}
        >
          <canvas
            ref={canvasRef}
            width={STAGE.width}
            height={STAGE.height}
            className="absolute inset-0 w-full h-full"
            style={{
              imageRendering: "pixelated",
              display: "block",
            }}
          />
          <div className="absolute inset-0">{children}</div>
        </div>
      )}
    </div>
  );
}

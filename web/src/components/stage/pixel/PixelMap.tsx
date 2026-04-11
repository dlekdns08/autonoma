"use client";

import React, { useEffect, useRef } from "react";

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
  TREE_H,
  BUSH_H,
  ROCK_H,
  HOUSE_H,
  SIGN_H,
  LAMP_H,
  FLOWER_H,
  FENCE_H,
  FOUNTAIN_H,
} from "./objectSprites";

interface Props {
  sky?: SkyMode;
  theme?: MapTheme;
  children?: React.ReactNode;
}

interface SkyStops {
  top: string;
  mid: string;
  bottom: string;
}

const SKY: Record<SkyMode, SkyStops> = {
  dawn: { top: "#ff9063", mid: "#ffb88c", bottom: "#f5d9b0" },
  day: { top: "#4ba3e8", mid: "#7cc6f4", bottom: "#bce4f9" },
  dusk: { top: "#3b1a5a", mid: "#c84a72", bottom: "#ffb088" },
  night: { top: "#0a0e2a", mid: "#1a1f48", bottom: "#2a2f5a" },
};

const NIGHT_TINT = "rgba(30, 22, 80, 0.28)";
const DUSK_TINT = "rgba(180, 60, 90, 0.16)";
const DAWN_TINT = "rgba(255, 160, 100, 0.12)";

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
  }
}

function drawObject(
  ctx: CanvasRenderingContext2D,
  obj: SceneObject,
  sky: SkyMode,
): void {
  const h = objectHeight(obj.kind);
  const topY = obj.y - h;
  const topX = Math.round(
    obj.x - objectWidth(obj.kind) / 2,
  );
  switch (obj.kind) {
    case "tree":
      drawTree(ctx, topX, topY, Math.floor(obj.x * 13));
      return;
    case "bush":
      drawBush(ctx, topX, topY);
      return;
    case "rock":
      drawRock(ctx, topX, topY);
      return;
    case "houseRed":
      drawHouse(ctx, topX, topY, "red");
      return;
    case "houseBlue":
      drawHouse(ctx, topX, topY, "blue");
      return;
    case "houseGreen":
      drawHouse(ctx, topX, topY, "green");
      return;
    case "sign":
      drawSign(ctx, topX, topY);
      return;
    case "lamp":
      drawLamp(ctx, topX, topY, sky === "night" || sky === "dusk");
      return;
    case "flowerPatch":
      drawFlowerPatch(ctx, topX, topY, obj.color ?? "#ff7a9e");
      return;
    case "fence":
      drawFence(ctx, topX, topY);
      return;
    case "fountain":
      drawFountain(ctx, topX, topY);
      return;
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
  }
}

/** Paint sky gradient on rows 0..horizonRow. */
function paintSky(ctx: CanvasRenderingContext2D, sky: SkyMode): void {
  const stops = SKY[sky];
  const horizonPx = STAGE.horizonRow * STAGE.tile;
  const grad = ctx.createLinearGradient(0, 0, 0, horizonPx);
  grad.addColorStop(0, stops.top);
  grad.addColorStop(0.6, stops.mid);
  grad.addColorStop(1, stops.bottom);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, STAGE.width, horizonPx);

  if (sky === "night") {
    // stars
    ctx.fillStyle = "#ffffff";
    const stars = [
      [10, 6], [28, 12], [44, 4], [66, 10], [82, 18], [104, 6],
      [130, 14], [154, 4], [176, 10], [200, 18], [220, 6], [244, 12],
      [268, 4], [290, 16], [306, 8],
    ];
    for (const [x, y] of stars) ctx.fillRect(x, y, 1, 1);
    // moon
    ctx.fillStyle = "#f5e9c0";
    ctx.fillRect(268, 20, 6, 6);
    ctx.fillRect(267, 22, 1, 2);
    ctx.fillRect(274, 22, 1, 2);
    ctx.fillStyle = "#c8b886";
    ctx.fillRect(272, 22, 2, 2);
  } else if (sky === "dawn" || sky === "dusk") {
    // big sun
    ctx.fillStyle = sky === "dawn" ? "#ffe8a6" : "#ffb36a";
    const sx = sky === "dawn" ? 54 : 260;
    const sy = 40;
    ctx.fillRect(sx + 1, sy, 8, 1);
    ctx.fillRect(sx, sy + 1, 10, 8);
    ctx.fillRect(sx + 1, sy + 9, 8, 1);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(sx + 2, sy + 2, 2, 1);
  } else {
    // puffy clouds for day
    ctx.fillStyle = "#ffffff";
    const clouds = [
      [30, 14], [32, 14], [33, 15], [28, 15], [34, 16], [29, 16],
      [132, 22], [134, 22], [135, 23], [130, 23], [136, 24], [131, 24],
      [240, 10], [242, 10], [243, 11], [238, 11], [244, 12], [239, 12],
    ];
    for (const [x, y] of clouds) ctx.fillRect(x, y, 2, 1);
  }
}

/** Apply a global sky tint to everything below the horizon. */
function tintGround(ctx: CanvasRenderingContext2D, sky: SkyMode): void {
  if (sky === "day") return;
  const groundTop = STAGE.horizonRow * STAGE.tile;
  ctx.fillStyle =
    sky === "night" ? NIGHT_TINT : sky === "dusk" ? DUSK_TINT : DAWN_TINT;
  ctx.fillRect(0, groundTop, STAGE.width, STAGE.height - groundTop);
}

export default function PixelMap({
  sky = "day",
  theme = "meadow",
  children,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;
    // Clear
    ctx.clearRect(0, 0, STAGE.width, STAGE.height);
    // Sky
    paintSky(ctx, sky);

    // Tiles below horizon
    const { tiles, objects } = buildMap(theme);
    for (let r = 0; r < STAGE.rows; r++) {
      for (let c = 0; c < STAGE.cols; c++) {
        const kind = tiles[r][c];
        if (kind === "sky") continue;
        drawTile(ctx, kind, c, r);
      }
    }

    // Objects, painter-sorted by bottom-y so distant things go first
    const sorted = [...objects].sort((a, b) => a.y - b.y);
    for (const obj of sorted) drawObject(ctx, obj, sky);

    // Global sky tint on the ground
    tintGround(ctx, sky);
  }, [sky, theme]);

  return (
    <div
      className="relative w-full h-full overflow-hidden"
      style={{ imageRendering: "pixelated" }}
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
      <div className="absolute inset-0 pointer-events-none">{children}</div>
    </div>
  );
}

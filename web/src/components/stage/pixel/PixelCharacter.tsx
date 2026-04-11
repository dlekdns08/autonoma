"use client";

import React, { useEffect, useMemo, useRef } from "react";

import { WALK_FRAMES, IDLE_FRAME, walkPhaseToFrame } from "./characterSprite";
import { buildCharacterPalette } from "./palette";
import { drawGrid } from "./drawGrid";
import { CHAR } from "./types";

export interface PixelCharacterProps {
  role?: string;
  species?: string;
  mood?: string;
  seed: string;
  /** 0..1 walk phase; omit for idle stand */
  walkPhase?: number;
  facingLeft?: boolean;
  /** optional external pixel scale for the gallery (the Stage uses 100% sizing) */
  pixelScale?: number;
}

/**
 * A single 16×24 Gen 3/4-style character rendered into a tiny canvas
 * and upscaled with nearest-neighbor.
 *
 * Inside the Stage the parent sets explicit width/height (percentage of
 * the playfield), so this component fills 100% of its container. In the
 * gallery, `pixelScale` is used to render at a fixed multiple.
 */
export default function PixelCharacter({
  role,
  species,
  mood,
  seed,
  walkPhase,
  facingLeft = false,
  pixelScale,
}: PixelCharacterProps) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  const palette = useMemo(
    () => buildCharacterPalette({ role, species, mood, seed }),
    [role, species, mood, seed],
  );

  const frameIdx =
    walkPhase !== undefined ? walkPhaseToFrame(walkPhase) : -1;
  const grid = frameIdx >= 0 ? WALK_FRAMES[frameIdx] : IDLE_FRAME;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, CHAR.width, CHAR.height);
    drawGrid(ctx, grid, palette, 0, 0);
  }, [grid, palette]);

  const sizeStyle: React.CSSProperties = pixelScale
    ? { width: CHAR.width * pixelScale, height: CHAR.height * pixelScale }
    : { width: "100%", height: "100%" };

  return (
    <canvas
      ref={ref}
      width={CHAR.width}
      height={CHAR.height}
      style={{
        ...sizeStyle,
        imageRendering: "pixelated",
        transform: facingLeft ? "scaleX(-1)" : undefined,
        display: "block",
      }}
    />
  );
}

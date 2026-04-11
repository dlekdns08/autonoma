"use client";

import React, { useEffect, useMemo, useRef } from "react";

import {
  buildFrames,
  resolveFeatures,
  walkPhaseToFrame,
  type CharacterFeatures,
} from "./characterSprite";
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
  /** override seed-derived features (gallery/preview only) */
  featureOverride?: Partial<CharacterFeatures>;
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
  featureOverride,
}: PixelCharacterProps) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  const palette = useMemo(
    () => buildCharacterPalette({ role, species, mood, seed }),
    [role, species, mood, seed],
  );

  // Feature overrides are only used by the gallery, which passes stable
  // literal objects per render, so a flat dep array on the individual
  // override fields is cheaper and safer than stringifying every render.
  const features = useMemo(
    () => ({ ...resolveFeatures(seed, species, role), ...featureOverride }),
    [
      seed,
      species,
      role,
      featureOverride?.hairStyle,
      featureOverride?.headwear,
      featureOverride?.ears,
      featureOverride?.glasses,
      featureOverride?.facialHair,
    ],
  );
  const frames = useMemo(() => buildFrames(features), [features]);

  const frameIdx =
    walkPhase !== undefined ? walkPhaseToFrame(walkPhase) : 0;
  const grid = frames[frameIdx];

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

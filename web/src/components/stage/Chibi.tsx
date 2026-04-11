"use client";

/**
 * Chibi — MapleStory-style anime chibi agent avatar.
 *
 * Composes a layered SVG character from independent part libraries in
 * ./chibi/parts/*. Walk cycle, mood expressions, role outfits, species
 * tails, rarity auras — all resolved from props without external state.
 *
 * The parent (Stage.tsx) is unchanged apart from passing `role` and `seed`.
 */

import React from "react";

import {
  CHIBI_VIEWBOX,
  type ChibiProps,
} from "./chibi/types";
import {
  OUTLINE,
  SKIN_BASE,
  SKIN_SHADE,
  SKIN_HIGHLIGHT,
  BLUSH,
  LIP,
  RARITY_GLOW,
  pickPalette,
  pickHairStyle,
} from "./chibi/theme";
import {
  resolveMood,
  resolveRole,
  resolveSpecies,
  resolveState,
} from "./chibi/variants";

import { HairBack, HairFront } from "./chibi/parts/Hair";
import { Eyes } from "./chibi/parts/Eyes";
import { FaceBase, FaceFeatures } from "./chibi/parts/Face";
import { Body } from "./chibi/parts/Body";
import { Outfit } from "./chibi/parts/Outfit";
import { HeadwearBack, HeadwearFront, pickHeadwear } from "./chibi/parts/Headwear";
import { SpeciesExtrasBack } from "./chibi/parts/SpeciesExtras";
import { HeldItem } from "./chibi/parts/HeldItem";
import { FaceAccessory, pickFaceAccessory } from "./chibi/parts/FaceAccessory";
import { EffectsBack, EffectsFront } from "./chibi/parts/Effects";

type Rarity = "common" | "uncommon" | "rare" | "legendary";

function resolveRarity(r?: string): Rarity {
  const k = (r ?? "common").toLowerCase();
  if (k === "uncommon" || k === "rare" || k === "legendary") return k;
  return "common";
}

export default function Chibi({
  species,
  mood,
  state,
  role,
  facingLeft = false,
  walkPhase,
  rarity,
  seed,
  bodyColor,
  size = 84,
}: ChibiProps) {
  const speciesKind = resolveSpecies(species);
  const moodKind = resolveMood(mood);
  const stateKind = resolveState(state);
  const roleKind = resolveRole(role);
  const rarityKind = resolveRarity(rarity);

  // Deterministic per-agent visual identity: same seed → same outfit/hair.
  const paletteSeed = seed ?? `${role ?? ""}:${species ?? ""}:${mood ?? ""}`;
  const palette = pickPalette(paletteSeed);
  const hairStyle = pickHairStyle(paletteSeed);

  const celebrating = stateKind === "celebrating";
  const isWalking = walkPhase !== undefined;
  const phase = walkPhase ?? 0;

  // body bob applied here, limbs are counter-rotated inside Body/Outfit
  const bodyBob = isWalking ? Math.abs(Math.sin(phase * Math.PI * 2)) * 2 : 0;

  // Width/height preserve the viewBox aspect
  const aspect = CHIBI_VIEWBOX.width / CHIBI_VIEWBOX.height;
  const renderHeight = size;
  const renderWidth = renderHeight * aspect;

  const headwearKind = pickHeadwear(roleKind, speciesKind);
  const faceAccessoryKind = pickFaceAccessory(roleKind);

  // Species primary colour (used for tails/wings). Humans get palette aura.
  const speciesColorMap: Record<string, string> = {
    cat: "#e2a95f",
    fox: "#f29052",
    rabbit: "#f5e6d0",
    dog: "#cfa36a",
    hamster: "#f5c85a",
    panda: "#1b1b22",
    bear: "#7a4a22",
    owl: "#8a5a24",
    duck: "#f5d64a",
    penguin: "#1c2540",
    human: palette.aura,
  };
  const speciesPrimary = speciesColorMap[speciesKind] ?? palette.aura;
  const speciesAccent = bodyColor ?? palette.hairLight;

  // Swap the eyebrow colour — darker than hair so it reads against skin.
  const hairColor = palette.hair;

  const glowFilter = RARITY_GLOW[rarityKind];

  return (
    <svg
      width={renderWidth}
      height={renderHeight}
      viewBox={`0 0 ${CHIBI_VIEWBOX.width} ${CHIBI_VIEWBOX.height}`}
      xmlns="http://www.w3.org/2000/svg"
      style={{
        transform: facingLeft ? "scaleX(-1)" : undefined,
        filter: glowFilter,
        overflow: "visible",
      }}
    >
      {/* ── 1. background effects (aura + ground shadow) ── */}
      <EffectsBack
        rarity={rarityKind}
        state={stateKind}
        palette={palette}
        outline={OUTLINE}
      />

      {/* ── 2. species back extras (tails / wings) ── */}
      <SpeciesExtrasBack
        species={speciesKind}
        walkPhase={walkPhase}
        outline={OUTLINE}
        primaryColor={speciesPrimary}
        accentColor={speciesAccent}
      />

      {/* ── 3. back hair (long tails, twin-tails, braid, etc.) ── */}
      <HairBack
        style={hairStyle}
        color={palette.hair}
        light={palette.hairLight}
        outline={OUTLINE}
      />

      {/* ── 4. headwear back layer (hood shell, ear bases) ── */}
      <HeadwearBack kind={headwearKind} palette={palette} outline={OUTLINE} />

      {/* ── 5. body armature + outfit, with walking bob on the whole stack ── */}
      <g transform={`translate(0, ${bodyBob})`}>
        <Body
          walkPhase={walkPhase}
          celebrating={celebrating}
          skinBase={SKIN_BASE}
          skinShade={SKIN_SHADE}
          outline={OUTLINE}
        />
        <Outfit
          role={roleKind}
          palette={palette}
          walkPhase={walkPhase}
          celebrating={celebrating}
          outline={OUTLINE}
        />
        <HeldItem
          role={roleKind}
          palette={palette}
          outline={OUTLINE}
          hidden={celebrating}
        />
      </g>

      {/* ── 6. head (face base → features → eyes → hair front → headwear front) ── */}
      <g transform={`translate(0, ${bodyBob * 0.5})`}>
        <FaceBase
          mood={moodKind}
          state={stateKind}
          outline={OUTLINE}
          skinBase={SKIN_BASE}
          skinShade={SKIN_SHADE}
          skinHighlight={SKIN_HIGHLIGHT}
          blush={BLUSH}
          lip={LIP}
          hairColor={hairColor}
        />
        <Eyes mood={moodKind} irisColor={palette.eye} outline={OUTLINE} />
        <FaceFeatures
          mood={moodKind}
          state={stateKind}
          outline={OUTLINE}
          skinBase={SKIN_BASE}
          skinShade={SKIN_SHADE}
          skinHighlight={SKIN_HIGHLIGHT}
          blush={BLUSH}
          lip={LIP}
          hairColor={hairColor}
        />
        <FaceAccessory kind={faceAccessoryKind} outline={OUTLINE} />
        <HairFront
          style={hairStyle}
          color={palette.hair}
          light={palette.hairLight}
          outline={OUTLINE}
        />
        <HeadwearFront kind={headwearKind} palette={palette} outline={OUTLINE} />
      </g>

      {/* ── 7. foreground effects (sparkles, emotes, state overlays) ── */}
      <EffectsFront
        rarity={rarityKind}
        state={stateKind}
        palette={palette}
        outline={OUTLINE}
      />
    </svg>
  );
}

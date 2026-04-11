"use client";

import React from "react";
import type { ChibiRole, PaletteSlot } from "../types";

// Coordinate constants re-declared (not imported from Body.tsx) to avoid a
// circular dep. Must stay in sync with CHIBI_VIEWBOX in ../types.ts.
const SHOULDER_LX = 40;
const SHOULDER_RX = 88;
const SHOULDER_Y = 116;
const HIP_LX = 54;
const HIP_RX = 74;
const HIP_Y = 158;
const PI = Math.PI;

export interface OutfitProps {
  role: ChibiRole;
  palette: PaletteSlot;
  walkPhase?: number;
  celebrating?: boolean;
  outline: string;
}

interface RoleCtx {
  palette: PaletteSlot;
  outline: string;
  leftArmAngle: number;
  rightArmAngle: number;
  leftLegAngle: number;
  rightLegAngle: number;
}

export function Outfit(props: OutfitProps): React.JSX.Element {
  const { role, palette, walkPhase, celebrating = false, outline } = props;
  const walking = walkPhase !== undefined;
  const phase = walkPhase ?? 0;
  const legSwing = walking ? Math.sin(phase * 2 * PI) * 20 : 0;
  const armSwing = walking ? Math.sin(phase * 2 * PI + PI) * 16 : 0;
  const ctx: RoleCtx = {
    palette,
    outline,
    leftArmAngle: celebrating ? -150 : +armSwing,
    rightArmAngle: celebrating ? 150 : -armSwing,
    leftLegAngle: +legSwing,
    rightLegAngle: -legSwing,
  };
  const gid = `of-${role}-${stableId(palette.outfitPrimary)}`;
  return (
    <g data-part="outfit">
      <Defs gid={gid} palette={palette} />
      {role === "director" && <DirectorOutfit gid={gid} ctx={ctx} />}
      {role === "coder" && <CoderOutfit gid={gid} ctx={ctx} />}
      {role === "reviewer" && <ReviewerOutfit gid={gid} ctx={ctx} />}
      {role === "tester" && <TesterOutfit gid={gid} ctx={ctx} />}
      {role === "writer" && <WriterOutfit gid={gid} ctx={ctx} />}
      {role === "designer" && <DesignerOutfit gid={gid} ctx={ctx} />}
      {role === "generic" && <GenericOutfit gid={gid} ctx={ctx} />}
    </g>
  );
}

// Stable id (FNV-ish) so each palette gets a unique gradient namespace.
function stableId(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h * 0x01000193) >>> 0;
  }
  return h.toString(36);
}

// Shared gradients. SVG has no color-mix(), so each gradient fades from the
// base hue to a 25%-30% black overlay at the bottom for shading.
function Defs({ gid, palette }: { gid: string; palette: PaletteSlot }) {
  return (
    <defs>
      <linearGradient id={`${gid}-primary`} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={palette.outfitPrimary} />
        <stop offset="55%" stopColor={palette.outfitPrimary} />
        <stop offset="100%" stopColor="#000" stopOpacity="0.25" />
      </linearGradient>
      <linearGradient id={`${gid}-secondary`} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={palette.outfitSecondary} />
        <stop offset="100%" stopColor="#000" stopOpacity="0.2" />
      </linearGradient>
      <linearGradient id={`${gid}-bottom`} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor={palette.outfitBottom} />
        <stop offset="100%" stopColor="#000" stopOpacity="0.3" />
      </linearGradient>
      <linearGradient id={`${gid}-white`} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stopColor="#ffffff" />
        <stop offset="100%" stopColor="#000" stopOpacity="0.18" />
      </linearGradient>
    </defs>
  );
}

// DIRECTOR — royal cloak + tunic, V-neck, gold belt, puffy short sleeves.
function DirectorOutfit({ gid, ctx }: { gid: string; ctx: RoleCtx }) {
  const { outline: stroke, leftArmAngle, rightArmAngle, leftLegAngle, rightLegAngle, palette } = ctx;
  const sw = 1.4;
  return (
    <>
      <g data-layer="cape">
        <path
          d="M 38 116 C 16 130, 12 160, 22 188 L 64 178 L 106 188 C 116 160, 112 130, 90 116 C 80 124, 48 124, 38 116 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 42 120 C 26 138, 24 162, 30 184" fill="none" stroke={palette.outfitSecondary} strokeWidth="2" strokeLinecap="round" />
        <path d="M 86 120 C 102 138, 104 162, 98 184" fill="none" stroke={palette.outfitSecondary} strokeWidth="2" strokeLinecap="round" />
        <circle cx={48} cy={116} r={2.4} fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.8" />
        <circle cx={80} cy={116} r={2.4} fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.8" />
        <path d="M 50 117 L 78 117" stroke={palette.outfitSecondary} strokeWidth="1.2" />
      </g>
      <g transform={`rotate(${rightArmAngle} ${SHOULDER_RX} ${SHOULDER_Y})`}>
        <path d="M 84 114 Q 96 116 98 124 Q 99 132 92 134 Q 84 132 82 124 Z" fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} />
        <path d="M 86 132 Q 92 134 96 132" stroke={palette.outfitSecondary} strokeWidth="1.4" fill="none" />
      </g>
      <g transform={`rotate(${rightLegAngle} ${HIP_RX} ${HIP_Y})`}>
        <path d="M 70 158 L 80 158 L 80 182 L 70 182 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
      </g>
      <g data-layer="torso">
        <path
          d="M 38 118 L 56 118 L 64 130 L 72 118 L 90 118 C 94 132, 94 148, 90 158 L 38 158 C 34 148, 34 132, 38 118 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 56 118 L 64 130 L 72 118" fill={palette.outfitBottom} stroke={stroke} strokeWidth={sw} strokeLinejoin="round" />
        <path d="M 42 124 L 42 154" stroke={palette.outfitSecondary} strokeWidth="1" strokeDasharray="2 2" />
        <path d="M 86 124 L 86 154" stroke={palette.outfitSecondary} strokeWidth="1" strokeDasharray="2 2" />
      </g>
      <g transform={`rotate(${leftArmAngle} ${SHOULDER_LX} ${SHOULDER_Y})`}>
        <path d="M 30 114 Q 44 116 46 124 Q 47 132 38 134 Q 30 132 28 124 Z" fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} />
        <path d="M 32 132 Q 38 134 44 132" stroke={palette.outfitSecondary} strokeWidth="1.4" fill="none" />
      </g>
      <g transform={`rotate(${leftLegAngle} ${HIP_LX} ${HIP_Y})`}>
        <path d="M 48 158 L 58 158 L 58 182 L 48 182 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
      </g>
      <g data-layer="trim">
        <rect x="36" y="148" width="56" height="6" fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} />
        <rect x="60" y="148" width="8" height="6" fill="#f2cd63" stroke={stroke} strokeWidth="0.9" />
        <circle cx={64} cy={151} r={1.4} fill={palette.outfitBottom} />
        <circle cx={40} cy={118} r={4} fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} />
        <circle cx={88} cy={118} r={4} fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} />
      </g>
    </>
  );
}

// CODER — oversized hoodie, hood lump, dashed zipper, kangaroo pocket.
function CoderOutfit({ gid, ctx }: { gid: string; ctx: RoleCtx }) {
  const { outline: stroke, leftArmAngle, rightArmAngle, leftLegAngle, rightLegAngle, palette } = ctx;
  const sw = 1.4;
  return (
    <>
      <g data-layer="hood">
        <path
          d="M 36 118 C 38 96, 56 90, 64 92 C 72 90, 90 96, 92 118 C 86 112, 72 110, 64 110 C 56 110, 42 112, 36 118 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 42 114 C 50 108, 78 108, 86 114" fill="none" stroke={palette.outfitSecondary} strokeWidth="1.2" />
      </g>
      <g transform={`rotate(${rightArmAngle} ${SHOULDER_RX} ${SHOULDER_Y})`}>
        <path
          d="M 82 116 Q 96 118 98 130 Q 100 148 96 158 Q 88 160 84 158 Q 80 148 80 130 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 84 156 Q 90 160 96 156 L 96 160 Q 90 162 84 160 Z" fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} />
      </g>
      <g transform={`rotate(${rightLegAngle} ${HIP_RX} ${HIP_Y})`}>
        <path d="M 70 158 L 80 158 L 80 184 L 70 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
        <path d="M 75 160 L 75 182" stroke={stroke} strokeWidth="0.7" strokeDasharray="2 2" opacity="0.5" />
      </g>
      <g data-layer="torso">
        <path
          d="M 34 116 C 32 132, 32 148, 36 160 L 92 160 C 96 148, 96 132, 94 116 C 88 118, 78 120, 64 120 C 50 120, 40 118, 34 116 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <line x1={64} y1={120} x2={64} y2={158} stroke={palette.outfitSecondary} strokeWidth="1.4" strokeDasharray="2 2" />
        <rect x="62.5" y="158" width="3" height="4" fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.6" />
        <path d="M 42 140 Q 42 152 50 152 L 64 152 L 64 144" fill="none" stroke={stroke} strokeWidth={sw} />
        <path d="M 86 140 Q 86 152 78 152 L 64 152 L 64 144" fill="none" stroke={stroke} strokeWidth={sw} />
        <path d="M 58 116 L 56 134" stroke={stroke} strokeWidth="1" />
        <path d="M 70 116 L 72 134" stroke={stroke} strokeWidth="1" />
        <circle cx={56} cy={135} r={1.3} fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.5" />
        <circle cx={72} cy={135} r={1.3} fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.5" />
      </g>
      <g transform={`rotate(${leftArmAngle} ${SHOULDER_LX} ${SHOULDER_Y})`}>
        <path
          d="M 30 116 Q 44 118 46 130 Q 48 148 44 158 Q 36 160 32 158 Q 28 148 28 130 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 32 156 Q 38 160 44 156 L 44 160 Q 38 162 32 160 Z" fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} />
      </g>
      <g transform={`rotate(${leftLegAngle} ${HIP_LX} ${HIP_Y})`}>
        <path d="M 48 158 L 58 158 L 58 184 L 48 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
        <path d="M 53 160 L 53 182" stroke={stroke} strokeWidth="0.7" strokeDasharray="2 2" opacity="0.5" />
      </g>
    </>
  );
}

// REVIEWER — scholar's vest + undershirt + button line + bow-tie.
function ReviewerOutfit({ gid, ctx }: { gid: string; ctx: RoleCtx }) {
  const { outline: stroke, leftArmAngle, rightArmAngle, leftLegAngle, rightLegAngle, palette } = ctx;
  const sw = 1.4;
  return (
    <>
      <g transform={`rotate(${rightArmAngle} ${SHOULDER_RX} ${SHOULDER_Y})`}>
        <path d="M 84 114 Q 95 116 96 128 Q 96 144 92 152 Q 86 152 84 150 Q 82 138 82 128 Z" fill={`url(#${gid}-white)`} stroke={stroke} strokeWidth={sw} />
        <rect x="84" y="148" width="10" height="3" fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.7" />
      </g>
      <g transform={`rotate(${rightLegAngle} ${HIP_RX} ${HIP_Y})`}>
        <path d="M 70 158 L 80 158 L 79 184 L 71 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
        <line x1="75" y1="160" x2="75" y2="182" stroke="#000" strokeOpacity="0.25" strokeWidth="0.7" />
      </g>
      <g data-layer="torso">
        <path
          d="M 38 116 L 90 116 C 92 130, 92 148, 90 158 L 38 158 C 36 148, 36 130, 38 116 Z"
          fill={`url(#${gid}-white)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path
          d="M 38 118 L 56 118 L 64 134 L 72 118 L 90 118 C 92 130, 92 148, 90 158 L 38 158 C 36 148, 36 130, 38 118 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 56 118 L 60 126 L 64 124 L 68 126 L 72 118" fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} strokeLinejoin="round" />
        <path
          d="M 60 122 L 56 119 L 56 126 L 60 124 L 64 126 L 68 124 L 72 126 L 72 119 L 68 122 Z"
          fill={palette.outfitBottom} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <circle cx={64} cy={123} r={1.2} fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.5" />
        <circle cx={64} cy={138} r={1.4} fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.6" />
        <circle cx={64} cy={146} r={1.4} fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.6" />
        <circle cx={64} cy={154} r={1.4} fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.6" />
      </g>
      <g transform={`rotate(${leftArmAngle} ${SHOULDER_LX} ${SHOULDER_Y})`}>
        <path d="M 30 114 Q 41 116 42 128 Q 42 144 38 152 Q 32 152 30 150 Q 28 138 28 128 Z" fill={`url(#${gid}-white)`} stroke={stroke} strokeWidth={sw} />
        <rect x="30" y="148" width="10" height="3" fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.7" />
      </g>
      <g transform={`rotate(${leftLegAngle} ${HIP_LX} ${HIP_Y})`}>
        <path d="M 48 158 L 58 158 L 57 184 L 49 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
        <line x1="53" y1="160" x2="53" y2="182" stroke="#000" strokeOpacity="0.25" strokeWidth="0.7" />
      </g>
      <path d="M 76 138 Q 82 142 80 150" fill="none" stroke="#f2cd63" strokeWidth="1.1" />
    </>
  );
}

// TESTER — open lab coat over coloured undershirt + utility belt.
function TesterOutfit({ gid, ctx }: { gid: string; ctx: RoleCtx }) {
  const { outline: stroke, leftArmAngle, rightArmAngle, leftLegAngle, rightLegAngle, palette } = ctx;
  const sw = 1.4;
  return (
    <>
      <g transform={`rotate(${rightArmAngle} ${SHOULDER_RX} ${SHOULDER_Y})`}>
        <path d="M 82 114 Q 96 116 98 126 Q 100 138 96 144 Q 88 144 84 142 Q 80 130 80 124 Z" fill={`url(#${gid}-white)`} stroke={stroke} strokeWidth={sw} />
        <path d="M 84 142 L 96 144 L 96 148 L 84 146 Z" fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} />
      </g>
      <g transform={`rotate(${rightLegAngle} ${HIP_RX} ${HIP_Y})`}>
        <path d="M 70 158 L 80 158 L 80 184 L 70 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
      </g>
      <g data-layer="torso">
        <path
          d="M 44 116 L 84 116 C 86 132, 86 148, 84 158 L 44 158 C 42 148, 42 132, 44 116 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 56 116 L 64 124 L 72 116" fill="none" stroke={stroke} strokeWidth={sw} />
        <path
          d="M 38 116 C 32 130, 32 150, 36 162 L 56 162 L 56 134 L 50 116 Z"
          fill={`url(#${gid}-white)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 50 116 L 56 134 L 56 122 L 52 116 Z" fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.9" />
        <path
          d="M 90 116 C 96 130, 96 150, 92 162 L 72 162 L 72 134 L 78 116 Z"
          fill={`url(#${gid}-white)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 78 116 L 72 134 L 72 122 L 76 116 Z" fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.9" />
        <rect x="38" y="146" width="12" height="8" fill="none" stroke={stroke} strokeWidth="1" />
        <rect x="78" y="146" width="12" height="8" fill="none" stroke={stroke} strokeWidth="1" />
      </g>
      <g transform={`rotate(${leftArmAngle} ${SHOULDER_LX} ${SHOULDER_Y})`}>
        <path d="M 30 114 Q 44 116 46 126 Q 46 138 42 144 Q 34 144 30 142 Q 28 130 28 124 Z" fill={`url(#${gid}-white)`} stroke={stroke} strokeWidth={sw} />
        <path d="M 30 142 L 42 144 L 42 148 L 30 146 Z" fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} />
      </g>
      <g transform={`rotate(${leftLegAngle} ${HIP_LX} ${HIP_Y})`}>
        <path d="M 48 158 L 58 158 L 58 184 L 48 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
      </g>
      <g data-layer="trim">
        <rect x="36" y="152" width="56" height="6" fill={palette.outfitBottom} stroke={stroke} strokeWidth={sw} />
        <rect x="44" y="151" width="6" height="8" fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.8" />
        <rect x="58" y="151" width="6" height="8" fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.8" />
        <rect x="72" y="151" width="6" height="8" fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.8" />
        <rect x="62" y="153" width="4" height="4" fill="#f2cd63" stroke={stroke} strokeWidth="0.6" />
      </g>
    </>
  );
}

// WRITER — long flowing robe with A-line skirt and wide drape sleeves.
function WriterOutfit({ gid, ctx }: { gid: string; ctx: RoleCtx }) {
  const { outline: stroke, leftArmAngle, rightArmAngle, leftLegAngle, rightLegAngle, palette } = ctx;
  const sw = 1.4;
  return (
    <>
      <g transform={`rotate(${rightArmAngle} ${SHOULDER_RX} ${SHOULDER_Y})`}>
        <path
          d="M 82 114 Q 100 118 104 132 Q 106 146 100 154 Q 90 156 84 152 Q 80 138 80 126 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 84 152 Q 94 156 100 154" stroke={palette.outfitSecondary} strokeWidth="2" fill="none" />
      </g>
      <g transform={`rotate(${rightLegAngle * 0.4} ${HIP_RX} ${HIP_Y})`}>
        <path d="M 64 140 L 88 140 L 104 188 L 64 188 Z" fill={`url(#${gid}-secondary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round" />
      </g>
      <g data-layer="torso">
        <path
          d="M 38 116 L 90 116 C 92 128, 92 138, 90 142 L 38 142 C 36 138, 36 128, 38 116 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 54 116 Q 64 124 74 116" fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} />
        <path
          d="M 36 140 L 92 140 L 108 188 L 20 188 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 48 142 L 38 184" stroke={palette.outfitSecondary} strokeWidth="1.1" fill="none" />
        <path d="M 64 142 L 64 186" stroke={palette.outfitSecondary} strokeWidth="1.1" fill="none" />
        <path d="M 80 142 L 90 184" stroke={palette.outfitSecondary} strokeWidth="1.1" fill="none" />
        <path d="M 20 188 Q 64 184 108 188" fill="none" stroke={palette.outfitSecondary} strokeWidth="2" />
        <rect x="36" y="138" width="56" height="5" fill={palette.outfitBottom} stroke={stroke} strokeWidth={sw} />
        <path d="M 60 143 L 56 152 L 64 148 L 72 152 L 68 143" fill={palette.outfitBottom} stroke={stroke} strokeWidth={sw} strokeLinejoin="round" />
      </g>
      <g transform={`rotate(${leftArmAngle} ${SHOULDER_LX} ${SHOULDER_Y})`}>
        <path
          d="M 30 114 Q 24 118 22 132 Q 22 146 28 154 Q 38 156 44 152 Q 48 138 46 126 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 28 154 Q 38 156 44 152" stroke={palette.outfitSecondary} strokeWidth="2" fill="none" />
      </g>
      <g transform={`rotate(${leftLegAngle * 0.4} ${HIP_LX} ${HIP_Y})`}>
        <path d="M 60 142 L 60 188" stroke={palette.outfitSecondary} strokeWidth="1.4" fill="none" />
      </g>
    </>
  );
}

// DESIGNER — coloured undershirt with apron-style bib + paint splatters.
function DesignerOutfit({ gid, ctx }: { gid: string; ctx: RoleCtx }) {
  const { outline: stroke, leftArmAngle, rightArmAngle, leftLegAngle, rightLegAngle, palette } = ctx;
  const sw = 1.4;
  return (
    <>
      <g transform={`rotate(${rightArmAngle} ${SHOULDER_RX} ${SHOULDER_Y})`}>
        <path d="M 84 114 Q 95 116 96 126 Q 96 138 92 144 Q 86 144 84 142 Q 82 130 82 124 Z" fill={`url(#${gid}-secondary)`} stroke={stroke} strokeWidth={sw} />
      </g>
      <g transform={`rotate(${rightLegAngle} ${HIP_RX} ${HIP_Y})`}>
        <path d="M 70 158 L 80 158 L 80 184 L 70 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
        <circle cx={75} cy={170} r={1.6} fill={palette.outfitPrimary} />
        <circle cx={73} cy={176} r={1} fill={palette.outfitPrimary} />
      </g>
      <g data-layer="torso">
        <path
          d="M 38 116 L 90 116 C 92 132, 92 148, 90 158 L 38 158 C 36 148, 36 132, 38 116 Z"
          fill={`url(#${gid}-secondary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 50 116 L 54 132" stroke={stroke} strokeWidth="2" fill="none" />
        <path d="M 78 116 L 74 132" stroke={stroke} strokeWidth="2" fill="none" />
        <rect x="48" y="114" width="6" height="3" fill={palette.outfitPrimary} stroke={stroke} strokeWidth="0.8" />
        <rect x="74" y="114" width="6" height="3" fill={palette.outfitPrimary} stroke={stroke} strokeWidth="0.8" />
        <path
          d="M 52 130 L 76 130 L 84 150 L 64 162 L 44 150 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <rect x="56" y="142" width="16" height="8" fill="none" stroke={stroke} strokeWidth="1" />
        <line x1="64" y1="142" x2="64" y2="150" stroke={stroke} strokeWidth="0.8" />
        <circle cx={60} cy={138} r={1.4} fill={palette.outfitSecondary} />
        <circle cx={72} cy={134} r={1.1} fill={palette.outfitBottom} />
        <circle cx={68} cy={154} r={1} fill={palette.outfitSecondary} />
      </g>
      <g transform={`rotate(${leftArmAngle} ${SHOULDER_LX} ${SHOULDER_Y})`}>
        <path d="M 30 114 Q 41 116 42 126 Q 42 138 38 144 Q 32 144 30 142 Q 28 130 28 124 Z" fill={`url(#${gid}-secondary)`} stroke={stroke} strokeWidth={sw} />
      </g>
      <g transform={`rotate(${leftLegAngle} ${HIP_LX} ${HIP_Y})`}>
        <path d="M 48 158 L 58 158 L 58 184 L 48 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
        <circle cx={53} cy={172} r={1.4} fill={palette.outfitSecondary} />
      </g>
    </>
  );
}

// GENERIC — short-sleeve tee + pants + simple belt with stitching detail.
function GenericOutfit({ gid, ctx }: { gid: string; ctx: RoleCtx }) {
  const { outline: stroke, leftArmAngle, rightArmAngle, leftLegAngle, rightLegAngle, palette } = ctx;
  const sw = 1.4;
  return (
    <>
      <g transform={`rotate(${rightArmAngle} ${SHOULDER_RX} ${SHOULDER_Y})`}>
        <path d="M 82 114 Q 96 116 97 124 Q 97 132 90 134 Q 84 132 82 124 Z" fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} />
        <path d="M 84 132 Q 90 134 96 132" stroke={stroke} strokeWidth="0.8" fill="none" strokeDasharray="1.5 1.5" />
      </g>
      <g transform={`rotate(${rightLegAngle} ${HIP_RX} ${HIP_Y})`}>
        <path d="M 70 158 L 80 158 L 80 184 L 70 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
        <line x1="75" y1="160" x2="75" y2="182" stroke="#000" strokeOpacity="0.2" strokeWidth="0.6" strokeDasharray="2 2" />
      </g>
      <g data-layer="torso">
        <path
          d="M 38 116 L 90 116 C 92 130, 92 148, 90 158 L 38 158 C 36 148, 36 130, 38 116 Z"
          fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} strokeLinejoin="round"
        />
        <path d="M 56 116 Q 64 124 72 116" fill={palette.outfitSecondary} stroke={stroke} strokeWidth={sw} />
        <line x1="64" y1="124" x2="64" y2="148" stroke={stroke} strokeWidth="0.7" strokeDasharray="2 2" opacity="0.5" />
        <path d="M 40 122 L 40 154" stroke="#000" strokeOpacity="0.15" strokeWidth="1.4" />
        <path d="M 88 122 L 88 154" stroke="#000" strokeOpacity="0.15" strokeWidth="1.4" />
      </g>
      <g transform={`rotate(${leftArmAngle} ${SHOULDER_LX} ${SHOULDER_Y})`}>
        <path d="M 30 114 Q 44 116 46 124 Q 46 132 38 134 Q 32 132 30 124 Z" fill={`url(#${gid}-primary)`} stroke={stroke} strokeWidth={sw} />
        <path d="M 32 132 Q 38 134 44 132" stroke={stroke} strokeWidth="0.8" fill="none" strokeDasharray="1.5 1.5" />
      </g>
      <g transform={`rotate(${leftLegAngle} ${HIP_LX} ${HIP_Y})`}>
        <path d="M 48 158 L 58 158 L 58 184 L 48 184 Z" fill={`url(#${gid}-bottom)`} stroke={stroke} strokeWidth={sw} />
        <line x1="53" y1="160" x2="53" y2="182" stroke="#000" strokeOpacity="0.2" strokeWidth="0.6" strokeDasharray="2 2" />
      </g>
      <g data-layer="trim">
        <rect x="36" y="150" width="56" height="5" fill={palette.outfitBottom} stroke={stroke} strokeWidth={sw} />
        <rect x="62" y="150" width="4" height="5" fill={palette.outfitSecondary} stroke={stroke} strokeWidth="0.7" />
      </g>
    </>
  );
}

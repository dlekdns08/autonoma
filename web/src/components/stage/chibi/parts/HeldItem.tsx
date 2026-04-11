"use client";

import type React from "react";
import { useId } from "react";
import type { ChibiRole, PaletteSlot } from "../types";

export interface HeldItemProps {
  role: ChibiRole;
  palette: PaletteSlot;
  outline: string;
  /** hide the item when celebrating or arms raised */
  hidden?: boolean;
}

const STROKE = "1.2";

/**
 * HeldItem — a small MapleStory-style prop drawn in the chibi's right hand.
 * Positioned at the static right-hand rest position (~100, 150) and does not
 * follow arm sway (kept readable because the sway is small).
 */
export function HeldItem(props: HeldItemProps): React.JSX.Element | null {
  const { role, palette, outline, hidden } = props;
  if (hidden) return null;
  switch (role) {
    case "director":
      return <DirectorScepter palette={palette} outline={outline} />;
    case "coder":
      return <CoderOrb palette={palette} outline={outline} />;
    case "reviewer":
      return <ReviewerMagnifier palette={palette} outline={outline} />;
    case "tester":
      return <TesterFlask palette={palette} outline={outline} />;
    case "writer":
      return <WriterBook palette={palette} outline={outline} />;
    case "designer":
      return <DesignerBrush palette={palette} outline={outline} />;
    case "generic":
    default:
      return <GenericGem palette={palette} outline={outline} />;
  }
}

interface SubProps {
  palette: PaletteSlot;
  outline: string;
}

/* director — golden scepter with 5-point star and gem ------------- */
function DirectorScepter({ palette, outline }: SubProps): React.JSX.Element {
  const uid = useId();
  const gRod = `${uid}-item-director-rod`;
  const gGem = `${uid}-item-director-gem`;
  return (
    <g>
      <defs>
        <linearGradient id={gRod} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#c8931a" />
          <stop offset="0.5" stopColor="#ffe28a" />
          <stop offset="1" stopColor="#a67112" />
        </linearGradient>
        <radialGradient id={gGem} cx="0.35" cy="0.3" r="0.8">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.4" stopColor={palette.aura} />
          <stop offset="1" stopColor={palette.outfitBottom} />
        </radialGradient>
      </defs>
      <line x1="100" y1="128" x2="100" y2="164" stroke={`url(#${gRod})`} strokeWidth="3" strokeLinecap="round" />
      <line x1="100" y1="128" x2="100" y2="164" stroke={outline} strokeWidth={STROKE} strokeLinecap="round" opacity="0.55" />
      <circle cx="100" cy="165" r="2.2" fill="#ffe28a" stroke={outline} strokeWidth={STROKE} />
      <polygon
        points="100,118 102.1,124 108.4,124 103.3,127.8 105.3,134 100,130.2 94.7,134 96.7,127.8 91.6,124 97.9,124"
        fill="#ffe28a"
        stroke={outline}
        strokeWidth={STROKE}
        strokeLinejoin="round"
      />
      <circle cx="100" cy="126" r="1.9" fill={`url(#${gGem})`} stroke={outline} strokeWidth="0.8" />
      <circle cx="107" cy="119" r="0.8" fill="#ffffff" opacity="0.9" />
      <circle cx="93" cy="132" r="0.6" fill="#ffffff" opacity="0.7" />
    </g>
  );
}

/* coder — glowing magic orb --------------------------------------- */
function CoderOrb({ palette, outline }: SubProps): React.JSX.Element {
  const uid = useId();
  const gOrb = `${uid}-item-coder-orb`;
  const gGlow = `${uid}-item-coder-glow`;
  return (
    <g>
      <defs>
        <radialGradient id={gOrb} cx="0.35" cy="0.3" r="0.9">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.35" stopColor={palette.outfitSecondary} />
          <stop offset="0.8" stopColor={palette.aura} />
          <stop offset="1" stopColor={palette.outfitBottom} />
        </radialGradient>
        <radialGradient id={gGlow} cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" stopColor={palette.aura} stopOpacity="0.6" />
          <stop offset="1" stopColor={palette.aura} stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="100" cy="148" r="11" fill={`url(#${gGlow})`} />
      <circle cx="100" cy="148" r="6" fill={`url(#${gOrb})`} stroke={outline} strokeWidth={STROKE} />
      <circle cx="98.5" cy="146.5" r="1.8" fill="#ffffff" opacity="0.95" />
      <circle cx="101.5" cy="150" r="0.9" fill="#ffffff" opacity="0.6" />
      <circle cx="90" cy="142" r="0.7" fill={palette.aura} opacity="0.9" />
      <circle cx="110" cy="145" r="0.6" fill={palette.aura} opacity="0.8" />
      <circle cx="94" cy="158" r="0.6" fill={palette.aura} opacity="0.8" />
      <circle cx="108" cy="156" r="0.8" fill={palette.aura} opacity="0.9" />
      <circle cx="100" cy="136" r="0.5" fill="#ffffff" opacity="0.9" />
    </g>
  );
}

/* reviewer — magnifying glass ------------------------------------- */
function ReviewerMagnifier({ palette, outline }: SubProps): React.JSX.Element {
  const uid = useId();
  const gRing = `${uid}-item-reviewer-ring`;
  const gGlass = `${uid}-item-reviewer-glass`;
  const warm = isWarm(palette.aura);
  const ringColor = warm ? "#d8961a" : "#3a2a4a";
  return (
    <g>
      <defs>
        <linearGradient id={gRing} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={warm ? "#ffe28a" : "#5a4a6a"} />
          <stop offset="1" stopColor={ringColor} />
        </linearGradient>
        <radialGradient id={gGlass} cx="0.35" cy="0.3" r="0.8">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.75" />
          <stop offset="0.6" stopColor="#ffffff" stopOpacity="0.25" />
          <stop offset="1" stopColor={palette.aura} stopOpacity="0.15" />
        </radialGradient>
      </defs>
      <line x1="106" y1="154" x2="114" y2="166" stroke={`url(#${gRing})`} strokeWidth="3" strokeLinecap="round" />
      <line x1="106" y1="154" x2="114" y2="166" stroke={outline} strokeWidth={STROKE} strokeLinecap="round" opacity="0.55" />
      <circle cx="100" cy="148" r="6.2" fill={`url(#${gGlass})`} />
      <circle cx="100" cy="148" r="6.2" fill="#ffffff" opacity="0.3" />
      <circle cx="100" cy="148" r="7" fill="none" stroke={`url(#${gRing})`} strokeWidth="2.2" />
      <circle cx="100" cy="148" r="7" fill="none" stroke={outline} strokeWidth={STROKE} opacity="0.55" />
      <path d="M96.5 145 Q99 143 101.5 144.5" stroke="#ffffff" strokeWidth="1" strokeLinecap="round" fill="none" opacity="0.9" />
    </g>
  );
}

/* tester — potion flask ------------------------------------------- */
function TesterFlask({ palette, outline }: SubProps): React.JSX.Element {
  const uid = useId();
  const gLiquid = `${uid}-item-tester-liquid`;
  const gGlass = `${uid}-item-tester-glass`;
  return (
    <g>
      <defs>
        <linearGradient id={gLiquid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={palette.outfitSecondary} />
          <stop offset="0.5" stopColor={palette.outfitPrimary} />
          <stop offset="1" stopColor={palette.outfitBottom} />
        </linearGradient>
        <radialGradient id={gGlass} cx="0.3" cy="0.3" r="0.9">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.7" />
          <stop offset="0.7" stopColor="#ffffff" stopOpacity="0.15" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect x="97.5" y="140" width="5" height="8" fill={palette.outfitSecondary} stroke={outline} strokeWidth={STROKE} opacity="0.85" />
      <circle cx="100" cy="152" r="6" fill={`url(#${gLiquid})`} stroke={outline} strokeWidth={STROKE} />
      <circle cx="100" cy="152" r="6" fill={`url(#${gGlass})`} />
      <path d="M94.2 151 Q100 149 105.8 151" stroke="#ffffff" strokeWidth="0.9" fill="none" opacity="0.55" />
      <rect x="96.5" y="137" width="7" height="3.5" rx="0.8" fill="#b07a3a" stroke={outline} strokeWidth={STROKE} />
      <circle cx="98" cy="153.5" r="1" fill="#ffffff" opacity="0.85" />
      <circle cx="102" cy="155" r="0.6" fill="#ffffff" opacity="0.6" />
      <circle cx="100" cy="133" r="0.8" fill={palette.aura} opacity="0.9" />
      <circle cx="104" cy="131" r="0.6" fill={palette.aura} opacity="0.75" />
      <circle cx="96" cy="130" r="0.5" fill={palette.aura} opacity="0.7" />
    </g>
  );
}

/* writer — open book ---------------------------------------------- */
function WriterBook({ palette, outline }: SubProps): React.JSX.Element {
  const uid = useId();
  const gPage = `${uid}-item-writer-page`;
  const gGlow = `${uid}-item-writer-glow`;
  const x = 95;
  const y = 145;
  const scribble = (sx: number, sy: number, len: number) => (
    <line x1={sx} y1={sy} x2={sx + len} y2={sy} stroke={outline} strokeWidth="0.5" opacity="0.65" />
  );
  return (
    <g>
      <defs>
        <linearGradient id={gPage} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="1" stopColor={palette.outfitSecondary} />
        </linearGradient>
        <radialGradient id={gGlow} cx="0.5" cy="0.5" r="0.6">
          <stop offset="0" stopColor={palette.aura} stopOpacity="0.45" />
          <stop offset="1" stopColor={palette.aura} stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="102" cy="150" rx="12" ry="9" fill={`url(#${gGlow})`} />
      <rect x={x} y={y} width="14" height="10" rx="0.8" fill={`url(#${gPage})`} stroke={outline} strokeWidth={STROKE} />
      <line x1="102" y1={y + 0.5} x2="102" y2={y + 9.5} stroke={outline} strokeWidth={STROKE} opacity="0.75" />
      <path d={`M${x + 0.5} ${y + 9.5} Q102 ${y + 10.5} ${x + 13.5} ${y + 9.5}`} stroke={outline} strokeWidth="0.6" fill="none" opacity="0.35" />
      {scribble(x + 2, y + 3, 4)}
      {scribble(x + 2, y + 5, 4)}
      {scribble(x + 2, y + 7, 3.5)}
      {scribble(x + 8, y + 3, 4)}
      {scribble(x + 8, y + 5, 4)}
      {scribble(x + 8, y + 7, 3.5)}
    </g>
  );
}

/* designer — paint brush ------------------------------------------ */
function DesignerBrush({ palette, outline }: SubProps): React.JSX.Element {
  const uid = useId();
  const gHandle = `${uid}-item-designer-handle`;
  const gBristle = `${uid}-item-designer-bristle`;
  return (
    <g>
      <defs>
        <linearGradient id={gHandle} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#d49a5e" />
          <stop offset="0.5" stopColor="#b2743a" />
          <stop offset="1" stopColor="#7a4a1e" />
        </linearGradient>
        <linearGradient id={gBristle} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={palette.outfitSecondary} />
          <stop offset="1" stopColor={palette.outfitPrimary} />
        </linearGradient>
      </defs>
      <line x1="96" y1="130" x2="110" y2="160" stroke={`url(#${gHandle})`} strokeWidth="3" strokeLinecap="round" />
      <line x1="96" y1="130" x2="110" y2="160" stroke={outline} strokeWidth={STROKE} strokeLinecap="round" opacity="0.55" />
      <line x1="107.3" y1="154.3" x2="112" y2="164.1" stroke="#c9c9d0" strokeWidth="3.3" strokeLinecap="round" />
      <line x1="107.3" y1="154.3" x2="112" y2="164.1" stroke={outline} strokeWidth={STROKE} strokeLinecap="round" opacity="0.55" />
      <path d="M109 161 L116 167 L113.5 170 L108 164 Z" fill={`url(#${gBristle})`} stroke={outline} strokeWidth={STROKE} strokeLinejoin="round" />
      <circle cx="118" cy="170" r="0.9" fill={palette.outfitPrimary} stroke={outline} strokeWidth="0.5" />
      <circle cx="115" cy="173" r="0.6" fill={palette.outfitPrimary} opacity="0.85" />
      <circle cx="120" cy="165" r="0.5" fill={palette.outfitPrimary} opacity="0.8" />
    </g>
  );
}

/* generic — diamond gem ------------------------------------------- */
function GenericGem({ palette, outline }: SubProps): React.JSX.Element {
  const uid = useId();
  const gGem = `${uid}-item-generic-gem`;
  const gGlow = `${uid}-item-generic-glow`;
  return (
    <g>
      <defs>
        <linearGradient id={gGem} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="0.35" stopColor={palette.outfitSecondary} />
          <stop offset="1" stopColor={palette.aura} />
        </linearGradient>
        <radialGradient id={gGlow} cx="0.5" cy="0.5" r="0.55">
          <stop offset="0" stopColor={palette.aura} stopOpacity="0.5" />
          <stop offset="1" stopColor={palette.aura} stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="100" cy="150" r="9" fill={`url(#${gGlow})`} />
      <polygon
        points="100,146 104,150 100,154 96,150"
        fill={`url(#${gGem})`}
        stroke={outline}
        strokeWidth={STROKE}
        strokeLinejoin="round"
      />
      <line x1="98.5" y1="148.5" x2="101.5" y2="151.5" stroke="#ffffff" strokeWidth="0.6" opacity="0.9" />
      <line x1="101.5" y1="148.5" x2="98.5" y2="151.5" stroke="#ffffff" strokeWidth="0.5" opacity="0.7" />
      <circle cx="100" cy="147.6" r="0.6" fill="#ffffff" opacity="0.95" />
    </g>
  );
}

/* helpers ---------------------------------------------------------- */
function isWarm(hex: string): boolean {
  if (!hex || hex[0] !== "#" || hex.length < 7) return false;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return r > b && r >= g - 20;
}

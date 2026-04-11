"use client";

import type * as React from "react";
import type { ChibiRole, ChibiSpecies, PaletteSlot } from "../types";

export type HeadwearKind =
  | "none"
  | "wizardHat"
  | "crown"
  | "hood"
  | "headband"
  | "beret"
  | "catEars"
  | "bunnyEars"
  | "foxEars"
  | "bearEars"
  | "pandaEars"
  | "penguinFluff"
  | "hamsterEars"
  | "dogEars"
  | "owlFeathers"
  | "duckBeak";

export interface HeadwearProps {
  kind: HeadwearKind;
  palette: PaletteSlot;
  outline: string;
}

const SW = "1.6";

/* ----------------------------- pickHeadwear ---------------------------- */

export function pickHeadwear(
  role: ChibiRole,
  species: ChibiSpecies,
): HeadwearKind {
  if (species !== "human") {
    const m: Record<Exclude<ChibiSpecies, "human">, HeadwearKind> = {
      cat: "catEars",
      rabbit: "bunnyEars",
      fox: "foxEars",
      bear: "bearEars",
      panda: "pandaEars",
      penguin: "penguinFluff",
      hamster: "hamsterEars",
      dog: "dogEars",
      owl: "owlFeathers",
      duck: "none",
    };
    return m[species] ?? "none";
  }
  const r: Record<ChibiRole, HeadwearKind> = {
    director: "crown",
    designer: "beret",
    writer: "headband",
    tester: "headband",
    coder: "hood",
    reviewer: "none",
    generic: "none",
  };
  return r[role] ?? "none";
}

/* ------------------------------ helpers -------------------------------- */

function shade(hex: string, amount: number): string {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!m) return hex;
  const ch = [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
  const t = amount < 0 ? 0 : 255;
  const p = Math.abs(amount);
  const out = ch
    .map((c) => Math.round((t - c) * p + c))
    .map((n) => Math.max(0, Math.min(255, n)).toString(16).padStart(2, "0"))
    .join("");
  return `#${out}`;
}

/* --------------------------- piece renderers --------------------------- */

function WizardHat({ palette, outline }: HeadwearProps) {
  const dark = shade(palette.outfitPrimary, -0.35);
  const light = shade(palette.outfitPrimary, 0.15);
  return (
    <g>
      <defs>
        <linearGradient id="hw-wizardHat-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={dark} />
          <stop offset="55%" stopColor={palette.outfitPrimary} />
          <stop offset="100%" stopColor={light} />
        </linearGradient>
      </defs>
      <path
        d="M28 22 Q64 14 100 22 Q100 26 64 28 Q28 26 28 22 Z"
        fill={dark}
        stroke={outline}
        strokeWidth={SW}
        strokeLinejoin="round"
      />
      <path
        d="M40 22 Q56 12 64 6 Q72 -2 80 -10 Q86 4 78 14 Q70 22 64 22 Q52 22 40 22 Z"
        fill="url(#hw-wizardHat-grad)"
        stroke={outline}
        strokeWidth={SW}
        strokeLinejoin="round"
      />
      <path
        d="M34 21 Q64 17 96 21 L96 24 Q64 28 32 24 Z"
        fill={palette.outfitSecondary}
        stroke={outline}
        strokeWidth={SW}
        strokeLinejoin="round"
      />
      <path
        d="M70 14 L72 10 L74 14 L78 14 L75 17 L76 21 L72 19 L68 21 L69 17 L66 14 Z"
        fill={palette.aura}
        stroke={outline}
        strokeWidth="1"
        strokeLinejoin="round"
      />
    </g>
  );
}

function Crown({ palette, outline }: HeadwearProps) {
  return (
    <g>
      <defs>
        <linearGradient id="hw-crown-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#fff1a8" />
          <stop offset="60%" stopColor="#f4c842" />
          <stop offset="100%" stopColor="#a0760a" />
        </linearGradient>
      </defs>
      <rect
        x="36"
        y="20"
        width="56"
        height="6"
        rx="1.5"
        fill="url(#hw-crown-grad)"
        stroke={outline}
        strokeWidth={SW}
      />
      <path
        d="M36 20 L42 12 L48 20 L54 16 L60 20 L64 10 L68 20 L74 16 L80 20 L86 12 L92 20 Z"
        fill="url(#hw-crown-grad)"
        stroke={outline}
        strokeWidth={SW}
        strokeLinejoin="round"
      />
      <g fill={outline}>
        {[40, 48, 56, 64, 72, 80, 88].map((x) => (
          <circle key={x} cx={x} cy="23" r="0.7" />
        ))}
      </g>
      <circle
        cx="48"
        cy="17"
        r="1.6"
        fill={palette.outfitSecondary}
        stroke={outline}
        strokeWidth="0.8"
      />
      <circle
        cx="64"
        cy="14"
        r="2"
        fill={palette.aura}
        stroke={outline}
        strokeWidth="0.8"
      />
      <circle
        cx="80"
        cy="17"
        r="1.6"
        fill={palette.outfitSecondary}
        stroke={outline}
        strokeWidth="0.8"
      />
    </g>
  );
}

function HoodBack({ palette, outline }: HeadwearProps) {
  const dark = shade(palette.outfitPrimary, -0.3);
  return (
    <g>
      <defs>
        <radialGradient id="hw-hood-back-grad" cx="0.5" cy="0.4" r="0.7">
          <stop offset="0%" stopColor={palette.outfitPrimary} />
          <stop offset="100%" stopColor={dark} />
        </radialGradient>
      </defs>
      <path
        d="M18 30 Q14 12 40 8 Q64 4 88 8 Q114 12 110 30 Q116 50 108 64 Q96 70 88 64 Q88 40 64 38 Q40 40 40 64 Q32 70 20 64 Q12 50 18 30 Z"
        fill="url(#hw-hood-back-grad)"
        stroke={outline}
        strokeWidth={SW}
        strokeLinejoin="round"
      />
    </g>
  );
}

function HoodFront({ palette, outline }: HeadwearProps) {
  const dark = shade(palette.outfitPrimary, -0.45);
  const light = shade(palette.outfitPrimary, 0.1);
  return (
    <g>
      <path d="M22 36 Q24 18 48 14 Q64 10 80 14 Q104 18 106 36 Q102 30 86 28 Q64 24 42 28 Q26 30 22 36 Z" fill={dark} stroke={outline} strokeWidth={SW} strokeLinejoin="round" />
      <path d="M28 34 Q40 26 64 26 Q88 26 100 34" fill="none" stroke={light} strokeWidth="1.2" strokeLinecap="round" />
      <path d="M48 56 Q52 64 50 70" fill="none" stroke={outline} strokeWidth={SW} strokeLinecap="round" />
      <path d="M80 56 Q76 64 78 70" fill="none" stroke={outline} strokeWidth={SW} strokeLinecap="round" />
      <circle cx="50" cy="71" r="1.6" fill={palette.outfitSecondary} stroke={outline} strokeWidth="0.8" />
      <circle cx="78" cy="71" r="1.6" fill={palette.outfitSecondary} stroke={outline} strokeWidth="0.8" />
    </g>
  );
}

function Headband({ palette, outline }: HeadwearProps) {
  const sec = palette.outfitSecondary;
  return (
    <g fill={sec} stroke={outline} strokeWidth={SW} strokeLinejoin="round">
      <path d="M26 26 Q64 20 102 26 L102 30 Q64 24 26 30 Z" />
      <path d="M92 26 Q84 18 82 26 Q84 32 92 28 Z" />
      <path d="M92 26 Q100 18 102 26 Q100 32 92 28 Z" />
      <path d="M92 24 Q88 16 94 16 Q98 18 94 24 Z" />
      <path d="M92 28 Q88 36 94 36 Q98 34 94 28 Z" />
      <circle cx="92" cy="26" r="2" fill={palette.outfitPrimary} />
      <path d="M92 28 Q90 38 88 44 L92 42 L94 46 Q96 38 96 28 Z" />
    </g>
  );
}

function Beret({ palette, outline }: HeadwearProps) {
  const dark = shade(palette.outfitPrimary, -0.3);
  const light = shade(palette.outfitPrimary, 0.18);
  return (
    <g>
      <defs>
        <radialGradient id="hw-beret-grad" cx="0.4" cy="0.35" r="0.7">
          <stop offset="0%" stopColor={light} />
          <stop offset="100%" stopColor={palette.outfitPrimary} />
        </radialGradient>
      </defs>
      <path
        d="M30 22 Q26 12 50 8 Q72 4 92 10 Q104 14 100 22 Q92 26 64 26 Q36 26 30 22 Z"
        fill="url(#hw-beret-grad)"
        stroke={outline}
        strokeWidth={SW}
        strokeLinejoin="round"
      />
      <path
        d="M32 22 Q64 28 100 22"
        fill="none"
        stroke={dark}
        strokeWidth="1.2"
        strokeLinecap="round"
      />
      <circle
        cx="76"
        cy="8"
        r="1.8"
        fill={dark}
        stroke={outline}
        strokeWidth={SW}
      />
    </g>
  );
}

/* ----------------------------- ANIMAL EARS ----------------------------- */

function CatEarsBack({ outline }: HeadwearProps) {
  return (
    <g fill="#000" opacity="0.18" stroke={outline} strokeWidth={SW} strokeLinejoin="round">
      <path d="M30 24 L40 12 L48 24 Z" />
      <path d="M80 24 L88 12 L98 24 Z" />
    </g>
  );
}

function CatEarsFront({ palette, outline }: HeadwearProps) {
  return (
    <g stroke={outline} strokeLinejoin="round">
      <path d="M30 26 L40 12 L48 26 Z" fill={palette.hair} strokeWidth={SW} />
      <path d="M80 26 L88 12 L98 26 Z" fill={palette.hair} strokeWidth={SW} />
      <path d="M34 24 L40 16 L44 24 Z" fill="#ff9fb5" strokeWidth="0.8" />
      <path d="M84 24 L88 16 L94 24 Z" fill="#ff9fb5" strokeWidth="0.8" />
    </g>
  );
}

function BunnyEarsBack({ outline }: HeadwearProps) {
  return (
    <g fill="#000" opacity="0.15" stroke={outline} strokeWidth={SW}>
      <ellipse cx="40" cy="16" rx="6" ry="14" />
      <ellipse cx="88" cy="16" rx="6" ry="14" />
    </g>
  );
}

function BunnyEarsFront({ palette, outline }: HeadwearProps) {
  return (
    <g stroke={outline}>
      <ellipse cx="40" cy="14" rx="6" ry="13" fill={palette.hair} strokeWidth={SW} />
      <ellipse cx="88" cy="14" rx="6" ry="13" fill={palette.hair} strokeWidth={SW} />
      <ellipse cx="40" cy="16" rx="2.2" ry="9" fill="#ffd1de" strokeWidth="0.6" />
      <ellipse cx="88" cy="16" rx="2.2" ry="9" fill="#ffd1de" strokeWidth="0.6" />
    </g>
  );
}

function FoxEars({ palette, outline }: HeadwearProps) {
  return (
    <g stroke={outline} strokeLinejoin="round">
      <path d="M28 28 L38 6 L48 26 Z" fill={palette.hair} strokeWidth={SW} />
      <path d="M80 26 L90 6 L100 28 Z" fill={palette.hair} strokeWidth={SW} />
      <path d="M34 24 L38 12 L44 24 Z" fill="#ffffff" strokeWidth="0.7" />
      <path d="M84 24 L90 12 L96 24 Z" fill="#ffffff" strokeWidth="0.7" />
    </g>
  );
}

function BearEars({ outline }: HeadwearProps) {
  return (
    <g>
      <circle cx="34" cy="20" r="8" fill="#4a2a10" stroke={outline} strokeWidth={SW} />
      <circle cx="94" cy="20" r="8" fill="#4a2a10" stroke={outline} strokeWidth={SW} />
      <circle cx="34" cy="21" r="4" fill="#2a1808" stroke={outline} strokeWidth="0.7" />
      <circle cx="94" cy="21" r="4" fill="#2a1808" stroke={outline} strokeWidth="0.7" />
    </g>
  );
}

function PandaEars({ outline }: HeadwearProps) {
  return (
    <g>
      <circle cx="32" cy="18" r="9" fill="#0f0f10" stroke={outline} strokeWidth={SW} />
      <circle cx="96" cy="18" r="9" fill="#0f0f10" stroke={outline} strokeWidth={SW} />
      <circle cx="32" cy="26" r="3" fill="#ffffff" stroke={outline} strokeWidth="0.7" />
      <circle cx="96" cy="26" r="3" fill="#ffffff" stroke={outline} strokeWidth="0.7" />
    </g>
  );
}

function PenguinFluff({ outline }: HeadwearProps) {
  return (
    <g fill="#ffffff" stroke={outline} strokeWidth={SW} strokeLinejoin="round">
      <path d="M52 22 Q50 14 56 14 Q58 18 56 22 Z" />
      <path d="M62 20 Q60 12 66 12 Q68 16 66 20 Z" />
      <path d="M72 22 Q70 14 76 14 Q78 18 76 22 Z" />
    </g>
  );
}

function HamsterEars({ outline }: HeadwearProps) {
  return (
    <g>
      <circle cx="38" cy="22" r="5" fill="#f0d9a4" stroke={outline} strokeWidth={SW} />
      <circle cx="90" cy="22" r="5" fill="#f0d9a4" stroke={outline} strokeWidth={SW} />
      <circle cx="38" cy="23" r="2.4" fill="#ff9fb5" stroke={outline} strokeWidth="0.6" />
      <circle cx="90" cy="23" r="2.4" fill="#ff9fb5" stroke={outline} strokeWidth="0.6" />
    </g>
  );
}

function DogEarsBack({ outline }: HeadwearProps) {
  return (
    <g fill="#b07a3a" opacity="0.7" stroke={outline} strokeWidth={SW} strokeLinejoin="round">
      <path d="M30 30 Q22 44 28 58 Q34 62 38 58 Q40 44 36 30 Z" />
      <path d="M98 30 Q106 44 100 58 Q94 62 90 58 Q88 44 92 30 Z" />
    </g>
  );
}

function DogEarsFront({ outline }: HeadwearProps) {
  return (
    <g>
      <path d="M32 30 Q24 46 30 58 Q36 60 40 56 Q42 44 38 30 Z" fill="#b07a3a" stroke={outline} strokeWidth={SW} strokeLinejoin="round" />
      <path d="M96 30 Q104 46 98 58 Q92 60 88 56 Q86 44 90 30 Z" fill="#b07a3a" stroke={outline} strokeWidth={SW} strokeLinejoin="round" />
      <path d="M34 36 Q32 48 34 56" fill="none" stroke="#7a4f1c" strokeWidth="1" strokeLinecap="round" />
      <path d="M94 36 Q96 48 94 56" fill="none" stroke="#7a4f1c" strokeWidth="1" strokeLinecap="round" />
    </g>
  );
}

function OwlFeathers({ outline }: HeadwearProps) {
  return (
    <g>
      <path d="M44 22 L48 8 L54 22 Z" fill="#7a4a20" stroke={outline} strokeWidth={SW} strokeLinejoin="round" />
      <path d="M74 22 L80 8 L84 22 Z" fill="#7a4a20" stroke={outline} strokeWidth={SW} strokeLinejoin="round" />
      <path d="M48 14 L50 12" stroke="#ffffff" strokeWidth="1.2" strokeLinecap="round" />
      <path d="M80 14 L82 12" stroke="#ffffff" strokeWidth="1.2" strokeLinecap="round" />
    </g>
  );
}

function DuckBeak({ outline }: HeadwearProps) {
  return (
    <g>
      <path d="M58 78 Q64 74 70 78 Q72 82 64 84 Q56 82 58 78 Z" fill="#f5a01a" stroke={outline} strokeWidth={SW} strokeLinejoin="round" />
      <path d="M58 80 Q64 81 70 80" fill="none" stroke="#b87410" strokeWidth="0.9" strokeLinecap="round" />
    </g>
  );
}

/* ---------------------------- public exports --------------------------- */

export function HeadwearBack(
  props: HeadwearProps,
): React.JSX.Element | null {
  switch (props.kind) {
    case "hood":
      return <HoodBack {...props} />;
    case "catEars":
      return <CatEarsBack {...props} />;
    case "bunnyEars":
      return <BunnyEarsBack {...props} />;
    case "dogEars":
      return <DogEarsBack {...props} />;
    default:
      return null;
  }
}

export function HeadwearFront(
  props: HeadwearProps,
): React.JSX.Element | null {
  switch (props.kind) {
    case "wizardHat":
      return <WizardHat {...props} />;
    case "crown":
      return <Crown {...props} />;
    case "hood":
      return <HoodFront {...props} />;
    case "headband":
      return <Headband {...props} />;
    case "beret":
      return <Beret {...props} />;
    case "catEars":
      return <CatEarsFront {...props} />;
    case "bunnyEars":
      return <BunnyEarsFront {...props} />;
    case "foxEars":
      return <FoxEars {...props} />;
    case "bearEars":
      return <BearEars {...props} />;
    case "pandaEars":
      return <PandaEars {...props} />;
    case "penguinFluff":
      return <PenguinFluff {...props} />;
    case "hamsterEars":
      return <HamsterEars {...props} />;
    case "dogEars":
      return <DogEarsFront {...props} />;
    case "owlFeathers":
      return <OwlFeathers {...props} />;
    case "duckBeak":
      return <DuckBeak {...props} />;
    case "none":
    default:
      return null;
  }
}

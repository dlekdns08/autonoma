"use client";

import type React from "react";
import { useId } from "react";
import type { ChibiSpecies } from "../types";

export interface SpeciesExtrasProps {
  species: ChibiSpecies;
  walkPhase?: number; // 0..1, used for tail sway
  outline: string;
  primaryColor: string; // body/fur colour for this species
  accentColor: string; // tip colour
}

const TAU = Math.PI * 2;

/** Darken a hex colour by blending toward black. */
function darken(hex: string, amount = 0.35): string {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!m) return hex;
  const f = 1 - amount;
  const ch = (i: number) =>
    Math.max(0, Math.min(255, Math.round(parseInt(m[i], 16) * f)))
      .toString(16)
      .padStart(2, "0");
  return `#${ch(1)}${ch(2)}${ch(3)}`;
}

/**
 * Species-specific parts drawn BEHIND the body/outfit: tails and wings.
 * Parent <svg> uses the chibi 128×192 viewBox and flips horizontally for
 * left-facing poses, so we always draw on the right side of the character.
 */
export function SpeciesExtrasBack(
  props: SpeciesExtrasProps,
): React.JSX.Element | null {
  const { species, walkPhase = 0, outline, primaryColor, accentColor } = props;
  const uid = useId();
  const phase = walkPhase;
  const gradId = `${uid}-se-${species}-grad`;
  const gradIdB = `${uid}-se-${species}-grad-b`;
  const dark = darken(primaryColor, 0.4);
  const midDark = darken(primaryColor, 0.2);
  const sw = "1.6";

  if (species === "human") return null;

  // Shared sway for tails (degrees)
  const tailSway = Math.sin(phase * TAU) * 8;

  if (species === "cat") {
    return (
      <g>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={primaryColor} />
            <stop offset="1" stopColor={dark} />
          </linearGradient>
        </defs>
        <g transform={`rotate(${tailSway} 88 148)`}>
          {/* Long S-curve tail */}
          <path
            d="M88 148 C 104 150, 124 140, 120 120 C 118 108, 112 100, 114 102"
            fill="none"
            stroke={`url(#${gradId})`}
            strokeWidth="9"
            strokeLinecap="round"
          />
          <path
            d="M88 148 C 104 150, 124 140, 120 120 C 118 108, 112 100, 114 102"
            fill="none"
            stroke={outline}
            strokeWidth={sw}
            strokeLinecap="round"
            strokeOpacity="0.9"
          />
          {/* Light stripe along the length */}
          <path
            d="M90 147 C 104 148, 120 140, 117 122 C 116 112, 113 104, 114 103"
            fill="none"
            stroke={accentColor}
            strokeWidth="2"
            strokeLinecap="round"
            opacity="0.75"
          />
          {/* Accent tip */}
          <circle cx="114" cy="102" r="3.2" fill={accentColor} stroke={outline} strokeWidth={sw} />
        </g>
      </g>
    );
  }

  if (species === "fox") {
    return (
      <g>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={primaryColor} />
            <stop offset="1" stopColor={dark} />
          </linearGradient>
        </defs>
        <g transform={`rotate(${tailSway} 90 150)`}>
          {/* Big bushy tail body */}
          <path
            d="M90 150 C 108 152, 128 138, 126 120 C 124 108, 118 106, 125 115 C 122 104, 110 100, 100 110 C 94 118, 90 138, 90 150 Z"
            fill={`url(#${gradId})`}
            stroke={outline}
            strokeWidth={sw}
            strokeLinejoin="round"
          />
          {/* Fluff lines */}
          <path
            d="M96 146 C 108 144, 118 136, 122 124"
            fill="none"
            stroke={midDark}
            strokeWidth="1.4"
            strokeLinecap="round"
            opacity="0.7"
          />
          <path
            d="M98 152 C 110 150, 120 142, 124 130"
            fill="none"
            stroke={midDark}
            strokeWidth="1.4"
            strokeLinecap="round"
            opacity="0.55"
          />
          {/* White tip cluster */}
          <path
            d="M118 108 C 124 108, 128 112, 126 118 C 124 122, 118 120, 116 116 C 114 112, 116 108, 118 108 Z"
            fill={accentColor}
            stroke={outline}
            strokeWidth={sw}
            strokeLinejoin="round"
          />
        </g>
      </g>
    );
  }

  if (species === "rabbit") {
    return (
      <g>
        <defs>
          <radialGradient id={gradId} cx="0.4" cy="0.4" r="0.7">
            <stop offset="0" stopColor={accentColor} />
            <stop offset="1" stopColor={darken(accentColor, 0.2)} />
          </radialGradient>
        </defs>
        {/* Fluffy round tail */}
        <circle
          cx="94"
          cy="152"
          r="6"
          fill={`url(#${gradId})`}
          stroke={outline}
          strokeWidth={sw}
        />
        <path
          d="M92 154 C 94 157, 98 156, 99 153"
          fill="none"
          stroke={darken(accentColor, 0.25)}
          strokeWidth="1.2"
          strokeLinecap="round"
          opacity="0.8"
        />
      </g>
    );
  }

  if (species === "dog") {
    return (
      <g>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={primaryColor} />
            <stop offset="1" stopColor={dark} />
          </linearGradient>
        </defs>
        <g transform={`rotate(${tailSway} 90 148)`}>
          {/* Curly upward C-shape tail */}
          <path
            d="M90 148 C 108 150, 122 138, 118 122 C 116 112, 108 112, 112 120 C 114 126, 118 122, 118 120"
            fill="none"
            stroke={`url(#${gradId})`}
            strokeWidth="8"
            strokeLinecap="round"
          />
          <path
            d="M90 148 C 108 150, 122 138, 118 122 C 116 112, 108 112, 112 120 C 114 126, 118 122, 118 120"
            fill="none"
            stroke={outline}
            strokeWidth={sw}
            strokeLinecap="round"
            strokeOpacity="0.9"
          />
          {/* Accent tip */}
          <circle cx="118" cy="120" r="3" fill={accentColor} stroke={outline} strokeWidth={sw} />
        </g>
      </g>
    );
  }

  if (species === "hamster") {
    return <circle cx="94" cy="154" r="3" fill={accentColor} stroke={outline} strokeWidth={sw} />;
  }

  if (species === "panda") {
    return <circle cx="94" cy="154" r="4" fill="#1b1420" stroke={outline} strokeWidth={sw} />;
  }

  if (species === "bear") {
    return (
      <g>
        <defs>
          <radialGradient id={gradId} cx="0.4" cy="0.4" r="0.7">
            <stop offset="0" stopColor={primaryColor} />
            <stop offset="1" stopColor={dark} />
          </radialGradient>
        </defs>
        <circle cx="94" cy="155" r="4" fill={`url(#${gradId})`} stroke={outline} strokeWidth={sw} />
      </g>
    );
  }

  if (species === "owl") {
    const flap = Math.sin(phase * TAU) * 4;
    return (
      <g>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={primaryColor} />
            <stop offset="1" stopColor={dark} />
          </linearGradient>
          <linearGradient id={gradIdB} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={primaryColor} />
            <stop offset="1" stopColor={dark} />
          </linearGradient>
        </defs>
        {/* Left wing */}
        <g transform={`rotate(${flap} 40 116)`}>
          <path
            d="M40 116 C 22 122, 14 140, 22 160 C 28 170, 38 166, 42 152 C 44 144, 44 132, 40 116 Z"
            fill={`url(#${gradId})`}
            stroke={outline}
            strokeWidth={sw}
            strokeLinejoin="round"
          />
          {/* Feather lines */}
          <path
            d="M36 124 C 26 132, 22 146, 26 158"
            fill="none"
            stroke={accentColor}
            strokeWidth="1.3"
            strokeLinecap="round"
            opacity="0.85"
          />
          <path
            d="M40 132 C 30 140, 28 150, 32 160"
            fill="none"
            stroke={accentColor}
            strokeWidth="1.3"
            strokeLinecap="round"
            opacity="0.7"
          />
        </g>
        {/* Right wing */}
        <g transform={`rotate(${-flap} 88 116)`}>
          <path
            d="M88 116 C 106 122, 114 140, 106 160 C 100 170, 90 166, 86 152 C 84 144, 84 132, 88 116 Z"
            fill={`url(#${gradIdB})`}
            stroke={outline}
            strokeWidth={sw}
            strokeLinejoin="round"
          />
          <path
            d="M92 124 C 102 132, 106 146, 102 158"
            fill="none"
            stroke={accentColor}
            strokeWidth="1.3"
            strokeLinecap="round"
            opacity="0.85"
          />
          <path
            d="M88 132 C 98 140, 100 150, 96 160"
            fill="none"
            stroke={accentColor}
            strokeWidth="1.3"
            strokeLinecap="round"
            opacity="0.7"
          />
        </g>
      </g>
    );
  }

  if (species === "duck") {
    return (
      <g>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={primaryColor} />
            <stop offset="1" stopColor={dark} />
          </linearGradient>
        </defs>
        {/* Left winglet stub */}
        <path
          d="M38 120 C 30 126, 30 140, 38 144 C 42 142, 44 132, 42 122 Z"
          fill={`url(#${gradId})`}
          stroke={outline}
          strokeWidth={sw}
          strokeLinejoin="round"
        />
        {/* Right winglet stub */}
        <path
          d="M90 120 C 98 126, 98 140, 90 144 C 86 142, 84 132, 86 122 Z"
          fill={`url(#${gradId})`}
          stroke={outline}
          strokeWidth={sw}
          strokeLinejoin="round"
        />
        {/* Accent tips */}
        <path
          d="M34 138 C 36 142, 40 142, 40 138"
          fill="none"
          stroke={accentColor}
          strokeWidth="1.3"
          strokeLinecap="round"
          opacity="0.85"
        />
        <path
          d="M88 138 C 90 142, 94 142, 94 138"
          fill="none"
          stroke={accentColor}
          strokeWidth="1.3"
          strokeLinecap="round"
          opacity="0.85"
        />
      </g>
    );
  }

  if (species === "penguin") {
    const flap = Math.sin(phase * TAU) * 2;
    return (
      <g>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={darken(primaryColor, 0.1)} />
            <stop offset="1" stopColor={darken(primaryColor, 0.55)} />
          </linearGradient>
        </defs>
        {/* Left flipper */}
        <g transform={`rotate(${flap} 40 118)`}>
          <path
            d="M40 118 C 30 124, 26 146, 32 156 C 36 158, 40 152, 42 140 C 43 132, 42 124, 40 118 Z"
            fill={`url(#${gradId})`}
            stroke={outline}
            strokeWidth={sw}
            strokeLinejoin="round"
          />
        </g>
        {/* Right flipper */}
        <g transform={`rotate(${-flap} 88 118)`}>
          <path
            d="M88 118 C 98 124, 102 146, 96 156 C 92 158, 88 152, 86 140 C 85 132, 86 124, 88 118 Z"
            fill={`url(#${gradId})`}
            stroke={outline}
            strokeWidth={sw}
            strokeLinejoin="round"
          />
        </g>
      </g>
    );
  }

  return null;
}

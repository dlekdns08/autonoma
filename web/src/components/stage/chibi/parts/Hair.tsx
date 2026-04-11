"use client";

import React, { useId } from "react";
import type { HairStyle } from "../types";

export interface HairProps {
  style: HairStyle;
  color: string;
  light: string;
  outline: string;
}

/**
 * Shared linear gradient definition. Highlight tint sits near the top
 * of the head (y≈18-32) and fades to the base colour at the lower end
 * of each hair piece, giving a soft anime sheen.
 */
function HairGradient({
  id,
  color,
  light,
  y1 = 12,
  y2 = 110,
}: {
  id: string;
  color: string;
  light: string;
  y1?: number;
  y2?: number;
}) {
  return (
    <linearGradient id={id} x1="64" y1={y1} x2="64" y2={y2} gradientUnits="userSpaceOnUse">
      <stop offset="0" stopColor={light} />
      <stop offset="0.35" stopColor={light} stopOpacity="0.85" />
      <stop offset="0.55" stopColor={color} />
      <stop offset="1" stopColor={color} />
    </linearGradient>
  );
}

/* ============================================================
 * BACK HAIR — drawn before the head
 * ============================================================ */

export function HairBack({ style, color, light, outline }: HairProps): React.JSX.Element | null {
  const uid = useId();
  const gradId = `${uid}-hair-${style}-back-grad`;
  const stroke = { stroke: outline, strokeWidth: 1.8, strokeLinejoin: "round" as const, strokeLinecap: "round" as const };

  switch (style) {
    case "longStraight": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={18} y2={170} />
          </defs>
          {/* Big sheet of straight hair behind body */}
          <path d="M 22 60 Q 18 100 24 140 Q 30 168 50 168 Q 64 172 78 168 Q 100 168 104 140 Q 110 100 106 60 Q 100 38 64 36 Q 28 38 22 60 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Inner shadow strands */}
          <path d="M 36 70 Q 32 110 40 150" fill="none" stroke={outline} strokeOpacity="0.25" strokeWidth="1.2" strokeLinecap="round" />
          <path d="M 92 70 Q 96 110 88 150" fill="none" stroke={outline} strokeOpacity="0.25" strokeWidth="1.2" strokeLinecap="round" />
          {/* Highlight shine */}
          <path d="M 48 50 Q 46 90 52 130" fill="none" stroke={light} strokeOpacity="0.7" strokeWidth="2" strokeLinecap="round" />
        </g>
      );
    }

    case "twinTails": {
      return (
        <g>
          <defs>
            <HairGradient id={`${gradId}-l`} color={color} light={light} y1={28} y2={160} />
            <HairGradient id={`${gradId}-r`} color={color} light={light} y1={28} y2={160} />
          </defs>
          {/* Left twin tail */}
          <path d="M 22 48 Q 10 80 14 120 Q 16 150 26 158 Q 34 160 38 150 Q 42 110 44 80 Q 42 56 30 46 Z" fill={`url(#${gradId}-l)`} {...stroke} />
          {/* Right twin tail */}
          <path d="M 106 48 Q 118 80 114 120 Q 112 150 102 158 Q 94 160 90 150 Q 86 110 84 80 Q 86 56 98 46 Z" fill={`url(#${gradId}-r)`} {...stroke} />
          {/* Highlights */}
          <path d="M 22 70 Q 20 110 26 140" fill="none" stroke={light} strokeOpacity="0.75" strokeWidth="1.8" strokeLinecap="round" />
          <path d="M 106 70 Q 108 110 102 140" fill="none" stroke={light} strokeOpacity="0.75" strokeWidth="1.8" strokeLinecap="round" />
        </g>
      );
    }

    case "sidePonytail": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={50} y2={150} />
          </defs>
          {/* Tail flowing down right side from (95,70) to ~(110,150) */}
          <path d="M 88 58 Q 104 64 110 86 Q 116 110 112 132 Q 108 150 96 152 Q 88 150 86 136 Q 84 110 82 86 Q 82 66 88 58 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Strand divisions */}
          <path d="M 96 76 Q 100 110 100 140" fill="none" stroke={outline} strokeOpacity="0.28" strokeWidth="1.2" strokeLinecap="round" />
          {/* Highlight */}
          <path d="M 90 72 Q 92 100 92 130" fill="none" stroke={light} strokeOpacity="0.75" strokeWidth="1.8" strokeLinecap="round" />
        </g>
      );
    }

    case "braid": {
      // Diamond segments down the back
      const segments = [];
      const cx = 64;
      const startY = 60;
      const segH = 12;
      const segW = 11;
      const count = 9;
      for (let i = 0; i < count; i++) {
        const y = startY + i * segH;
        const sw = segW - i * 0.35;
        const d = `M ${cx - sw} ${y} Q ${cx} ${y - 2} ${cx + sw} ${y} Q ${cx} ${y + segH + 2} ${cx - sw} ${y + segH} Q ${cx} ${y + segH - 2} ${cx + sw} ${y + segH}`;
        segments.push(<path key={`braid-${i}`} d={d} fill={`url(#${gradId})`} {...stroke} />);
      }
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={50} y2={170} />
          </defs>
          {/* Tie at top */}
          <path d="M 54 56 Q 64 50 74 56 Q 76 64 64 66 Q 52 64 54 56 Z" fill={color} {...stroke} />
          {segments}
          {/* Tassel at bottom */}
          <path d={`M ${cx - 6} ${startY + count * segH} Q ${cx - 4} ${startY + count * segH + 8} ${cx - 7} ${startY + count * segH + 12}`} fill="none" stroke={outline} strokeWidth="1.6" strokeLinecap="round" />
          <path d={`M ${cx + 6} ${startY + count * segH} Q ${cx + 4} ${startY + count * segH + 8} ${cx + 7} ${startY + count * segH + 12}`} fill="none" stroke={outline} strokeWidth="1.6" strokeLinecap="round" />
          <path d={`M ${cx} ${startY + count * segH} Q ${cx + 1} ${startY + count * segH + 9} ${cx - 1} ${startY + count * segH + 14}`} fill="none" stroke={outline} strokeWidth="1.6" strokeLinecap="round" />
        </g>
      );
    }

    case "wavy": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={20} y2={130} />
          </defs>
          {/* Soft wavy back sheet */}
          <path d="M 24 58 Q 18 90 26 118 Q 34 128 44 122 Q 54 130 64 124 Q 74 130 84 122 Q 94 128 102 118 Q 110 90 104 58 Q 92 34 64 32 Q 36 34 24 58 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Wavy strand details */}
          <path d="M 32 80 Q 36 100 30 116" fill="none" stroke={outline} strokeOpacity="0.3" strokeWidth="1.2" strokeLinecap="round" />
          <path d="M 96 80 Q 92 100 98 116" fill="none" stroke={outline} strokeOpacity="0.3" strokeWidth="1.2" strokeLinecap="round" />
          <path d="M 40 60 Q 44 90 38 110" fill="none" stroke={light} strokeOpacity="0.7" strokeWidth="1.8" strokeLinecap="round" />
        </g>
      );
    }

    default:
      return null;
  }
}

/* ============================================================
 * FRONT HAIR — drawn after the head
 * ============================================================ */

export function HairFront({ style, color, light, outline }: HairProps): React.JSX.Element {
  const uid = useId();
  const gradId = `${uid}-hair-${style}-front-grad`;
  const stroke = { stroke: outline, strokeWidth: 1.8, strokeLinejoin: "round" as const, strokeLinecap: "round" as const };

  switch (style) {
    case "shortBob": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={14} y2={110} />
          </defs>
          {/* Bob silhouette: top dome + chin-length sides */}
          <path d="M 22 60 Q 18 30 46 16 Q 64 10 82 16 Q 110 30 106 60 Q 108 88 102 104 Q 96 110 88 104 Q 86 80 88 60 Q 64 66 40 60 Q 42 80 40 104 Q 32 110 26 104 Q 20 88 22 60 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Bangs with pointed tips */}
          <path d="M 38 36 Q 44 52 50 38 Q 56 56 64 40 Q 72 56 78 38 Q 84 52 90 36 Q 92 50 86 56 Q 64 60 42 56 Q 36 50 38 36 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Highlight shine on top */}
          <path d="M 44 24 Q 56 18 70 22" fill="none" stroke={light} strokeOpacity="0.85" strokeWidth="2.2" strokeLinecap="round" />
          <path d="M 88 30 Q 96 38 98 50" fill="none" stroke={light} strokeOpacity="0.6" strokeWidth="1.6" strokeLinecap="round" />
        </g>
      );
    }

    case "longStraight": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={14} y2={120} />
          </defs>
          {/* Top dome + side locks down to shoulders */}
          <path d="M 22 62 Q 18 28 48 14 Q 64 10 80 14 Q 110 28 106 62 Q 110 100 104 130 Q 96 138 88 130 Q 86 96 90 64 Q 64 70 38 64 Q 42 96 40 130 Q 32 138 24 130 Q 18 100 22 62 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Straight bangs with 3 tips */}
          <path d="M 36 36 Q 42 54 48 38 Q 54 56 62 40 Q 70 56 76 40 Q 82 56 88 38 Q 94 54 96 36 Q 96 50 88 56 Q 64 62 40 56 Q 32 50 36 36 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Highlights */}
          <path d="M 46 22 Q 60 16 74 22" fill="none" stroke={light} strokeOpacity="0.85" strokeWidth="2.4" strokeLinecap="round" />
          <path d="M 32 70 Q 30 100 32 124" fill="none" stroke={light} strokeOpacity="0.6" strokeWidth="1.6" strokeLinecap="round" />
        </g>
      );
    }

    case "twinTails": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={14} y2={80} />
          </defs>
          {/* Top dome */}
          <path d="M 24 60 Q 20 26 50 14 Q 64 10 78 14 Q 108 26 104 60 Q 100 70 90 66 Q 64 70 38 66 Q 28 70 24 60 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Fluffy tuft where left tail attaches */}
          <path d="M 18 54 Q 14 38 26 32 Q 34 38 30 52 Q 24 60 18 54 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Fluffy tuft where right tail attaches */}
          <path d="M 110 54 Q 114 38 102 32 Q 94 38 98 52 Q 104 60 110 54 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Bangs split in the middle */}
          <path d="M 36 36 Q 42 56 50 40 Q 58 56 62 42 L 64 56 L 66 42 Q 70 56 78 40 Q 86 56 92 36 Q 94 52 86 58 Q 64 64 42 58 Q 34 52 36 36 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Highlight */}
          <path d="M 44 22 Q 58 16 72 22" fill="none" stroke={light} strokeOpacity="0.85" strokeWidth="2.2" strokeLinecap="round" />
        </g>
      );
    }

    case "sidePonytail": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={14} y2={80} />
          </defs>
          {/* Top swept hair */}
          <path d="M 22 60 Q 18 28 48 14 Q 64 10 82 16 Q 108 30 102 60 Q 96 70 88 66 Q 64 70 40 66 Q 28 70 22 60 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Swept-side bangs (sweeping right) */}
          <path d="M 32 38 Q 40 56 50 42 Q 60 56 70 40 Q 82 54 94 36 Q 98 52 90 60 Q 64 66 40 58 Q 30 52 32 38 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Hair tie / bobble at base of ponytail */}
          <circle cx="92" cy="62" r="4" fill={light} stroke={outline} strokeWidth="1.4" />
          {/* Highlight */}
          <path d="M 42 22 Q 58 16 76 22" fill="none" stroke={light} strokeOpacity="0.85" strokeWidth="2.2" strokeLinecap="round" />
        </g>
      );
    }

    case "braid": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={14} y2={90} />
          </defs>
          {/* Top dome with center part */}
          <path d="M 22 60 Q 18 28 50 14 Q 64 10 78 14 Q 110 28 106 60 Q 102 70 92 66 Q 66 70 64 56 Q 62 70 36 66 Q 26 70 22 60 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Parted bangs */}
          <path d="M 36 36 Q 44 56 52 40 Q 58 50 62 38 L 64 50 L 66 38 Q 70 50 76 40 Q 84 56 92 36 Q 94 52 86 58 Q 64 62 42 58 Q 34 52 36 36 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Center part line */}
          <path d="M 64 38 L 64 56" stroke={outline} strokeOpacity="0.35" strokeWidth="1" strokeLinecap="round" />
          {/* Highlight */}
          <path d="M 44 22 Q 60 16 76 22" fill="none" stroke={light} strokeOpacity="0.85" strokeWidth="2.2" strokeLinecap="round" />
        </g>
      );
    }

    case "spiky": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={6} y2={80} />
          </defs>
          {/* Zigzag spiky silhouette */}
          <path d="M 22 62 L 26 40 L 32 52 L 38 22 L 46 44 L 52 18 L 60 40 L 66 14 L 74 38 L 80 20 L 88 44 L 94 22 L 100 48 L 106 38 L 108 62 Q 104 68 92 66 Q 64 70 36 66 Q 24 68 22 62 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Diagonal bangs */}
          <path d="M 30 44 Q 40 58 48 42 L 56 58 L 62 40 L 70 56 L 78 42 Q 86 58 96 44 Q 96 56 88 60 Q 64 64 40 60 Q 30 56 30 44 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Spike highlights */}
          <path d="M 38 30 L 42 42" stroke={light} strokeOpacity="0.85" strokeWidth="1.6" strokeLinecap="round" />
          <path d="M 66 22 L 70 36" stroke={light} strokeOpacity="0.85" strokeWidth="1.6" strokeLinecap="round" />
          <path d="M 88 30 L 92 42" stroke={light} strokeOpacity="0.85" strokeWidth="1.6" strokeLinecap="round" />
        </g>
      );
    }

    case "wavy": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={14} y2={110} />
          </defs>
          {/* Wavy top with bumpy outline */}
          <path d="M 22 60 Q 18 32 30 22 Q 38 12 50 16 Q 58 8 66 14 Q 74 8 82 16 Q 94 12 100 22 Q 110 32 106 60 Q 110 86 102 100 Q 94 108 86 100 Q 86 80 90 64 Q 64 70 38 64 Q 42 80 42 100 Q 34 108 26 100 Q 18 86 22 60 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Curled bangs with curls */}
          <path d="M 36 38 Q 38 52 46 50 Q 50 42 54 50 Q 60 56 64 44 Q 68 56 74 50 Q 78 42 82 50 Q 90 52 92 38 Q 92 56 84 60 Q 64 64 44 60 Q 36 56 36 38 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Side curl swoops */}
          <path d="M 24 70 Q 16 80 22 92 Q 30 96 32 86" fill={color} {...stroke} />
          <path d="M 104 70 Q 112 80 106 92 Q 98 96 96 86" fill={color} {...stroke} />
          {/* Highlights */}
          <path d="M 44 22 Q 58 16 72 22" fill="none" stroke={light} strokeOpacity="0.85" strokeWidth="2.2" strokeLinecap="round" />
          <path d="M 86 30 Q 92 40 92 50" fill="none" stroke={light} strokeOpacity="0.6" strokeWidth="1.6" strokeLinecap="round" />
        </g>
      );
    }

    case "bun": {
      return (
        <g>
          <defs>
            <HairGradient id={gradId} color={color} light={light} y1={10} y2={80} />
          </defs>
          {/* Smooth top */}
          <path d="M 22 62 Q 18 30 48 18 Q 64 14 80 18 Q 110 30 106 62 Q 100 70 90 66 Q 64 70 38 66 Q 28 70 22 62 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* The bun on top */}
          <ellipse cx="64" cy="16" rx="14" ry="11" fill={`url(#${gradId})`} {...stroke} />
          {/* Bun wrap detail */}
          <path d="M 52 14 Q 64 8 76 14 Q 64 22 52 14 Z" fill="none" stroke={outline} strokeOpacity="0.4" strokeWidth="1.2" strokeLinecap="round" />
          {/* Smooth bangs */}
          <path d="M 36 36 Q 46 56 54 42 Q 60 54 64 44 Q 68 54 74 42 Q 82 56 92 36 Q 94 52 86 58 Q 64 62 42 58 Q 34 52 36 36 Z" fill={`url(#${gradId})`} {...stroke} />
          {/* Bun highlight */}
          <path d="M 56 12 Q 60 8 66 10" fill="none" stroke={light} strokeOpacity="0.95" strokeWidth="1.8" strokeLinecap="round" />
          {/* Top sheen */}
          <path d="M 44 26 Q 58 20 72 24" fill="none" stroke={light} strokeOpacity="0.8" strokeWidth="2" strokeLinecap="round" />
        </g>
      );
    }

    case "hoodHidden": {
      // Fully hidden — return an empty group so signature stays non-null.
      return <g />;
    }

    default:
      return <g />;
  }
}

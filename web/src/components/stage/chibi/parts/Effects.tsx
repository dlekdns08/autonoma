"use client";

import type * as React from "react";
import type { ChibiRarity, ChibiState, PaletteSlot } from "../types";

export interface EffectsProps {
  rarity: ChibiRarity;
  state: ChibiState;
  palette: PaletteSlot;
  outline: string;
}

/* ──────────────────────────────────────────────────────────────────────────
 * EffectsBack — ground shadow + rarity aura, drawn behind the character.
 * ────────────────────────────────────────────────────────────────────────── */
export function EffectsBack(props: EffectsProps): React.JSX.Element {
  const { rarity, palette } = props;
  const auraId = `fx-aura-${rarity}`;

  return (
    <g data-effects="back">
      {/* Aura gradient defs */}
      <defs>
        {rarity === "uncommon" ? (
          <radialGradient id={auraId} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#a7f3d0" stopOpacity="0.9" />
            <stop offset="60%" stopColor="#a7f3d0" stopOpacity="0.25" />
            <stop offset="100%" stopColor="#a7f3d0" stopOpacity="0" />
          </radialGradient>
        ) : null}
        {rarity === "rare" ? (
          <radialGradient id={auraId} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.85" />
            <stop offset="45%" stopColor="#8b5cf6" stopOpacity="0.45" />
            <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0" />
          </radialGradient>
        ) : null}
        {rarity === "legendary" ? (
          <radialGradient id={auraId} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#fde68a" stopOpacity="0.95" />
            <stop offset="40%" stopColor="#fbbf24" stopOpacity="0.65" />
            <stop offset="80%" stopColor="#f59e0b" stopOpacity="0.25" />
            <stop offset="100%" stopColor="#f59e0b" stopOpacity="0" />
          </radialGradient>
        ) : null}
      </defs>

      {/* Ground shadow (base layer) */}
      {rarity === "legendary" ? (
        <ellipse
          cx={64}
          cy={188}
          rx={28}
          ry={4}
          fill={palette.aura}
          opacity={0.4}
        />
      ) : null}
      <ellipse cx={64} cy={188} rx={22} ry={3} fill="#000" opacity={0.25} />

      {/* Aura ring per-rarity */}
      {rarity === "uncommon" ? (
        <ellipse
          cx={64}
          cy={100}
          rx={55}
          ry={55}
          fill={`url(#${auraId})`}
          opacity={0.35}
        />
      ) : null}

      {rarity === "rare" ? (
        <g opacity={0.9}>
          <ellipse
            cx={64}
            cy={96}
            rx={60}
            ry={60}
            fill={`url(#${auraId})`}
            opacity={0.5}
          />
          <circle
            cx={64}
            cy={96}
            r={58}
            fill="none"
            stroke="#22d3ee"
            strokeOpacity={0.5}
            strokeWidth={1.2}
            strokeDasharray="3 4"
          />
        </g>
      ) : null}

      {rarity === "legendary" ? (
        <g>
          <ellipse
            cx={64}
            cy={92}
            rx={70}
            ry={70}
            fill={`url(#${auraId})`}
            opacity={0.75}
          />
          {/* Sunburst rays — 6 long triangles around the head */}
          <g
            fill="#fde68a"
            opacity={0.55}
            stroke="#fbbf24"
            strokeOpacity={0.7}
            strokeWidth={0.6}
            strokeLinejoin="round"
          >
            {Array.from({ length: 6 }).map((_, i) => {
              const angle = (i * 60 - 90) * (Math.PI / 180);
              const cx = 64;
              const cy = 60;
              const innerR = 44;
              const outerR = 86;
              const spread = 6;
              // Perpendicular offset for base width
              const px = Math.cos(angle);
              const py = Math.sin(angle);
              const nx = -py;
              const ny = px;
              const baseAx = cx + px * innerR + nx * spread;
              const baseAy = cy + py * innerR + ny * spread;
              const baseBx = cx + px * innerR - nx * spread;
              const baseBy = cy + py * innerR - ny * spread;
              const tipX = cx + px * outerR;
              const tipY = cy + py * outerR;
              return (
                <path
                  key={i}
                  d={`M ${baseAx.toFixed(2)} ${baseAy.toFixed(2)} L ${tipX.toFixed(2)} ${tipY.toFixed(2)} L ${baseBx.toFixed(2)} ${baseBy.toFixed(2)} Z`}
                />
              );
            })}
          </g>
          <circle
            cx={64}
            cy={92}
            r={66}
            fill="none"
            stroke="#fbbf24"
            strokeOpacity={0.75}
            strokeWidth={1.4}
            strokeDasharray="4 5"
          />
        </g>
      ) : null}
    </g>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
 * EffectsFront — sparkles, emotes, particle overlays, drawn in front.
 * ────────────────────────────────────────────────────────────────────────── */
export function EffectsFront(props: EffectsProps): React.JSX.Element {
  const { rarity, state, palette } = props;

  return (
    <g data-effects="front">
      {/* Rarity sparkles */}
      {rarity === "rare" || rarity === "legendary"
        ? renderSparkles(palette.aura)
        : null}

      {/* State-driven emote */}
      {state === "thinking" ? renderThinking() : null}
      {state === "working" ? renderWorking() : null}
      {state === "celebrating" ? renderCelebrating(palette.aura) : null}
      {state === "talking" ? renderTalking() : null}

      {/* Working gleam on hand */}
      {state === "working" ? renderGleam(100, 148, 3) : null}
    </g>
  );
}

/* ─── helpers ─────────────────────────────────────────────────────────── */

/** A small 4-point star/diamond drawn as a closed path using quadratic
 *  curves. Center (cx, cy), radius r. */
function sparklePath(cx: number, cy: number, r: number): string {
  const k = r * 0.35; // curve control distance
  return [
    `M ${cx} ${cy - r}`,
    `Q ${cx + k} ${cy - k} ${cx + r} ${cy}`,
    `Q ${cx + k} ${cy + k} ${cx} ${cy + r}`,
    `Q ${cx - k} ${cy + k} ${cx - r} ${cy}`,
    `Q ${cx - k} ${cy - k} ${cx} ${cy - r}`,
    "Z",
  ].join(" ");
}

interface SparklePos {
  cx: number;
  cy: number;
  r: number;
}

const SPARKLE_POSITIONS: readonly SparklePos[] = [
  { cx: 20, cy: 40, r: 3.2 },
  { cx: 110, cy: 30, r: 3.8 },
  { cx: 15, cy: 120, r: 2.6 },
  { cx: 115, cy: 140, r: 3 },
  { cx: 90, cy: 15, r: 2.4 },
  { cx: 32, cy: 10, r: 3.4 },
];

function renderSparkles(aura: string): React.JSX.Element {
  return (
    <g data-fx="sparkles">
      {SPARKLE_POSITIONS.map((s, i) => (
        <g key={i}>
          <path d={sparklePath(s.cx, s.cy, s.r)} fill={aura} opacity={0.95} />
          <path
            d={sparklePath(s.cx, s.cy, s.r * 0.45)}
            fill="#ffffff"
            opacity={0.95}
          />
        </g>
      ))}
    </g>
  );
}

/* Thinking: cloud bubble + ? glyph */
function renderThinking(): React.JSX.Element {
  return (
    <g data-fx="thinking">
      {/* trailing small bubble toward head */}
      <circle
        cx={88}
        cy={32}
        r={2}
        fill="#ffffff"
        stroke="#2a1b3d"
        strokeWidth={0.8}
      />
      {/* medium bubble */}
      <circle
        cx={93}
        cy={27}
        r={3.2}
        fill="#ffffff"
        stroke="#2a1b3d"
        strokeWidth={0.9}
      />
      {/* main cloud — offset circles to suggest a cloud shape */}
      <g
        fill="#ffffff"
        stroke="#2a1b3d"
        strokeWidth={1}
        strokeLinejoin="round"
      >
        <circle cx={100} cy={20} r={8} />
        <circle cx={108} cy={22} r={5.5} />
        <circle cx={93} cy={22} r={5} />
        <circle cx={102} cy={14} r={5} />
      </g>
      {/* ? glyph, drawn as path so no text glyphs */}
      <path
        d="M 97.5 17 Q 97.5 14 100 14 Q 103 14 103 17 Q 103 19 101 20 Q 100 20.5 100 22"
        fill="none"
        stroke="#2a1b3d"
        strokeWidth={1.2}
        strokeLinecap="round"
      />
      <circle cx={100} cy={24.4} r={0.9} fill="#2a1b3d" />
    </g>
  );
}

/* Working: 8-tooth gear with hole + motion lines */
function renderWorking(): React.JSX.Element {
  const cx = 100;
  const cy = 18;
  const rOuter = 8;
  const rTooth = 10;
  const rHole = 3;
  const teeth = 8;
  // Build an 8-tooth gear as a path of alternating arcs / teeth. For
  // simplicity & readability we draw the base disc then 8 tooth rects
  // rotated around the center.
  return (
    <g data-fx="working">
      <g
        fill="#9ca3af"
        stroke="#2a1b3d"
        strokeWidth={0.9}
        strokeLinejoin="round"
      >
        {Array.from({ length: teeth }).map((_, i) => {
          const angle = (i * 360) / teeth;
          return (
            <rect
              key={i}
              x={cx - 1.6}
              y={cy - rTooth}
              width={3.2}
              height={3.2}
              rx={0.4}
              transform={`rotate(${angle} ${cx} ${cy})`}
            />
          );
        })}
        <circle cx={cx} cy={cy} r={rOuter} />
      </g>
      {/* inner hole */}
      <circle
        cx={cx}
        cy={cy}
        r={rHole}
        fill="#ffffff"
        stroke="#2a1b3d"
        strokeWidth={0.9}
      />
      {/* motion lines */}
      <g
        fill="none"
        stroke="#6b7280"
        strokeWidth={1.2}
        strokeLinecap="round"
        opacity={0.85}
      >
        <path d="M 84 12 L 88 14" />
        <path d="M 84 20 L 88 20" />
        <path d="M 86 26 L 89 24" />
      </g>
    </g>
  );
}

/* Celebrating: 3 confetti/star shapes above head with static rotations */
function renderCelebrating(aura: string): React.JSX.Element {
  const gold = "#fbbf24";
  return (
    <g data-fx="celebrating">
      {/* left star */}
      <g transform="rotate(-18 24 12)">
        <path
          d={sparklePath(24, 12, 4.2)}
          fill={aura}
          stroke="#2a1b3d"
          strokeWidth={0.8}
        />
        <path d={sparklePath(24, 12, 1.8)} fill="#ffffff" />
      </g>
      {/* center big star */}
      <g transform="rotate(8 64 4)">
        <path
          d={sparklePath(64, 6, 5.2)}
          fill={gold}
          stroke="#2a1b3d"
          strokeWidth={0.9}
        />
        <path d={sparklePath(64, 6, 2.3)} fill="#fff7cc" />
      </g>
      {/* right confetti */}
      <g transform="rotate(22 104 12)">
        <path
          d={sparklePath(104, 12, 4)}
          fill={aura}
          stroke="#2a1b3d"
          strokeWidth={0.8}
        />
        <path d={sparklePath(104, 12, 1.7)} fill="#ffffff" />
      </g>
      {/* little confetti bits */}
      <rect
        x={40}
        y={8}
        width={2.4}
        height={2.4}
        fill={gold}
        transform="rotate(25 41 9)"
      />
      <rect
        x={84}
        y={6}
        width={2.4}
        height={2.4}
        fill={aura}
        transform="rotate(-15 85 7)"
      />
    </g>
  );
}

/* Talking: 3 concentric sound-wave arcs to the right of head */
function renderTalking(): React.JSX.Element {
  const cx = 108;
  const cy = 60;
  return (
    <g
      data-fx="talking"
      fill="none"
      stroke="#2a1b3d"
      strokeWidth={1.3}
      strokeLinecap="round"
    >
      {/* arcs open to the right of the head, i.e. sweep on the right side */}
      <path d={`M ${cx} ${cy - 4} Q ${cx + 3} ${cy} ${cx} ${cy + 4}`} />
      <path d={`M ${cx + 3} ${cy - 7} Q ${cx + 8} ${cy} ${cx + 3} ${cy + 7}`} />
      <path
        d={`M ${cx + 6} ${cy - 10} Q ${cx + 13} ${cy} ${cx + 6} ${cy + 10}`}
        opacity={0.7}
      />
    </g>
  );
}

/* Gleam: small 4-point cross star (hand glint, working state) */
function renderGleam(cx: number, cy: number, r: number): React.JSX.Element {
  return (
    <g data-fx="gleam">
      <path
        d={`M ${cx} ${cy - r} L ${cx + r * 0.35} ${cy - r * 0.35} L ${cx + r} ${cy} L ${cx + r * 0.35} ${cy + r * 0.35} L ${cx} ${cy + r} L ${cx - r * 0.35} ${cy + r * 0.35} L ${cx - r} ${cy} L ${cx - r * 0.35} ${cy - r * 0.35} Z`}
        fill="#ffffff"
        opacity={0.95}
      />
      <circle cx={cx} cy={cy} r={0.8} fill="#ffffff" />
    </g>
  );
}

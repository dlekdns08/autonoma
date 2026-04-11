"use client";

import type * as React from "react";
import type { ChibiMood, ChibiState } from "../types";

export interface FaceProps {
  mood: ChibiMood;
  state: ChibiState;
  outline: string;
  skinBase: string;
  skinShade: string;
  skinHighlight: string;
  blush: string;
  lip: string;
  /** for eyebrow colour (darker shade derived from hair) */
  hairColor: string;
}

/* ------------------------------------------------------------------ */
/* FaceBase — head silhouette + shading + neck stub                    */
/* ------------------------------------------------------------------ */

export function FaceBase(props: FaceProps): React.JSX.Element {
  const { outline, skinBase, skinShade, skinHighlight } = props;

  // Unique-ish gradient id (kept stable; multiple chibis on screen will
  // each get the same id, but svg defs scoping inside <g> still works
  // because we resolve fill via url(#face-skin) per render). For full
  // multi-instance safety the parent can wrap each chibi in its own <svg>.
  const gradId = "face-skin-grad";

  return (
    <g>
      <defs>
        <radialGradient
          id={gradId}
          cx="0.3"
          cy="0.3"
          r="0.85"
          fx="0.3"
          fy="0.3"
        >
          <stop offset="0%" stopColor={skinHighlight} />
          <stop offset="55%" stopColor={skinBase} />
          <stop offset="100%" stopColor={skinShade} />
        </radialGradient>
      </defs>

      {/* Neck stub — drawn first so head sits over it */}
      <rect
        x={58}
        y={104}
        width={12}
        height={8}
        fill={skinShade}
        stroke={outline}
        strokeWidth={1.2}
        strokeLinejoin="round"
      />

      {/* Head silhouette — wider at jaw, soft chibi chin */}
      <path
        d="M 20 50 Q 18 15 64 14 Q 110 15 108 50 Q 108 95 64 104 Q 20 95 20 50 Z"
        fill={`url(#${gradId})`}
        stroke={outline}
        strokeWidth={1.8}
        strokeLinejoin="round"
      />

      {/* Subtle chin shadow */}
      <ellipse
        cx={64}
        cy={100}
        rx={14}
        ry={3}
        fill={skinShade}
        opacity={0.5}
      />
    </g>
  );
}

/* ------------------------------------------------------------------ */
/* FaceFeatures — eyebrows, mouth, blush                               */
/* ------------------------------------------------------------------ */

export function FaceFeatures(props: FaceProps): React.JSX.Element {
  const { mood, state, outline, blush, lip } = props;

  return (
    <g>
      <Eyebrows mood={mood} outline={outline} />
      <Mouth mood={mood} state={state} outline={outline} lip={lip} />
      <Blush mood={mood} blush={blush} />
    </g>
  );
}

/* ------------------------------------------------------------------ */
/* Eyebrows                                                            */
/* ------------------------------------------------------------------ */

interface EyebrowsProps {
  mood: ChibiMood;
  outline: string;
}

function Eyebrows({ mood, outline }: EyebrowsProps): React.JSX.Element {
  const stroke = outline;
  const common = {
    stroke,
    strokeWidth: 2.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    fill: "none",
    opacity: 0.85,
  };

  // Returns [leftPath, rightPath]
  const paths = browPaths(mood);

  return (
    <g>
      <path d={paths[0]} {...common} />
      <path d={paths[1]} {...common} />
    </g>
  );
}

function browPaths(mood: ChibiMood): [string, string] {
  switch (mood) {
    // Gentle slight arcs
    case "neutral":
    case "happy":
    case "relaxed":
    case "proud":
      return [
        "M 44 49 Q 50 46 56 49",
        "M 72 49 Q 78 46 84 49",
      ];

    // Raised arcs (higher at y=44)
    case "excited":
    case "inspired":
    case "curious":
      return [
        "M 44 47 Q 50 42 56 46",
        "M 72 46 Q 78 42 84 47",
      ];

    // Straight diagonal slashes angling down-inward
    case "focused":
    case "determined":
    case "mischievous":
      return [
        "M 44 46 L 56 50",
        "M 72 50 L 84 46",
      ];

    // Sharp angry V (down-inward)
    case "frustrated":
      return [
        "M 44 46 L 56 52",
        "M 72 52 L 84 46",
      ];

    // Concerned softer curve down-inward
    case "worried":
      return [
        "M 44 47 Q 50 49 56 52",
        "M 72 52 Q 78 49 84 47",
      ];

    // Low flat lines at y=50
    case "tired":
    case "nostalgic":
      return [
        "M 44 50 L 56 50",
        "M 72 50 L 84 50",
      ];

    default:
      return [
        "M 44 49 Q 50 46 56 49",
        "M 72 49 Q 78 46 84 49",
      ];
  }
}

/* ------------------------------------------------------------------ */
/* Mouth                                                               */
/* ------------------------------------------------------------------ */

interface MouthProps {
  mood: ChibiMood;
  state: ChibiState;
  outline: string;
  lip: string;
}

function Mouth({
  mood,
  state,
  outline,
  lip,
}: MouthProps): React.JSX.Element {
  // State overrides mood
  if (state === "talking") {
    return (
      <g>
        <ellipse
          cx={64}
          cy={86}
          rx={4}
          ry={5}
          fill="#4a1a2a"
          stroke={outline}
          strokeWidth={1.2}
        />
        <ellipse cx={64} cy={88} rx={2} ry={1.5} fill={lip} />
      </g>
    );
  }

  if (state === "thinking") {
    return (
      <g>
        <ellipse
          cx={64}
          cy={85}
          rx={2}
          ry={2}
          fill="#ffffff"
          stroke={outline}
          strokeWidth={1.2}
        />
      </g>
    );
  }

  // idle / walking / working / celebrating — mood-driven
  return mouthForMood(mood, outline, lip);
}

function mouthForMood(
  mood: ChibiMood,
  outline: string,
  lip: string,
): React.JSX.Element {
  switch (mood) {
    case "happy":
    case "excited":
    case "proud":
    case "inspired":
      return (
        <g>
          <path
            d="M 55 82 Q 64 92 73 82"
            fill={lip}
            fillOpacity={0.4}
            stroke={outline}
            strokeWidth={1.8}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <ellipse cx={64} cy={88} rx={2.5} ry={1.2} fill={lip} opacity={0.7} />
        </g>
      );

    case "mischievous":
      return (
        <g>
          <path
            d="M 56 82 Q 64 90 72 86"
            fill="none"
            stroke={outline}
            strokeWidth={1.8}
            strokeLinecap="round"
          />
        </g>
      );

    case "frustrated":
      return (
        <g>
          <path
            d="M 56 86 Q 64 80 72 86"
            fill="none"
            stroke={outline}
            strokeWidth={1.8}
            strokeLinecap="round"
          />
        </g>
      );

    case "tired":
    case "nostalgic":
      return (
        <g>
          <line
            x1={60}
            y1={85}
            x2={68}
            y2={85}
            stroke={outline}
            strokeWidth={1.8}
            strokeLinecap="round"
          />
        </g>
      );

    case "worried":
    case "curious":
      return (
        <g>
          <path
            d="M 58 85 q 2 -2 4 0 q 2 2 4 0 q 2 -2 4 0"
            fill="none"
            stroke={outline}
            strokeWidth={1.6}
            strokeLinecap="round"
          />
        </g>
      );

    case "focused":
    case "determined":
      return (
        <g>
          <line
            x1={59}
            y1={85}
            x2={69}
            y2={85}
            stroke={outline}
            strokeWidth={2}
            strokeLinecap="round"
          />
        </g>
      );

    case "relaxed":
    case "neutral":
    default:
      return (
        <g>
          <path
            d="M 57 83 Q 64 88 71 83"
            fill="none"
            stroke={outline}
            strokeWidth={1.6}
            strokeLinecap="round"
          />
        </g>
      );
  }
}

/* ------------------------------------------------------------------ */
/* Blush                                                               */
/* ------------------------------------------------------------------ */

interface BlushProps {
  mood: ChibiMood;
  blush: string;
}

function Blush({ mood, blush }: BlushProps): React.JSX.Element | null {
  const blushMoods: ChibiMood[] = [
    "happy",
    "excited",
    "proud",
    "inspired",
    "relaxed",
  ];
  if (!blushMoods.includes(mood)) return null;

  return (
    <g>
      {/* Left cheek */}
      <ellipse cx={42} cy={76} rx={5} ry={2.2} fill={blush} opacity={0.65} />
      <circle cx={40} cy={75} r={0.7} fill="#ffffff" opacity={0.85} />
      <circle cx={43} cy={76} r={0.6} fill="#ffffff" opacity={0.85} />

      {/* Right cheek */}
      <ellipse cx={86} cy={76} rx={5} ry={2.2} fill={blush} opacity={0.65} />
      <circle cx={84} cy={75} r={0.7} fill="#ffffff" opacity={0.85} />
      <circle cx={87} cy={76} r={0.6} fill="#ffffff" opacity={0.85} />
    </g>
  );
}

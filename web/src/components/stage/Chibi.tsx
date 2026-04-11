"use client";

import React from "react";

export interface ChibiProps {
  species?: string;
  mood?: string;
  state?: string;
  facingLeft?: boolean;
  walkPhase?: number;
  rarity?: string;
  bodyColor?: string;
  size?: number;
}

type SpeciesKind =
  | "cat"
  | "rabbit"
  | "fox"
  | "owl"
  | "bear"
  | "penguin"
  | "hamster"
  | "dog"
  | "panda"
  | "duck"
  | "human";

const SKIN = "#fcd9b8";
const OUTLINE = "#1e293b";
const HAIR_DEFAULT = "#8b5a3c";
const BLUSH = "#f9a8a8";

const SPECIES_MAP: Record<string, SpeciesKind> = {
  cat: "cat",
  tiger: "cat",
  lion: "cat",
  rabbit: "rabbit",
  hare: "rabbit",
  jackalope: "rabbit",
  fox: "fox",
  wolf: "fox",
  kitsune: "fox",
  owl: "owl",
  eagle: "owl",
  phoenix: "owl",
  bear: "bear",
  grizzly: "bear",
  "polar bear": "bear",
  polarbear: "bear",
  penguin: "penguin",
  emperor: "penguin",
  "ice dragon": "penguin",
  icedragon: "penguin",
  hamster: "hamster",
  chinchilla: "hamster",
  capybara: "hamster",
  dog: "dog",
  husky: "dog",
  "dire wolf": "dog",
  direwolf: "dog",
  panda: "panda",
  "red panda": "panda",
  redpanda: "panda",
  "spirit bear": "panda",
  spiritbear: "panda",
  duck: "duck",
  swan: "duck",
  thunderbird: "duck",
};

const SPECIES_BODY: Record<SpeciesKind, string> = {
  cat: "#f59e0b",
  rabbit: "#f3e8d2",
  fox: "#f97316",
  owl: "#a16207",
  bear: "#92400e",
  penguin: "#1f2937",
  hamster: "#fbbf24",
  dog: "#d4a373",
  panda: "#111827",
  duck: "#facc15",
  human: "#22d3ee",
};

const SPECIES_HAIR: Record<SpeciesKind, string> = {
  cat: "#5b3a29",
  rabbit: "#c9a87c",
  fox: "#b45309",
  owl: "#78350f",
  bear: "#451a03",
  penguin: "#0f172a",
  hamster: "#a16207",
  dog: "#78350f",
  panda: "#0f172a",
  duck: "#ca8a04",
  human: HAIR_DEFAULT,
};

function resolveSpecies(species?: string): SpeciesKind {
  if (!species) return "human";
  const key = species.toLowerCase().trim();
  return SPECIES_MAP[key] ?? "human";
}

function Eyes({ mood, species }: { mood?: string; species: SpeciesKind }) {
  const leftX = 26;
  const rightX = 38;
  const y = 38;
  const owlLike = species === "owl";

  switch (mood) {
    case "happy":
    case "proud":
    case "relaxed":
      return (
        <g fill="none" stroke={OUTLINE} strokeWidth="1.6" strokeLinecap="round">
          <path d={`M${leftX - 3} ${y} Q${leftX} ${y - 3} ${leftX + 3} ${y}`} />
          <path d={`M${rightX - 3} ${y} Q${rightX} ${y - 3} ${rightX + 3} ${y}`} />
        </g>
      );
    case "frustrated":
    case "determined":
      return (
        <g fill="none" stroke={OUTLINE} strokeWidth="1.6" strokeLinecap="round">
          <path d={`M${leftX - 3} ${y - 2} L${leftX + 3} ${y + 2}`} />
          <path d={`M${leftX + 3} ${y - 2} L${leftX - 3} ${y + 2}`} />
          <path d={`M${rightX - 3} ${y - 2} L${rightX + 3} ${y + 2}`} />
          <path d={`M${rightX + 3} ${y - 2} L${rightX - 3} ${y + 2}`} />
        </g>
      );
    case "excited":
    case "inspired":
      return (
        <g fill={OUTLINE}>
          <text x={leftX} y={y + 2} fontSize="7" textAnchor="middle" fontFamily="serif">★</text>
          <text x={rightX} y={y + 2} fontSize="7" textAnchor="middle" fontFamily="serif">★</text>
        </g>
      );
    case "tired":
    case "nostalgic":
      return (
        <g fill="none" stroke={OUTLINE} strokeWidth="1.6" strokeLinecap="round">
          <path d={`M${leftX - 3} ${y} L${leftX + 3} ${y}`} />
          <path d={`M${rightX - 3} ${y} L${rightX + 3} ${y}`} />
        </g>
      );
    case "worried":
    case "curious": {
      const r = owlLike ? 3 : 2;
      return (
        <g>
          <circle cx={leftX} cy={y} r={r} fill="#fff" stroke={OUTLINE} strokeWidth="1" />
          <circle cx={rightX} cy={y} r={r} fill="#fff" stroke={OUTLINE} strokeWidth="1" />
          <circle cx={leftX} cy={y + 0.5} r={0.9} fill={OUTLINE} />
          <circle cx={rightX} cy={y + 0.5} r={0.9} fill={OUTLINE} />
        </g>
      );
    }
    case "focused":
    case "mischievous":
      return (
        <g fill={OUTLINE}>
          <rect x={leftX - 3} y={y - 0.8} width={6} height={1.6} rx={0.6} />
          <rect x={rightX - 3} y={y - 0.8} width={6} height={1.6} rx={0.6} />
        </g>
      );
    default: {
      const r = owlLike ? 2.4 : 1.4;
      return (
        <g fill={OUTLINE}>
          <circle cx={leftX} cy={y} r={r} />
          <circle cx={rightX} cy={y} r={r} />
        </g>
      );
    }
  }
}

function Mouth({ mood, state, species }: { mood?: string; state?: string; species: SpeciesKind }) {
  const cx = 32;
  const cy = 46;
  if (species === "duck") {
    return (
      <polygon
        points={`${cx - 4},${cy - 1} ${cx + 4},${cy - 1} ${cx + 3},${cy + 2} ${cx - 3},${cy + 2}`}
        fill="#f59e0b"
        stroke={OUTLINE}
        strokeWidth="1"
      />
    );
  }
  if (state === "talking") {
    return <ellipse cx={cx} cy={cy} rx={2} ry={1.5} fill={OUTLINE} />;
  }
  if (mood === "frustrated") {
    return (
      <path
        d={`M${cx - 3} ${cy + 1} Q${cx} ${cy - 1} ${cx + 3} ${cy + 1}`}
        fill="none"
        stroke={OUTLINE}
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    );
  }
  if (mood === "happy" || mood === "excited" || mood === "proud") {
    return (
      <path
        d={`M${cx - 3} ${cy - 1} Q${cx} ${cy + 2} ${cx + 3} ${cy - 1}`}
        fill="none"
        stroke={OUTLINE}
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    );
  }
  return (
    <path
      d={`M${cx - 2} ${cy} L${cx + 2} ${cy}`}
      fill="none"
      stroke={OUTLINE}
      strokeWidth="1.4"
      strokeLinecap="round"
    />
  );
}

function SpeciesHeadExtras({ species }: { species: SpeciesKind }) {
  switch (species) {
    case "cat":
      return (
        <g fill="#f59e0b" stroke={OUTLINE} strokeWidth="1">
          <polygon points="20,18 24,10 28,18" />
          <polygon points="36,18 40,10 44,18" />
          <polygon points="22,16 24,12 26,16" fill="#f9a8a8" stroke="none" />
          <polygon points="38,16 40,12 42,16" fill="#f9a8a8" stroke="none" />
        </g>
      );
    case "rabbit":
      return (
        <g fill="#f3e8d2" stroke={OUTLINE} strokeWidth="1">
          <ellipse cx="26" cy="10" rx="2.5" ry="8" />
          <ellipse cx="38" cy="10" rx="2.5" ry="8" />
          <ellipse cx="26" cy="11" rx="1" ry="5" fill="#f9a8a8" stroke="none" />
          <ellipse cx="38" cy="11" rx="1" ry="5" fill="#f9a8a8" stroke="none" />
        </g>
      );
    case "fox":
      return (
        <g fill="#f97316" stroke={OUTLINE} strokeWidth="1">
          <polygon points="19,20 22,8 28,18" />
          <polygon points="36,18 42,8 45,20" />
          <polygon points="23,16 24,12 26,16" fill="#fff" stroke="none" />
          <polygon points="38,16 40,12 41,16" fill="#fff" stroke="none" />
        </g>
      );
    case "bear":
      return (
        <g fill="#92400e" stroke={OUTLINE} strokeWidth="1">
          <circle cx="22" cy="18" r="4" />
          <circle cx="42" cy="18" r="4" />
          <circle cx="22" cy="18" r="2" fill="#451a03" stroke="none" />
          <circle cx="42" cy="18" r="2" fill="#451a03" stroke="none" />
        </g>
      );
    case "penguin":
      return (
        <path
          d="M16 30 Q32 8 48 30 Q40 22 32 22 Q24 22 16 30 Z"
          fill="#0f172a"
          stroke={OUTLINE}
          strokeWidth="1"
        />
      );
    case "hamster":
      return (
        <g fill="#fbbf24" stroke={OUTLINE} strokeWidth="1">
          <circle cx="22" cy="20" r="3" />
          <circle cx="42" cy="20" r="3" />
          <circle cx="22" cy="20" r="1.5" fill="#f9a8a8" stroke="none" />
          <circle cx="42" cy="20" r="1.5" fill="#f9a8a8" stroke="none" />
        </g>
      );
    case "dog":
      return (
        <g fill="#78350f" stroke={OUTLINE} strokeWidth="1">
          <ellipse cx="18" cy="28" rx="4" ry="8" />
          <ellipse cx="46" cy="28" rx="4" ry="8" />
        </g>
      );
    case "panda":
      return (
        <g>
          <circle cx="22" cy="18" r="4" fill="#0f172a" stroke={OUTLINE} strokeWidth="1" />
          <circle cx="42" cy="18" r="4" fill="#0f172a" stroke={OUTLINE} strokeWidth="1" />
          <ellipse cx="26" cy="38" rx="4" ry="3" fill="#0f172a" />
          <ellipse cx="38" cy="38" rx="4" ry="3" fill="#0f172a" />
        </g>
      );
    case "owl":
      return (
        <g fill="#78350f" stroke={OUTLINE} strokeWidth="1">
          <polygon points="22,20 24,14 27,20" />
          <polygon points="37,20 40,14 42,20" />
        </g>
      );
    case "duck":
      return null;
    default:
      return null;
  }
}

function SpeciesBackExtras({ species }: { species: SpeciesKind }) {
  switch (species) {
    case "cat":
      return (
        <path
          d="M44 68 Q58 64 56 50 Q54 46 52 50 Q54 62 44 64 Z"
          fill="#f59e0b"
          stroke={OUTLINE}
          strokeWidth="1"
        />
      );
    case "fox":
      return (
        <path
          d="M44 68 Q62 66 60 48 Q56 44 52 50 Q56 62 44 64 Z"
          fill="#f97316"
          stroke={OUTLINE}
          strokeWidth="1"
        />
      );
    case "dog":
      return (
        <path
          d="M44 66 Q56 60 54 52 Q50 50 48 54 Q50 60 44 62 Z"
          fill="#78350f"
          stroke={OUTLINE}
          strokeWidth="1"
        />
      );
    case "hamster":
      return <ellipse cx="48" cy="66" rx="3" ry="2" fill="#fbbf24" stroke={OUTLINE} strokeWidth="1" />;
    case "owl":
      return (
        <g fill="#a16207" stroke={OUTLINE} strokeWidth="1">
          <path d="M16 58 Q8 52 12 70 Q18 66 22 62 Z" />
          <path d="M48 62 Q58 66 52 70 Q56 52 48 58 Z" />
        </g>
      );
    case "duck":
      return (
        <g fill="#fde68a" stroke={OUTLINE} strokeWidth="1">
          <path d="M18 58 Q12 62 16 68 Q20 64 22 62 Z" />
          <path d="M46 62 Q52 64 48 68 Q44 64 42 62 Z" />
        </g>
      );
    default:
      return null;
  }
}

function StateEffects({ state }: { state?: string }) {
  if (state === "working") {
    return (
      <g>
        <circle cx="44" cy="34" r="1.2" fill="#60a5fa" />
        <text x="32" y="6" fontSize="6" textAnchor="middle" fill="#64748b">⚙</text>
      </g>
    );
  }
  if (state === "thinking") {
    return (
      <text x="32" y="6" fontSize="8" textAnchor="middle" fill="#64748b" fontWeight="bold">?</text>
    );
  }
  if (state === "celebrating") {
    return (
      <g fill="#fbbf24">
        <text x="12" y="10" fontSize="6" textAnchor="middle">✦</text>
        <text x="52" y="8" fontSize="6" textAnchor="middle">✦</text>
        <text x="32" y="4" fontSize="6" textAnchor="middle">★</text>
      </g>
    );
  }
  return null;
}

export default function Chibi({
  species,
  mood = "happy",
  state = "idle",
  facingLeft = false,
  walkPhase,
  rarity = "common",
  bodyColor,
  size = 72,
}: ChibiProps) {
  const kind = resolveSpecies(species);
  const body = bodyColor ?? SPECIES_BODY[kind];
  const hair = SPECIES_HAIR[kind];
  const isWalking = walkPhase !== undefined;
  const phase = walkPhase ?? 0;
  const legSwing = isWalking ? Math.sin(phase * Math.PI * 2) * 18 : 0;
  const armSwing = isWalking ? Math.sin(phase * Math.PI * 2 + Math.PI) * 14 : 0;
  const bodyBob = isWalking ? Math.abs(Math.sin(phase * Math.PI * 2)) * 2 : 0;
  const idleBob = 0;
  const celebrating = state === "celebrating";
  const showBlush = mood === "excited" || mood === "proud" || mood === "inspired";

  const glowFilter =
    rarity === "legendary"
      ? "drop-shadow(0 0 6px #fbbf24) drop-shadow(0 0 10px #f59e0b)"
      : rarity === "rare"
      ? "drop-shadow(0 0 5px #22d3ee)"
      : rarity === "uncommon"
      ? "drop-shadow(0 0 2px #a7f3d0)"
      : undefined;

  const width = (size * 64) / 96;
  const headY = 4 + bodyBob + idleBob;

  const leftArmAngle = celebrating ? -150 : armSwing;
  const rightArmAngle = celebrating ? 150 : -armSwing;

  return (
    <svg
      width={width}
      height={size}
      viewBox="0 0 64 96"
      xmlns="http://www.w3.org/2000/svg"
      style={{
        transform: facingLeft ? "scaleX(-1)" : undefined,
        filter: glowFilter,
        overflow: "visible",
      }}
    >
      <ellipse cx="32" cy="92" rx="14" ry="2" fill="#0f172a" opacity="0.2" />

      <SpeciesBackExtras species={kind} />

      <g transform={`translate(0, ${bodyBob})`}>
        <g transform={`rotate(${leftArmAngle} 20 58)`}>
          <rect
            x="16"
            y="56"
            width="6"
            height="14"
            rx="3"
            fill={body}
            stroke={OUTLINE}
            strokeWidth="1.2"
          />
          <circle cx="19" cy="70" r="2.6" fill={SKIN} stroke={OUTLINE} strokeWidth="1" />
        </g>

        <g transform={`rotate(${rightArmAngle} 44 58)`}>
          <rect
            x="42"
            y="56"
            width="6"
            height="14"
            rx="3"
            fill={body}
            stroke={OUTLINE}
            strokeWidth="1.2"
          />
          <circle cx="45" cy="70" r="2.6" fill={SKIN} stroke={OUTLINE} strokeWidth="1" />
        </g>

        <rect
          x="20"
          y="54"
          width="24"
          height="22"
          rx="5"
          fill={body}
          stroke={OUTLINE}
          strokeWidth="1.4"
        />
        <rect x="20" y="70" width="24" height="6" fill="#1e293b" opacity="0.15" />

        <g transform={`rotate(${legSwing} 26 76)`}>
          <rect
            x="22"
            y="76"
            width="7"
            height="12"
            rx="2"
            fill="#475569"
            stroke={OUTLINE}
            strokeWidth="1.2"
          />
          <rect
            x="21"
            y="86"
            width="9"
            height="4"
            rx="1.5"
            fill={OUTLINE}
          />
        </g>
        <g transform={`rotate(${-legSwing} 38 76)`}>
          <rect
            x="35"
            y="76"
            width="7"
            height="12"
            rx="2"
            fill="#475569"
            stroke={OUTLINE}
            strokeWidth="1.2"
          />
          <rect
            x="34"
            y="86"
            width="9"
            height="4"
            rx="1.5"
            fill={OUTLINE}
          />
        </g>
      </g>

      <g transform={`translate(0, ${headY})`}>
        <SpeciesHeadExtras species={kind} />
        <ellipse
          cx="32"
          cy="36"
          rx="18"
          ry="18"
          fill={SKIN}
          stroke={OUTLINE}
          strokeWidth="1.6"
        />
        <path
          d="M14 32 Q18 14 32 14 Q46 14 50 32 Q48 24 32 22 Q16 24 14 32 Z"
          fill={hair}
          stroke={OUTLINE}
          strokeWidth="1.2"
        />
        <Eyes mood={mood} species={kind} />
        <Mouth mood={mood} state={state} species={kind} />
        {showBlush && (
          <g fill={BLUSH} opacity="0.7">
            <ellipse cx="22" cy="42" rx="2.4" ry="1.4" />
            <ellipse cx="42" cy="42" rx="2.4" ry="1.4" />
          </g>
        )}
        <StateEffects state={state} />
      </g>
    </svg>
  );
}

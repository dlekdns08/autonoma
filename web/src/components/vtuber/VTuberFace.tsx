"use client";

/**
 * A procedural SVG VTuber face.
 *
 * We compose the character from independent SVG layers — skin, hair,
 * eyes, brows, mouth, species ears, rarity aura — so each layer can be
 * animated from its own signal:
 *
 *   - Mouth shape is driven by the live audio amplitude from
 *     `useAgentVoice` (0..1). We pick one of four mouth paths per frame
 *     inside a RAF loop, avoiding a React re-render per audio sample.
 *   - Blink is a self-contained setInterval with per-agent offset so a
 *     group of agents doesn't blink in lockstep.
 *   - Brows are driven by the agent's mood string — small angle/offset
 *     tweaks, no animation needed because mood changes on the beat.
 *   - Species maps to an ear accessory (kemonomimi style). The base face
 *     is always human; the species hint is a single decorative layer.
 *   - Rarity drives a glow + particle aura behind the head.
 *
 * Deliberately *not* a full Live2D rig — we get ~80% of the VTuber feel
 * with zero asset authoring and a total of one SVG per character.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentData } from "@/lib/types";
import { seedForAgent, type FaceSeed } from "./faceSeed";

interface Props {
  agent: AgentData;
  /** Amplitude getter from useAgentVoice. Called per-frame. */
  getMouthAmplitude?: (name: string) => number;
  /** Whether this face is the current speaker spotlight. Non-speakers
   *  render smaller and slightly desaturated. */
  spotlight?: boolean;
  /** Optional click → open agent modal. */
  onClick?: () => void;
}

// ── Shape tables ─────────────────────────────────────────────────────
//
// All paths are drawn into a 200×260 viewBox so layouts can scale the
// whole face uniformly. Face center is roughly (100, 140).

const MOUTH_PATHS = {
  // Closed — a soft line with a tiny smile curve.
  closed: "M 88 176 Q 100 180 112 176",
  // Small open — narrow oval, as if mid-consonant.
  small: "M 92 174 Q 100 182 108 174 Q 100 186 92 174 Z",
  // Medium open — wider, vowel-ish.
  medium: "M 88 172 Q 100 184 112 172 Q 100 192 88 172 Z",
  // Wide open — a proper shout.
  wide: "M 84 170 Q 100 190 116 170 Q 100 202 84 170 Z",
};
type MouthShape = keyof typeof MOUTH_PATHS;

function amplitudeToShape(amp: number): MouthShape {
  if (amp < 0.05) return "closed";
  if (amp < 0.18) return "small";
  if (amp < 0.4) return "medium";
  return "wide";
}

// ── Mood → eyebrow geometry ──────────────────────────────────────────
//
// Each entry yields inner/outer Y offsets and angle — applied via an SVG
// `transform` on the brow group. Keeping this data-driven lets us tweak
// expressions in one place.
interface BrowStyle {
  /** Overall vertical offset for both brows (negative = raised). */
  dy: number;
  /** Inner end lift (higher = surprised/worried). */
  innerLift: number;
  /** Rotation in degrees applied inward (negative = angry/furrow). */
  angle: number;
}

const MOOD_BROWS: Record<string, BrowStyle> = {
  happy:       { dy: -1, innerLift: 0, angle: 0 },
  excited:     { dy: -4, innerLift: -3, angle: 4 },
  proud:       { dy: -3, innerLift: 0, angle: 2 },
  focused:     { dy: 0, innerLift: 0, angle: -2 },
  determined:  { dy: 0, innerLift: 2, angle: -3 },
  frustrated:  { dy: 2, innerLift: 4, angle: -8 },
  worried:     { dy: -2, innerLift: -5, angle: 6 },
  tired:       { dy: 3, innerLift: 3, angle: 4 },
  relaxed:     { dy: 1, innerLift: 0, angle: 1 },
  curious:     { dy: -2, innerLift: -2, angle: 1 },
};
const BROW_DEFAULT: BrowStyle = { dy: 0, innerLift: 0, angle: 0 };

// ── Species → ear overlay ────────────────────────────────────────────
//
// Human base + species ears (kemonomimi). Keeps the anime-human look
// while preserving the backend's 10 species as a visual signature.

interface SpeciesAccent {
  /** SVG paths drawn on top of the hair. */
  render: (color: string, secondary: string) => React.ReactNode;
}

function catEars(color: string, accent: string): React.ReactNode {
  return (
    <g>
      <path d="M 52 56 L 38 18 L 74 46 Z" fill={color} />
      <path d="M 148 56 L 162 18 L 126 46 Z" fill={color} />
      <path d="M 56 50 L 46 28 L 68 44 Z" fill={accent} opacity={0.7} />
      <path d="M 144 50 L 154 28 L 132 44 Z" fill={accent} opacity={0.7} />
    </g>
  );
}

function rabbitEars(color: string, accent: string): React.ReactNode {
  return (
    <g>
      <path d="M 68 50 C 56 20 60 -4 76 2 C 84 4 86 30 82 58 Z" fill={color} />
      <path d="M 132 50 C 144 20 140 -4 124 2 C 116 4 114 30 118 58 Z" fill={color} />
      <path d="M 72 48 C 66 26 68 10 76 10 C 80 12 80 30 78 52 Z" fill={accent} opacity={0.8} />
      <path d="M 128 48 C 134 26 132 10 124 10 C 120 12 120 30 122 52 Z" fill={accent} opacity={0.8} />
    </g>
  );
}

function foxEars(color: string, accent: string): React.ReactNode {
  return (
    <g>
      <path d="M 54 58 L 34 10 L 76 44 Z" fill={color} />
      <path d="M 146 58 L 166 10 L 124 44 Z" fill={color} />
      <path d="M 50 24 L 42 14 L 58 28 Z" fill="#f8f3e9" />
      <path d="M 150 24 L 158 14 L 142 28 Z" fill="#f8f3e9" />
      <path d="M 58 48 L 48 22 L 70 42 Z" fill={accent} opacity={0.65} />
      <path d="M 142 48 L 152 22 L 130 42 Z" fill={accent} opacity={0.65} />
    </g>
  );
}

function wolfEars(color: string, accent: string): React.ReactNode {
  return (
    <g>
      <path d="M 50 60 L 36 16 L 78 48 Z" fill={color} />
      <path d="M 150 60 L 164 16 L 122 48 Z" fill={color} />
      <path d="M 54 52 L 46 24 L 72 46 Z" fill={accent} opacity={0.6} />
      <path d="M 146 52 L 154 24 L 128 46 Z" fill={accent} opacity={0.6} />
    </g>
  );
}

function owlTufts(color: string): React.ReactNode {
  return (
    <g>
      <path d="M 66 34 C 54 8 70 10 74 28 Z" fill={color} />
      <path d="M 134 34 C 146 8 130 10 126 28 Z" fill={color} />
    </g>
  );
}

function bearEars(color: string, accent: string): React.ReactNode {
  return (
    <g>
      <circle cx={58} cy={48} r={16} fill={color} />
      <circle cx={142} cy={48} r={16} fill={color} />
      <circle cx={58} cy={50} r={8} fill={accent} opacity={0.7} />
      <circle cx={142} cy={50} r={8} fill={accent} opacity={0.7} />
    </g>
  );
}

function dogEars(color: string, accent: string): React.ReactNode {
  return (
    <g>
      <path d="M 48 62 C 30 50 32 30 54 38 L 68 70 Z" fill={color} />
      <path d="M 152 62 C 170 50 168 30 146 38 L 132 70 Z" fill={color} />
      <path d="M 54 58 C 44 50 46 40 58 44 L 66 62 Z" fill={accent} opacity={0.55} />
      <path d="M 146 58 C 156 50 154 40 142 44 L 134 62 Z" fill={accent} opacity={0.55} />
    </g>
  );
}

function hamsterEars(color: string, accent: string): React.ReactNode {
  return (
    <g>
      <circle cx={62} cy={52} r={12} fill={color} />
      <circle cx={138} cy={52} r={12} fill={color} />
      <circle cx={62} cy={54} r={6} fill={accent} opacity={0.7} />
      <circle cx={138} cy={54} r={6} fill={accent} opacity={0.7} />
    </g>
  );
}

function pandaEars(color: string): React.ReactNode {
  return (
    <g>
      <circle cx={60} cy={48} r={14} fill="#1e1e1e" />
      <circle cx={140} cy={48} r={14} fill="#1e1e1e" />
      <circle cx={60} cy={48} r={7} fill={color} opacity={0.6} />
      <circle cx={140} cy={48} r={7} fill={color} opacity={0.6} />
    </g>
  );
}

function duckAccent(color: string): React.ReactNode {
  // Ducks get a little feather tuft rather than ears.
  return (
    <g>
      <path d="M 100 12 C 88 2 86 22 96 26 Z" fill={color} />
      <path d="M 100 12 C 112 2 114 22 104 26 Z" fill={color} opacity={0.8} />
    </g>
  );
}

function penguinAccent(): React.ReactNode {
  // Simple black "cap" sliver — penguin = sleek head.
  return (
    <path d="M 50 74 Q 100 44 150 74 L 150 84 Q 100 56 50 84 Z" fill="#1c1c28" opacity={0.55} />
  );
}

const SPECIES_ACCENT: Record<string, SpeciesAccent> = {
  cat:     { render: (c, a) => catEars(c, a) },
  rabbit:  { render: (c, a) => rabbitEars(c, a) },
  fox:     { render: (c, a) => foxEars(c, a) },
  owl:     { render: (c) => owlTufts(c) },
  bear:    { render: (c, a) => bearEars(c, a) },
  penguin: { render: () => penguinAccent() },
  hamster: { render: (c, a) => hamsterEars(c, a) },
  dog:     { render: (c, a) => dogEars(c, a) },
  panda:   { render: (c) => pandaEars(c) },
  duck:    { render: (c) => duckAccent(c) },
  // Evolved species — reuse the closest base.
  tiger:      { render: (c, a) => catEars(c, a) },
  lion:       { render: (c, a) => catEars(c, a) },
  wolf:       { render: (c, a) => wolfEars(c, a) },
  kitsune:    { render: (c, a) => foxEars(c, a) },
  hare:       { render: (c, a) => rabbitEars(c, a) },
  jackalope:  { render: (c, a) => rabbitEars(c, a) },
  eagle:      { render: (c) => owlTufts(c) },
  phoenix:    { render: (c) => owlTufts(c) },
  grizzly:    { render: (c, a) => bearEars(c, a) },
  "polar bear": { render: (c, a) => bearEars(c, a) },
  emperor:    { render: () => penguinAccent() },
  "ice dragon": { render: () => penguinAccent() },
  chinchilla: { render: (c, a) => hamsterEars(c, a) },
  capybara:   { render: (c, a) => hamsterEars(c, a) },
  husky:      { render: (c, a) => dogEars(c, a) },
  "dire wolf": { render: (c, a) => wolfEars(c, a) },
  "red panda": { render: (c) => pandaEars(c) },
  "spirit bear": { render: (c, a) => bearEars(c, a) },
  swan:       { render: (c) => duckAccent(c) },
  thunderbird: { render: (c) => duckAccent(c) },
};

// ── Hair templates ───────────────────────────────────────────────────
//
// Front layer (bangs visible over forehead) + back layer (volume behind
// head). Drawn using the seed-picked color.

function hairShort(color: string): React.ReactNode {
  return (
    <g>
      {/* back volume */}
      <path d="M 40 90 C 36 50 70 34 100 34 C 130 34 164 50 160 90 L 160 130 L 40 130 Z" fill={color} />
      {/* side bangs */}
      <path d="M 52 92 C 48 72 66 54 94 56 L 92 100 Z" fill={color} />
      <path d="M 148 92 C 152 72 134 54 106 56 L 108 100 Z" fill={color} />
      {/* center forelock */}
      <path d="M 90 54 C 100 48 110 52 112 70 L 100 80 L 88 70 Z" fill={color} />
    </g>
  );
}

function hairMedium(color: string): React.ReactNode {
  return (
    <g>
      <path d="M 34 92 C 32 54 68 30 100 30 C 132 30 168 54 166 92 L 170 170 L 30 170 Z" fill={color} />
      <path d="M 48 90 C 46 70 66 56 92 56 L 88 100 Z" fill={color} />
      <path d="M 152 90 C 154 70 134 56 108 56 L 112 100 Z" fill={color} />
      <path d="M 86 50 C 100 42 114 48 116 72 L 100 84 L 84 70 Z" fill={color} />
    </g>
  );
}

function hairLong(color: string): React.ReactNode {
  return (
    <g>
      <path d="M 28 92 C 26 50 66 26 100 26 C 134 26 174 50 172 92 L 180 240 L 20 240 Z" fill={color} />
      <path d="M 44 92 C 42 70 64 54 92 54 L 88 104 Z" fill={color} />
      <path d="M 156 92 C 158 70 136 54 108 54 L 112 104 Z" fill={color} />
      <path d="M 86 50 C 100 40 114 46 118 72 L 100 86 L 82 72 Z" fill={color} />
    </g>
  );
}

function hairTwintails(color: string): React.ReactNode {
  return (
    <g>
      <path d="M 34 92 C 32 54 68 30 100 30 C 132 30 168 54 166 92 L 166 128 L 34 128 Z" fill={color} />
      {/* twintails */}
      <path d="M 28 110 C 10 140 14 200 30 230 C 40 210 40 160 46 126 Z" fill={color} />
      <path d="M 172 110 C 190 140 186 200 170 230 C 160 210 160 160 154 126 Z" fill={color} />
      <path d="M 50 90 C 48 70 68 58 92 58 L 90 100 Z" fill={color} />
      <path d="M 150 90 C 152 70 132 58 108 58 L 110 100 Z" fill={color} />
      <path d="M 86 50 C 100 42 114 48 116 72 L 100 84 L 84 70 Z" fill={color} />
    </g>
  );
}

function hairPonytail(color: string): React.ReactNode {
  return (
    <g>
      <path d="M 36 92 C 34 54 68 30 100 30 C 132 30 166 54 164 92 L 166 140 L 34 140 Z" fill={color} />
      {/* pulled-back ponytail behind head */}
      <path d="M 158 100 C 198 120 200 190 172 232 C 164 204 150 160 146 110 Z" fill={color} />
      <path d="M 50 90 C 48 70 68 58 92 58 L 88 100 Z" fill={color} />
      <path d="M 150 90 C 152 74 140 66 120 62 L 122 86 Z" fill={color} />
      <path d="M 82 50 C 96 42 110 48 114 72 L 96 82 L 80 68 Z" fill={color} />
    </g>
  );
}

function hairBob(color: string): React.ReactNode {
  return (
    <g>
      <path d="M 38 92 C 36 56 70 32 100 32 C 130 32 164 56 162 92 L 162 150 L 38 150 Z" fill={color} />
      {/* straight bangs */}
      <path d="M 54 60 L 146 60 L 138 94 L 62 94 Z" fill={color} />
    </g>
  );
}

function renderHair(style: string, color: string): React.ReactNode {
  switch (style) {
    case "short": return hairShort(color);
    case "medium": return hairMedium(color);
    case "long": return hairLong(color);
    case "twintails": return hairTwintails(color);
    case "ponytail": return hairPonytail(color);
    case "bob": return hairBob(color);
    default: return hairMedium(color);
  }
}

// ── Rarity aura ──────────────────────────────────────────────────────

function RarityAura({ rarity }: { rarity: string | undefined }) {
  if (!rarity || rarity === "common") return null;
  const map: Record<string, { color: string; blur: number }> = {
    uncommon:  { color: "rgba(103, 232, 249, 0.35)", blur: 18 },
    rare:      { color: "rgba(196, 130, 246, 0.45)", blur: 24 },
    legendary: { color: "rgba(251, 191, 36, 0.55)", blur: 32 },
  };
  const cfg = map[rarity] ?? map.uncommon;
  return (
    <circle
      cx={100}
      cy={140}
      r={110}
      fill={cfg.color}
      style={{ filter: `blur(${cfg.blur}px)` }}
    />
  );
}

// ── Component ────────────────────────────────────────────────────────

export default function VTuberFace({
  agent,
  getMouthAmplitude,
  spotlight = false,
  onClick,
}: Props) {
  const seed = useMemo(() => seedForAgent(agent.name), [agent.name]);
  const brow = MOOD_BROWS[agent.mood] ?? BROW_DEFAULT;
  const accent = SPECIES_ACCENT[agent.species ?? ""];

  // ── Mouth shape via RAF, mutating a ref instead of React state to
  //    avoid 60Hz re-renders per character. We swap `d` on the same
  //    path element in place.
  const mouthRef = useRef<SVGPathElement | null>(null);
  const lastShapeRef = useRef<MouthShape>("closed");
  useEffect(() => {
    if (!getMouthAmplitude) return;
    let raf = 0;
    const tick = () => {
      const amp = getMouthAmplitude(agent.name);
      const shape = amplitudeToShape(amp);
      if (shape !== lastShapeRef.current) {
        lastShapeRef.current = shape;
        if (mouthRef.current) {
          mouthRef.current.setAttribute("d", MOUTH_PATHS[shape]);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [agent.name, getMouthAmplitude]);

  // ── Blink via setInterval, offset per-agent so a group doesn't
  //    blink in unison. Single flag drives a scale on the eye group.
  const [blinking, setBlinking] = useState(false);
  useEffect(() => {
    const firstDelay = seed.blinkOffset * seed.blinkPeriod * 1000;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const loop = () => {
      if (!active) return;
      setBlinking(true);
      timer = setTimeout(() => {
        setBlinking(false);
        const next = (seed.blinkPeriod + Math.random() * 2) * 1000;
        timer = setTimeout(loop, next);
      }, 130);
    };
    timer = setTimeout(loop, firstDelay);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [seed.blinkOffset, seed.blinkPeriod]);

  const rarityClass =
    agent.rarity === "legendary"
      ? "text-amber-300"
      : agent.rarity === "rare"
        ? "text-purple-300"
        : agent.rarity === "uncommon"
          ? "text-cyan-300"
          : "text-white/80";

  return (
    <div
      className={`relative flex flex-col items-center ${onClick ? "cursor-pointer" : ""} ${
        spotlight ? "" : "opacity-70 hover:opacity-100 transition-opacity"
      }`}
      onClick={onClick}
    >
      <svg
        viewBox="0 0 200 260"
        className="w-full h-full"
        style={{ transform: `scale(${seed.faceScale})` }}
      >
        <RarityAura rarity={agent.rarity} />

        {/* neck + collar hint */}
        <path
          d="M 78 210 L 122 210 L 130 250 L 70 250 Z"
          fill={seed.skin}
          opacity={0.9}
        />
        <rect x={62} y={244} width={76} height={16} rx={4} fill="#1a1a2e" opacity={0.9} />

        {/* back hair (renders behind head) */}
        <HairLayer seed={seed} layer="back" />

        {/* face oval */}
        <FaceShape skin={seed.skin} />

        {/* ears/accents sit on top of face + hair */}
        {accent && accent.render(seed.hairColor, seed.eyeColor)}

        {/* front hair (bangs) */}
        <HairLayer seed={seed} layer="front" />

        {/* eyes */}
        <Eyes color={seed.eyeColor} blinking={blinking} />

        {/* brows */}
        <Brows style={brow} />

        {/* nose hint — tiny shadow, keeps the face from looking flat */}
        <path
          d="M 100 148 Q 103 158 100 162"
          stroke={shade(seed.skin, -0.15)}
          strokeWidth={1.4}
          fill="none"
          strokeLinecap="round"
          opacity={0.6}
        />

        {/* mouth — mutated in place by the RAF loop */}
        <path
          ref={mouthRef}
          d={MOUTH_PATHS.closed}
          stroke="#3a1420"
          strokeWidth={2.2}
          strokeLinecap="round"
          fill="#2a0f18"
        />

        {/* small cheek blush when happy / excited */}
        {(agent.mood === "happy" || agent.mood === "excited" || agent.mood === "proud") && (
          <g opacity={0.5}>
            <ellipse cx={70} cy={170} rx={10} ry={5} fill="#f49da8" />
            <ellipse cx={130} cy={170} rx={10} ry={5} fill="#f49da8" />
          </g>
        )}
      </svg>

      {/* label + level below */}
      <div className={`mt-1 font-mono text-[11px] font-bold ${rarityClass} text-center`}>
        <div>{agent.name}</div>
        <div className="text-[9px] opacity-70">
          Lv{agent.level} · {agent.role.slice(0, 14)}
        </div>
      </div>
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────

function FaceShape({ skin }: { skin: string }) {
  return (
    <g>
      {/* main oval — a touch narrower at the chin for a mature look */}
      <path
        d="M 100 72 C 148 72 162 112 160 148 C 158 184 132 214 100 214 C 68 214 42 184 40 148 C 38 112 52 72 100 72 Z"
        fill={skin}
      />
      {/* jaw shadow for a hint of depth */}
      <path
        d="M 58 180 C 70 206 130 206 142 180 C 134 210 66 210 58 180 Z"
        fill={shade(skin, -0.12)}
        opacity={0.5}
      />
    </g>
  );
}

function HairLayer({ seed, layer }: { seed: FaceSeed; layer: "front" | "back" }) {
  // We just render the full hair group twice; the "back" layer is
  // clipped behind the face via ordering. This is simpler than
  // maintaining two path sets and looks fine at the SVG resolution we
  // draw at.
  if (layer === "back") return null;
  return <>{renderHair(seed.hairStyle, seed.hairColor)}</>;
}

function Eyes({ color, blinking }: { color: string; blinking: boolean }) {
  return (
    <g transform={blinking ? "translate(0, 0) scale(1, 0.08)" : ""} style={{ transformOrigin: "100px 144px", transition: "transform 80ms ease-out" }}>
      {/* whites */}
      <ellipse cx={72} cy={144} rx={14} ry={10} fill="#fefefe" />
      <ellipse cx={128} cy={144} rx={14} ry={10} fill="#fefefe" />
      {/* iris */}
      <ellipse cx={72} cy={144} rx={9} ry={9} fill={color} />
      <ellipse cx={128} cy={144} rx={9} ry={9} fill={color} />
      {/* pupil */}
      <circle cx={72} cy={146} r={4} fill="#0a0a14" />
      <circle cx={128} cy={146} r={4} fill="#0a0a14" />
      {/* specular highlight */}
      <circle cx={74} cy={140} r={2.4} fill="#ffffff" />
      <circle cx={130} cy={140} r={2.4} fill="#ffffff" />
      {/* upper lid shadow */}
      <path d="M 58 140 Q 72 132 86 140" stroke="#2a1a1a" strokeWidth={1.6} fill="none" strokeLinecap="round" />
      <path d="M 114 140 Q 128 132 142 140" stroke="#2a1a1a" strokeWidth={1.6} fill="none" strokeLinecap="round" />
      {/* lashes — short downward ticks at outer corners */}
      <path d="M 86 140 L 90 138" stroke="#2a1a1a" strokeWidth={1.4} strokeLinecap="round" />
      <path d="M 114 140 L 110 138" stroke="#2a1a1a" strokeWidth={1.4} strokeLinecap="round" />
    </g>
  );
}

function Brows({ style }: { style: BrowStyle }) {
  const { dy, innerLift, angle } = style;
  return (
    <g>
      {/* left brow: inner = right end (x=88) */}
      <g transform={`translate(0, ${dy}) rotate(${-angle}, 72, 124)`}>
        <path
          d={`M 58 126 Q 72 ${120 + innerLift} 88 ${126 + innerLift * 0.5}`}
          stroke="#2a1a1a"
          strokeWidth={3}
          fill="none"
          strokeLinecap="round"
        />
      </g>
      {/* right brow: inner = left end (x=112) */}
      <g transform={`translate(0, ${dy}) rotate(${angle}, 128, 124)`}>
        <path
          d={`M 112 ${126 + innerLift * 0.5} Q 128 ${120 + innerLift} 142 126`}
          stroke="#2a1a1a"
          strokeWidth={3}
          fill="none"
          strokeLinecap="round"
        />
      </g>
    </g>
  );
}

// ── Color helper ─────────────────────────────────────────────────────
// Lightens/darkens a hex string by a factor (-1..1). Used for face
// shading without pulling in a color library.
function shade(hex: string, factor: number): string {
  const m = hex.replace("#", "");
  const r = parseInt(m.slice(0, 2), 16);
  const g = parseInt(m.slice(2, 4), 16);
  const b = parseInt(m.slice(4, 6), 16);
  const adj = (c: number) =>
    Math.max(0, Math.min(255, Math.round(c + c * factor)));
  const hex2 = (n: number) => n.toString(16).padStart(2, "0");
  return `#${hex2(adj(r))}${hex2(adj(g))}${hex2(adj(b))}`;
}

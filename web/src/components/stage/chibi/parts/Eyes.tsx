"use client";

import * as React from "react";
import { useId } from "react";
import type { ChibiMood } from "../types";

export interface EyesProps {
  mood: ChibiMood;
  irisColor: string;
  outline: string;
}

/**
 * Anime-style eye library for the chibi stage.
 *
 * Coordinate anchors (shared across all parts):
 *   left  eye center  ≈ (50, 62), bbox (44,54)–(56,70)
 *   right eye center  ≈ (78, 62), bbox (72,54)–(84,70)
 *
 * Each mood produces its own visual treatment. Open eyes always include
 * a white sclera, an iris radial gradient, a black pupil, two specular
 * highlights, and a thick top eyelash with tick marks at the outer corner.
 */
export function Eyes(props: EyesProps): React.JSX.Element {
  const { mood, irisColor, outline } = props;
  const uid = useId();

  // ---- shared gradient + clip ids ---------------------------------------
  const irisGradId = `${uid}-eyes-${mood}-iris-grad`;
  const clipLId = `${uid}-eyes-${mood}-clip-l`;
  const clipRId = `${uid}-eyes-${mood}-clip-r`;

  // ---- shared definitions block (only emitted when iris is visible) -----
  const IrisDefs = (
    <defs>
      <radialGradient id={irisGradId} cx="50%" cy="30%" r="75%" fx="50%" fy="20%">
        <stop offset="0%" stopColor="#ffffff" stopOpacity="0.55" />
        <stop offset="35%" stopColor={irisColor} stopOpacity="0.95" />
        <stop offset="100%" stopColor="#000000" stopOpacity="0.55" />
      </radialGradient>
      <clipPath id={clipLId}><ellipse cx="50" cy="62" rx="5.5" ry="7.2" /></clipPath>
      <clipPath id={clipRId}><ellipse cx="78" cy="62" rx="5.5" ry="7.2" /></clipPath>
    </defs>
  );

  // -----------------------------------------------------------------------
  // Helper: a full anime open eye centered at (cx, cy).
  // The `tilt` parameter rotates the iris gaze and is used by `curious`.
  // -----------------------------------------------------------------------
  function OpenEye(opts: {
    cx: number;
    cy: number;
    rx?: number;
    ry?: number;
    irisOffsetX?: number;
    irisOffsetY?: number;
    clipId: string;
    cornerOuterX: number; // for lash ticks
  }) {
    const {
      cx,
      cy,
      rx = 5.5,
      ry = 7.2,
      irisOffsetX = 0,
      irisOffsetY = 0,
      clipId,
      cornerOuterX,
    } = opts;
    const ix = cx + irisOffsetX;
    const iy = cy + irisOffsetY;
    const dir = cornerOuterX > cx ? 1 : -1;
    return (
      <g>
        <ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="#ffffff" />
        <g clipPath={`url(#${clipId})`}>
          <ellipse cx={ix} cy={iy} rx={rx - 0.6} ry={ry - 0.4} fill={`url(#${irisGradId})`} />
          <ellipse cx={ix} cy={iy + 1.2} rx={rx - 0.6} ry={ry - 0.4} fill="#000" opacity="0.18" />
          <ellipse cx={ix} cy={iy + 0.4} rx={1.7} ry={2.4} fill="#0d0612" />
          <ellipse cx={ix - 1.4} cy={iy - 2.2} rx={1.4} ry={1.7} fill="#ffffff" />
          <circle cx={ix + 1.6} cy={iy + 2.6} r={0.7} fill="#ffffff" />
        </g>
        <ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="none" stroke={outline} strokeWidth={1.1} />
        <path
          d={`M ${cx - rx - 0.4} ${cy - ry * 0.55} Q ${cx} ${cy - ry - 1.6} ${cx + rx + 0.4} ${cy - ry * 0.55}`}
          fill="none" stroke={outline} strokeWidth={1.9} strokeLinecap="round"
        />
        <line
          x1={cornerOuterX} y1={cy - ry * 0.65}
          x2={cornerOuterX + dir * 1.6} y2={cy - ry - 0.6}
          stroke={outline} strokeWidth={0.9} strokeLinecap="round"
        />
        <line
          x1={cornerOuterX + dir * 0.4} y1={cy - ry * 0.4}
          x2={cornerOuterX + dir * 2.2} y2={cy - ry * 0.95}
          stroke={outline} strokeWidth={0.8} strokeLinecap="round"
        />
      </g>
    );
  }

  // -----------------------------------------------------------------------
  // Mood branches
  // -----------------------------------------------------------------------

  // ── neutral / focused ────────────────────────────────────────────────
  if (mood === "neutral" || mood === "focused") {
    return (
      <g>
        {IrisDefs}
        <OpenEye cx={50} cy={62} clipId={clipLId} cornerOuterX={44.5} />
        <OpenEye cx={78} cy={62} clipId={clipRId} cornerOuterX={83.5} />
      </g>
    );
  }

  // ── happy / relaxed: closed ^_^ arcs ─────────────────────────────────
  if (mood === "happy" || mood === "relaxed") {
    return (
      <g
        fill="none"
        stroke={outline}
        strokeWidth={2.1}
        strokeLinecap="round"
      >
        <path d="M 44.5 64 Q 50 56 55.5 64" />
        <path d="M 72.5 64 Q 78 56 83.5 64" />
        {/* tiny lash tick at outer corners */}
        <path d="M 44 63.5 L 42.8 62.4" strokeWidth={1.1} />
        <path d="M 56 63.5 L 57.2 62.4" strokeWidth={1.1} />
        <path d="M 72 63.5 L 70.8 62.4" strokeWidth={1.1} />
        <path d="M 84 63.5 L 85.2 62.4" strokeWidth={1.1} />
      </g>
    );
  }

  // ── excited / inspired: sparkly star highlights ──────────────────────
  if (mood === "excited" || mood === "inspired") {
    const star = (cx: number, cy: number, s: number) => {
      // 5-point star centered at (cx, cy), radius s
      const pts: string[] = [];
      for (let i = 0; i < 10; i++) {
        const r = i % 2 === 0 ? s : s * 0.42;
        const a = (Math.PI / 5) * i - Math.PI / 2;
        pts.push(`${(cx + Math.cos(a) * r).toFixed(2)},${(cy + Math.sin(a) * r).toFixed(2)}`);
      }
      return `M ${pts.join(" L ")} Z`;
    };
    return (
      <g>
        {IrisDefs}
        <OpenEye cx={50} cy={62} clipId={clipLId} cornerOuterX={44.5} />
        <OpenEye cx={78} cy={62} clipId={clipRId} cornerOuterX={83.5} />
        {/* overlay big sparkle stars on iris */}
        <g clipPath={`url(#${clipLId})`}>
          <path d={star(50, 60.5, 2.4)} fill="#ffffff" opacity="0.95" />
          <path d={star(51.5, 64.5, 1.0)} fill="#ffffff" opacity="0.85" />
        </g>
        <g clipPath={`url(#${clipRId})`}>
          <path d={star(78, 60.5, 2.4)} fill="#ffffff" opacity="0.95" />
          <path d={star(79.5, 64.5, 1.0)} fill="#ffffff" opacity="0.85" />
        </g>
        {/* extra little sparkle outside the eyes */}
        <path d={star(43, 55, 1.1)} fill="#fff4a3" opacity="0.9" />
        <path d={star(86, 56, 1.0)} fill="#fff4a3" opacity="0.9" />
      </g>
    );
  }

  // ── proud: half-closed confident look ────────────────────────────────
  if (mood === "proud") {
    return (
      <g>
        {IrisDefs}
        <OpenEye cx={50} cy={63} ry={6.4} clipId={clipLId} cornerOuterX={44.5} />
        <OpenEye cx={78} cy={63} ry={6.4} clipId={clipRId} cornerOuterX={83.5} />
        {/* heavy upper lid bar covering top third */}
        <path
          d="M 44 60.5 Q 50 58.5 56 60.5 L 56 63 L 44 63 Z"
          fill={outline}
        />
        <path
          d="M 72 60.5 Q 78 58.5 84 60.5 L 84 63 L 72 63 Z"
          fill={outline}
        />
      </g>
    );
  }

  // ── determined / mischievous: sharp narrow eyes ──────────────────────
  if (mood === "determined" || mood === "mischievous") {
    const tiltUp = mood === "mischievous";
    const lDash = "M 44 62 Q 50 58 56 62 Q 50 66 44 62 Z";
    const rDash = "M 72 62 Q 78 58 84 62 Q 78 66 72 62 Z";
    return (
      <g>
        <defs>
          <radialGradient id={irisGradId} cx="50%" cy="40%" r="70%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.6" />
            <stop offset="40%" stopColor={irisColor} stopOpacity="1" />
            <stop offset="100%" stopColor="#000000" stopOpacity="0.7" />
          </radialGradient>
          <clipPath id={clipLId}><path d={lDash} /></clipPath>
          <clipPath id={clipRId}><path d={rDash} /></clipPath>
        </defs>
        <path d={lDash} fill="#ffffff" stroke={outline} strokeWidth={1.2} />
        <g clipPath={`url(#${clipLId})`}>
          <ellipse cx={50} cy={62} rx={4.2} ry={4.2} fill={`url(#${irisGradId})`} />
          <ellipse cx={50} cy={62.4} rx={1.4} ry={2} fill="#0d0612" />
          <ellipse cx={48.8} cy={60.6} rx={0.9} ry={1} fill="#ffffff" />
        </g>
        <path d={rDash} fill="#ffffff" stroke={outline} strokeWidth={1.2} />
        <g clipPath={`url(#${clipRId})`}>
          <ellipse cx={78} cy={62} rx={4.2} ry={4.2} fill={`url(#${irisGradId})`} />
          <ellipse cx={78} cy={62.4} rx={1.4} ry={2} fill="#0d0612" />
          <ellipse cx={76.8} cy={60.6} rx={0.9} ry={1} fill="#ffffff" />
        </g>
        <path
          d={tiltUp ? "M 43.6 63 L 56.4 59.4" : "M 43.6 60 L 56.4 60.6"}
          stroke={outline} strokeWidth={2} fill="none" strokeLinecap="round"
        />
        <path
          d={tiltUp ? "M 71.6 59.4 L 84.4 63" : "M 71.6 60.6 L 84.4 60"}
          stroke={outline} strokeWidth={2} fill="none" strokeLinecap="round"
        />
        <line x1={43.6} y1={62} x2={42} y2={61} stroke={outline} strokeWidth={0.9} strokeLinecap="round" />
        <line x1={84.4} y1={62} x2={86} y2={61} stroke={outline} strokeWidth={0.9} strokeLinecap="round" />
      </g>
    );
  }

  // ── frustrated: angry slanted top edges ──────────────────────────────
  if (mood === "frustrated") {
    return (
      <g>
        {IrisDefs}
        {/* sclera + iris peeking under angry lid */}
        <ellipse cx={50} cy={63.5} rx={5.4} ry={5.4} fill="#ffffff" />
        <ellipse cx={78} cy={63.5} rx={5.4} ry={5.4} fill="#ffffff" />
        <g clipPath={`url(#${clipLId})`}>
          <ellipse cx={50} cy={63.6} rx={4.6} ry={5} fill={`url(#${irisGradId})`} />
          <ellipse cx={50} cy={64} rx={1.6} ry={2.1} fill="#0d0612" />
          <ellipse cx={48.7} cy={62.2} rx={1.1} ry={1.3} fill="#ffffff" />
        </g>
        <g clipPath={`url(#${clipRId})`}>
          <ellipse cx={78} cy={63.6} rx={4.6} ry={5} fill={`url(#${irisGradId})`} />
          <ellipse cx={78} cy={64} rx={1.6} ry={2.1} fill="#0d0612" />
          <ellipse cx={76.7} cy={62.2} rx={1.1} ry={1.3} fill="#ffffff" />
        </g>
        {/* downward slanted angry top edges (drawn as filled wedges) */}
        <path
          d="M 44 58.5 L 56 62 L 56 63.5 Q 50 60 44 63.5 Z"
          fill={outline}
        />
        <path
          d="M 72 62 L 84 58.5 Q 84 63.5 78 60 Q 72 63.5 72 63.5 Z"
          fill={outline}
        />
        {/* full eye outline */}
        <ellipse cx={50} cy={63.5} rx={5.4} ry={5.4} fill="none" stroke={outline} strokeWidth={1.1} />
        <ellipse cx={78} cy={63.5} rx={5.4} ry={5.4} fill="none" stroke={outline} strokeWidth={1.1} />
        {/* furrowed brow scowl ticks above */}
        <path d="M 45 56 L 54 58.5" stroke={outline} strokeWidth={1.1} strokeLinecap="round" />
        <path d="M 83 56 L 74 58.5" stroke={outline} strokeWidth={1.1} strokeLinecap="round" />
      </g>
    );
  }

  // ── tired / nostalgic: gentle horizontal lines + tiny tear ───────────
  if (mood === "tired" || mood === "nostalgic") {
    return (
      <g fill="none" stroke={outline} strokeLinecap="round">
        <path d="M 44 63 Q 50 65.5 56 63" strokeWidth={1.8} />
        <path d="M 72 63 Q 78 65.5 84 63" strokeWidth={1.8} />
        <path d="M 45 61.6 Q 50 60.4 55 61.6" strokeWidth={0.9} opacity={0.65} />
        <path d="M 73 61.6 Q 78 60.4 83 61.6" strokeWidth={0.9} opacity={0.65} />
        <path d="M 43.6 65 q -0.6 1.4 0 2.1 q 0.7 -0.7 0 -2.1 Z" fill="#9ecdff" strokeWidth={0.5} />
        <path d="M 84.4 65 q 0.6 1.4 0 2.1 q -0.7 -0.7 0 -2.1 Z" fill="#9ecdff" strokeWidth={0.5} />
      </g>
    );
  }

  // ── worried: round watery eyes with wavy bottom + big highlights ─────
  if (mood === "worried") {
    const lW = "M 44 60 Q 50 53 56 60 Q 55 67 53 67 Q 50 65 47 67 Q 45 67 44 60 Z";
    const rW = "M 72 60 Q 78 53 84 60 Q 83 67 81 67 Q 78 65 75 67 Q 73 67 72 60 Z";
    return (
      <g>
        <defs>
          <radialGradient id={irisGradId} cx="50%" cy="30%" r="80%">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.7" />
            <stop offset="40%" stopColor={irisColor} stopOpacity="0.95" />
            <stop offset="100%" stopColor="#000000" stopOpacity="0.55" />
          </radialGradient>
          <clipPath id={clipLId}><path d={lW} /></clipPath>
          <clipPath id={clipRId}><path d={rW} /></clipPath>
        </defs>
        <path d={lW} fill="#ffffff" stroke={outline} strokeWidth={1.1} />
        <g clipPath={`url(#${clipLId})`}>
          <ellipse cx={50} cy={62} rx={4.6} ry={5.6} fill={`url(#${irisGradId})`} />
          <ellipse cx={50} cy={62.6} rx={1.6} ry={2.2} fill="#0d0612" />
          <ellipse cx={48.5} cy={60} rx={1.7} ry={2} fill="#ffffff" />
          <circle cx={51.6} cy={64.6} r={0.8} fill="#ffffff" />
        </g>
        <path d={rW} fill="#ffffff" stroke={outline} strokeWidth={1.1} />
        <g clipPath={`url(#${clipRId})`}>
          <ellipse cx={78} cy={62} rx={4.6} ry={5.6} fill={`url(#${irisGradId})`} />
          <ellipse cx={78} cy={62.6} rx={1.6} ry={2.2} fill="#0d0612" />
          <ellipse cx={76.5} cy={60} rx={1.7} ry={2} fill="#ffffff" />
          <circle cx={79.6} cy={64.6} r={0.8} fill="#ffffff" />
        </g>
        <path d="M 44 59.5 Q 50 53.5 56 59.5" fill="none" stroke={outline} strokeWidth={1.7} strokeLinecap="round" />
        <path d="M 72 59.5 Q 78 53.5 84 59.5" fill="none" stroke={outline} strokeWidth={1.7} strokeLinecap="round" />
      </g>
    );
  }

  // ── curious: asymmetric eyes, iris offset upward ─────────────────────
  if (mood === "curious") {
    return (
      <g>
        {IrisDefs}
        <OpenEye cx={50} cy={62} rx={5.2} ry={6.6} irisOffsetY={-1.2} clipId={clipLId} cornerOuterX={44.8} />
        <OpenEye cx={78} cy={61.5} rx={5.9} ry={7.6} irisOffsetY={-1.4} irisOffsetX={0.4} clipId={clipRId} cornerOuterX={83.9} />
      </g>
    );
  }

  // ── fallback: neutral open eyes ──────────────────────────────────────
  return (
    <g>
      {IrisDefs}
      <OpenEye cx={50} cy={62} clipId={clipLId} cornerOuterX={44.5} />
      <OpenEye cx={78} cy={62} clipId={clipRId} cornerOuterX={83.5} />
    </g>
  );
}

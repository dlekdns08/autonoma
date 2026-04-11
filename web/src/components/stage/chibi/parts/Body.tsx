"use client";

import { useId } from "react";
import * as React from "react";
import { CHIBI_VIEWBOX } from "../types";

/**
 * Body — the chibi armature: torso, arms, legs, hands, feet.
 *
 * Outfit.tsx draws clothing ON TOP of this. The exported BODY_ANCHORS
 * lets the outfit re-use the same shoulder / hip pivots so sleeves and
 * pants can rotate in lockstep with the limbs.
 *
 * Walk cycle is purely transform-driven from `walkPhase` (0..1). The
 * parent component is responsible for body bob (vertical translate).
 */

const SHOE_FILL = "#1a1533";

const SHOULDER_L: [number, number] = [
  CHIBI_VIEWBOX.shoulderLx,
  CHIBI_VIEWBOX.shoulderY,
];
const SHOULDER_R: [number, number] = [
  CHIBI_VIEWBOX.shoulderRx,
  CHIBI_VIEWBOX.shoulderY,
];
const HIP_L: [number, number] = [CHIBI_VIEWBOX.hipLx, CHIBI_VIEWBOX.hipY];
const HIP_R: [number, number] = [CHIBI_VIEWBOX.hipRx, CHIBI_VIEWBOX.hipY];

export const BODY_ANCHORS = {
  shoulderL: SHOULDER_L,
  shoulderR: SHOULDER_R,
  hipL: HIP_L,
  hipR: HIP_R,
  torsoBox: { x: 36, y: 112, w: 56, h: 46 },
} as const;

export interface BodyProps {
  walkPhase?: number;
  celebrating?: boolean;
  skinBase: string;
  skinShade: string;
  outline: string;
  children?: React.ReactNode;
}

interface LimbCommonProps {
  skinBase: string;
  outline: string;
}

interface ArmProps extends LimbCommonProps {
  side: "left" | "right";
  angle: number;
}

/**
 * Tapered arm path. Drawn in local space starting at the shoulder anchor
 * (hard-coded to either SHOULDER_L or SHOULDER_R), so the wrapping <g>
 * just needs the rotate() pivot at that same anchor.
 */
function Arm({ side, angle, skinBase, outline }: ArmProps): React.JSX.Element {
  const isLeft = side === "left";
  const sx = isLeft ? SHOULDER_L[0] : SHOULDER_R[0];
  const sy = isLeft ? SHOULDER_L[1] : SHOULDER_R[1];

  // The arm hangs down and slightly outward from the shoulder. Direction
  // sign flips for left vs right so both arms curve away from the torso.
  const dir = isLeft ? -1 : 1;

  // Anchor points along the arm (shoulder -> elbow -> wrist).
  const elbowX = sx + dir * 4;
  const elbowY = sy + 16;
  const wristX = sx + dir * 2;
  const wristY = sy + 30;

  // Outer (away from torso) and inner contour offsets — narrower at wrist.
  const shoulderHalf = 4.6;
  const elbowHalf = 4.0;
  const wristHalf = 2.8;

  const outerStartX = sx + dir * shoulderHalf;
  const outerStartY = sy - 1.5;
  const outerElbowX = elbowX + dir * elbowHalf;
  const outerWristX = wristX + dir * wristHalf;

  const innerStartX = sx - dir * shoulderHalf;
  const innerStartY = sy + 0.5;
  const innerElbowX = elbowX - dir * elbowHalf;
  const innerWristX = wristX - dir * wristHalf;

  // Curving tapered path. We use Q (quadratic) curves through the elbow
  // so the silhouette has a soft bend rather than a hard angle.
  const d =
    `M ${outerStartX} ${outerStartY} ` +
    `Q ${outerElbowX} ${elbowY - 2}, ${outerWristX} ${wristY} ` +
    `Q ${wristX} ${wristY + 2}, ${innerWristX} ${wristY} ` +
    `Q ${innerElbowX} ${elbowY + 2}, ${innerStartX} ${innerStartY} ` +
    `Z`;

  return (
    <g transform={`rotate(${angle} ${sx} ${sy})`}>
      <path
        d={d}
        fill={skinBase}
        stroke={outline}
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      {/* hand */}
      <circle
        cx={wristX}
        cy={wristY + 2}
        r={4}
        fill={skinBase}
        stroke={outline}
        strokeWidth="1.6"
      />
    </g>
  );
}

interface LegProps extends LimbCommonProps {
  side: "left" | "right";
  angle: number;
}

/**
 * Tapered leg path with a knee bump and a chunky shoe.
 */
function Leg({ side, angle, skinBase, outline }: LegProps): React.JSX.Element {
  const isLeft = side === "left";
  const hx = isLeft ? HIP_L[0] : HIP_R[0];
  const hy = isLeft ? HIP_L[1] : HIP_R[1];

  // Slight outward fan so legs aren't perfectly parallel.
  const dir = isLeft ? -1 : 1;

  const kneeX = hx + dir * 0.5;
  const kneeY = 172;
  const ankleX = hx + dir * 0.2;
  const ankleY = 182;

  const hipHalf = 4.6;
  const kneeHalf = 3.8;
  const ankleHalf = 3.0;

  const outerHipX = hx + dir * hipHalf;
  const outerKneeX = kneeX + dir * kneeHalf;
  const outerAnkleX = ankleX + dir * ankleHalf;

  const innerHipX = hx - dir * hipHalf;
  const innerKneeX = kneeX - dir * kneeHalf;
  const innerAnkleX = ankleX - dir * ankleHalf;

  // Path: outer hip -> outer knee bump -> outer ankle -> across foot top
  // -> inner ankle -> inner knee -> inner hip -> close.
  const d =
    `M ${outerHipX} ${hy - 1} ` +
    `Q ${outerKneeX + dir * 0.6} ${kneeY}, ${outerAnkleX} ${ankleY} ` +
    `L ${innerAnkleX} ${ankleY} ` +
    `Q ${innerKneeX} ${kneeY}, ${innerHipX} ${hy - 1} ` +
    `Z`;

  // Shoe — a rounded chunky block straddling the ankle.
  const shoeCx = ankleX;
  const shoeCy = 184;
  const shoeW = 10;
  const shoeH = 5;

  return (
    <g transform={`rotate(${angle} ${hx} ${hy})`}>
      <path
        d={d}
        fill={skinBase}
        stroke={outline}
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      {/* knee highlight dot */}
      <circle
        cx={kneeX}
        cy={kneeY}
        r={1.2}
        fill={outline}
        opacity={0.25}
      />
      {/* shoe */}
      <rect
        x={shoeCx - shoeW / 2}
        y={shoeCy - shoeH / 2}
        width={shoeW}
        height={shoeH}
        rx={2.4}
        ry={2.4}
        fill={SHOE_FILL}
        stroke={outline}
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </g>
  );
}

/**
 * Torso — rounded skin shape narrower at the waist (~y=140), wider at hips.
 * Uses a vertical gradient from skinBase (top) to skinShade (bottom).
 */
function Torso({
  outline,
  gradId,
}: {
  outline: string;
  gradId: string;
}): React.JSX.Element {
  // Outline points (clockwise from top-left shoulder).
  // Top is across the shoulder line at y=112.
  // Waist pinches in at y=140.
  // Hips bulge out at y=158.
  const d =
    "M 40 112 " +
    "C 36 118, 36 130, 39 140 " + // left side: shoulder -> waist
    "C 36 150, 38 156, 42 159 " + // waist -> hip
    "Q 64 164, 86 159 " + // across hips
    "C 90 156, 92 150, 89 140 " + // right hip -> waist
    "C 92 130, 92 118, 88 112 " + // waist -> shoulder
    "Q 64 108, 40 112 " + // across shoulders / collar
    "Z";

  return (
    <path
      d={d}
      fill={`url(#${gradId})`}
      stroke={outline}
      strokeWidth="1.6"
      strokeLinejoin="round"
    />
  );
}

export function Body(props: BodyProps): React.JSX.Element {
  const { walkPhase, celebrating, skinBase, skinShade, outline, children } =
    props;
  const uid = useId();
  const torsoGradId = `${uid}-body-torso-grad`;

  // ---- Walk cycle math ----------------------------------------------------
  // walkPhase undefined => standing still, all swings = 0.
  // Otherwise sin-driven swings, arms phase-offset by π so they
  // counter-swing the legs. bodyBob is intentionally NOT applied here —
  // the parent wraps Body in <g transform="translate(0,bodyBob)">.
  const isWalking = walkPhase !== undefined;
  const phase = walkPhase ?? 0;
  const legSwing = isWalking ? Math.sin(phase * Math.PI * 2) * 20 : 0;
  const armSwing = isWalking
    ? Math.sin(phase * Math.PI * 2 + Math.PI) * 16
    : 0;

  // ---- Per-limb angles ----------------------------------------------------
  // Front (left) limb gets +swing, back (right) gets -swing so they
  // alternate. Celebrating freezes legs and throws arms up in a V.
  let leftArmAngle: number;
  let rightArmAngle: number;
  let leftLegAngle: number;
  let rightLegAngle: number;

  if (celebrating) {
    leftArmAngle = -150;
    rightArmAngle = 150;
    leftLegAngle = 0;
    rightLegAngle = 0;
  } else {
    leftArmAngle = armSwing;
    rightArmAngle = -armSwing;
    leftLegAngle = legSwing;
    rightLegAngle = -legSwing;
  }

  return (
    <g>
      <defs>
        <linearGradient
          id={torsoGradId}
          x1="0"
          y1="0"
          x2="0"
          y2="1"
        >
          <stop offset="0%" stopColor={skinBase} />
          <stop offset="100%" stopColor={skinShade} />
        </linearGradient>
      </defs>

      {/* 1. Back leg (right side) — drawn first so the front leg overlaps it */}
      <Leg
        side="right"
        angle={rightLegAngle}
        skinBase={skinBase}
        outline={outline}
      />

      {/* 2. Back arm (right side) */}
      <Arm
        side="right"
        angle={rightArmAngle}
        skinBase={skinBase}
        outline={outline}
      />

      {/* 3. Torso (skin only — clothing is layered on top by Outfit) */}
      <Torso outline={outline} gradId={torsoGradId} />

      {/* 4. Front arm (left side) */}
      <Arm
        side="left"
        angle={leftArmAngle}
        skinBase={skinBase}
        outline={outline}
      />

      {/* 5. Front leg (left side) */}
      <Leg
        side="left"
        angle={leftLegAngle}
        skinBase={skinBase}
        outline={outline}
      />

      {children}
    </g>
  );
}

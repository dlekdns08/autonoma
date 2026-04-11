"use client";

import type React from "react";
import type { ChibiRole } from "../types";

export type FaceAccessoryKind =
  | "none"
  | "round-glasses"
  | "square-glasses"
  | "eye-patch"
  | "freckles";

export interface FaceAccessoryProps {
  kind: FaceAccessoryKind;
  outline: string;
  frameColor?: string;
}

export function FaceAccessory(
  props: FaceAccessoryProps,
): React.JSX.Element | null {
  const { kind, outline, frameColor } = props;
  const frame = frameColor ?? outline;

  if (kind === "none") {
    return null;
  }

  if (kind === "round-glasses") {
    return (
      <g>
        {/* Left lens */}
        <circle
          cx={50}
          cy={62}
          r={9}
          fill="none"
          stroke={frame}
          strokeWidth={2.2}
        />
        {/* Right lens */}
        <circle
          cx={78}
          cy={62}
          r={9}
          fill="none"
          stroke={frame}
          strokeWidth={2.2}
        />
        {/* Bridge */}
        <line
          x1={59}
          y1={62}
          x2={69}
          y2={62}
          stroke={frame}
          strokeWidth={2.2}
          strokeLinecap="round"
        />
        {/* Temples */}
        <line
          x1={41}
          y1={62}
          x2={38}
          y2={60}
          stroke={frame}
          strokeWidth={2.2}
          strokeLinecap="round"
        />
        <line
          x1={87}
          y1={62}
          x2={90}
          y2={60}
          stroke={frame}
          strokeWidth={2.2}
          strokeLinecap="round"
        />
        {/* Reflections */}
        <line
          x1={45}
          y1={58}
          x2={48}
          y2={55}
          stroke="#ffffff"
          strokeWidth={1.2}
          strokeLinecap="round"
          opacity={0.85}
        />
        <line
          x1={73}
          y1={58}
          x2={76}
          y2={55}
          stroke="#ffffff"
          strokeWidth={1.2}
          strokeLinecap="round"
          opacity={0.85}
        />
      </g>
    );
  }

  if (kind === "square-glasses") {
    return (
      <g>
        {/* Left lens */}
        <rect
          x={40}
          y={54}
          width={20}
          height={16}
          rx={3}
          ry={3}
          fill="none"
          stroke={frame}
          strokeWidth={2.2}
        />
        {/* Right lens */}
        <rect
          x={68}
          y={54}
          width={20}
          height={16}
          rx={3}
          ry={3}
          fill="none"
          stroke={frame}
          strokeWidth={2.2}
        />
        {/* Bridge */}
        <line
          x1={60}
          y1={62}
          x2={68}
          y2={62}
          stroke={frame}
          strokeWidth={2.2}
          strokeLinecap="round"
        />
        {/* Temples */}
        <line
          x1={40}
          y1={60}
          x2={36}
          y2={58}
          stroke={frame}
          strokeWidth={2.2}
          strokeLinecap="round"
        />
        <line
          x1={88}
          y1={60}
          x2={92}
          y2={58}
          stroke={frame}
          strokeWidth={2.2}
          strokeLinecap="round"
        />
        {/* Reflections */}
        <line
          x1={43}
          y1={58}
          x2={47}
          y2={55}
          stroke="#ffffff"
          strokeWidth={1.2}
          strokeLinecap="round"
          opacity={0.85}
        />
        <line
          x1={71}
          y1={58}
          x2={75}
          y2={55}
          stroke="#ffffff"
          strokeWidth={1.2}
          strokeLinecap="round"
          opacity={0.85}
        />
      </g>
    );
  }

  if (kind === "eye-patch") {
    return (
      <g>
        {/* Patch over right eye */}
        <rect
          x={69}
          y={55}
          width={18}
          height={14}
          rx={3}
          ry={3}
          fill="#1a1a2a"
          stroke={outline}
          strokeWidth={1.2}
        />
        {/* Strap across head */}
        <line
          x1={69}
          y1={57}
          x2={40}
          y2={40}
          stroke="#1a1a2a"
          strokeWidth={1.6}
          strokeLinecap="round"
        />
      </g>
    );
  }

  if (kind === "freckles") {
    const dots: Array<[number, number]> = [
      [48, 72],
      [52, 70],
      [56, 72],
      [72, 70],
      [76, 72],
      [80, 70],
    ];
    return (
      <g>
        {dots.map(([cx, cy], i) => (
          <circle key={i} cx={cx} cy={cy} r={0.8} fill="#c7805a" />
        ))}
      </g>
    );
  }

  return null;
}

export function pickFaceAccessory(role: ChibiRole): FaceAccessoryKind {
  switch (role) {
    case "reviewer":
      return "round-glasses";
    case "coder":
      return "square-glasses";
    case "writer":
      return "freckles";
    case "tester":
      return "square-glasses";
    case "designer":
    case "director":
    case "generic":
    default:
      return "none";
  }
}

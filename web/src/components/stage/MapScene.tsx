"use client";

import React from "react";

export const GROUND_Y_PERCENT = 82;

export interface MapSceneProps {
  sky?: string;
  theme?: string;
  children?: React.ReactNode;
}

const SKY_GRADIENTS: Record<string, string> = {
  dawn: "from-orange-300 via-pink-300 to-indigo-400",
  day: "from-sky-300 via-sky-200 to-cyan-100",
  dusk: "from-purple-500 via-pink-400 to-orange-300",
  night: "from-slate-950 via-indigo-950 to-purple-900",
};

const STAR_POSITIONS = [
  { top: "6%", left: "8%", delay: "0s" },
  { top: "10%", left: "22%", delay: "0.4s" },
  { top: "4%", left: "37%", delay: "0.9s" },
  { top: "14%", left: "49%", delay: "1.3s" },
  { top: "8%", left: "61%", delay: "0.2s" },
  { top: "18%", left: "73%", delay: "1.1s" },
  { top: "5%", left: "85%", delay: "0.6s" },
  { top: "12%", left: "92%", delay: "1.6s" },
  { top: "22%", left: "15%", delay: "0.8s" },
  { top: "26%", left: "42%", delay: "1.5s" },
  { top: "20%", left: "66%", delay: "0.3s" },
  { top: "28%", left: "80%", delay: "1.0s" },
];

function Tree({ x, y, scale = 1, dark = false }: { x: number; y: number; scale?: number; dark?: boolean }) {
  const canopy = dark ? "#166534" : "#22c55e";
  const canopyDark = dark ? "#14532d" : "#16a34a";
  const trunk = "#7c2d12";
  return (
    <g transform={`translate(${x} ${y}) scale(${scale})`}>
      <rect x={-6} y={-18} width={12} height={22} rx={2} fill={trunk} />
      <circle cx={0} cy={-30} r={22} fill={canopy} />
      <circle cx={-14} cy={-22} r={16} fill={canopyDark} />
      <circle cx={14} cy={-22} r={16} fill={canopyDark} />
      <circle cx={0} cy={-44} r={16} fill={canopyDark} />
    </g>
  );
}

function House({ x, y, scale = 1 }: { x: number; y: number; scale?: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${scale})`}>
      <rect x={-28} y={-36} width={56} height={36} fill="#fde68a" />
      <polygon points="-34,-36 34,-36 0,-66" fill="#b91c1c" />
      <rect x={-8} y={-22} width={16} height={22} fill="#78350f" />
      <rect x={12} y={-30} width={12} height={10} fill="#bae6fd" />
      <rect x={-24} y={-30} width={12} height={10} fill="#bae6fd" />
    </g>
  );
}

function Mushroom({ x, y, scale = 1 }: { x: number; y: number; scale?: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${scale})`}>
      <rect x={-4} y={-8} width={8} height={10} rx={2} fill="#fef3c7" />
      <ellipse cx={0} cy={-10} rx={12} ry={8} fill="#dc2626" />
      <circle cx={-4} cy={-12} r={2} fill="#fff" />
      <circle cx={5} cy={-10} r={1.6} fill="#fff" />
      <circle cx={0} cy={-14} r={1.8} fill="#fff" />
    </g>
  );
}

function Flower({ x, y, color }: { x: number; y: number; color: string }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x={-0.8} y={-2} width={1.6} height={8} fill="#15803d" />
      <circle cx={-3} cy={-2} r={2.2} fill={color} />
      <circle cx={3} cy={-2} r={2.2} fill={color} />
      <circle cx={0} cy={-5} r={2.2} fill={color} />
      <circle cx={0} cy={1} r={2.2} fill={color} />
      <circle cx={0} cy={-2} r={1.8} fill="#fde047" />
    </g>
  );
}

export default function MapScene({ sky = "day", theme = "meadow", children }: MapSceneProps) {
  const gradient = SKY_GRADIENTS[sky] ?? SKY_GRADIENTS.day;
  const isNight = sky === "night";
  const isForest = theme === "forest";
  const isTown = theme === "town";

  const groundY = (GROUND_Y_PERCENT / 100) * 600;
  const soilY = groundY + 18;

  const cloudCount = isForest ? 2 : 4;
  const clouds = [
    { cx: 140, cy: 90, s: 1 },
    { cx: 420, cy: 60, s: 1.2 },
    { cx: 700, cy: 110, s: 0.9 },
    { cx: 880, cy: 70, s: 1.1 },
  ].slice(0, cloudCount);

  const grassColor = isForest ? "#16a34a" : "#4ade80";
  const grassDark = isForest ? "#15803d" : "#22c55e";
  const soilColor = isForest ? "#422006" : "#78350f";

  const trees: Array<{ x: number; y: number; s: number; dark?: boolean }> = isForest
    ? [
        { x: 60, y: groundY, s: 1.2, dark: true },
        { x: 140, y: groundY, s: 1.0, dark: true },
        { x: 220, y: groundY, s: 1.3, dark: true },
        { x: 310, y: groundY, s: 0.9, dark: true },
        { x: 390, y: groundY, s: 1.1, dark: true },
        { x: 610, y: groundY, s: 1.2, dark: true },
        { x: 720, y: groundY, s: 1.0, dark: true },
        { x: 820, y: groundY, s: 1.3, dark: true },
        { x: 920, y: groundY, s: 1.1, dark: true },
      ]
    : isTown
    ? [
        { x: 90, y: groundY, s: 0.9 },
        { x: 820, y: groundY, s: 1.0 },
      ]
    : [
        { x: 80, y: groundY, s: 1.1 },
        { x: 260, y: groundY, s: 0.9 },
        { x: 780, y: groundY, s: 1.2 },
        { x: 920, y: groundY, s: 1.0 },
      ];

  const houses = isTown
    ? [
        { x: 220, y: groundY, s: 1.0 },
        { x: 380, y: groundY, s: 1.1 },
        { x: 560, y: groundY, s: 0.95 },
        { x: 700, y: groundY, s: 1.05 },
      ]
    : [];

  return (
    <div className="relative w-full h-full overflow-hidden">
      <div className={`absolute inset-0 bg-gradient-to-b ${gradient}`} />

      {isNight && (
        <div className="absolute inset-0 pointer-events-none">
          {STAR_POSITIONS.map((s, i) => (
            <div
              key={i}
              className="absolute w-[3px] h-[3px] rounded-full bg-white animate-pulse"
              style={{ top: s.top, left: s.left, animationDelay: s.delay }}
            />
          ))}
        </div>
      )}

      <svg
        className="absolute inset-0 w-full h-full"
        viewBox="0 0 1000 600"
        preserveAspectRatio="xMidYMid slice"
      >
        <path
          d="M -20 360 L 80 280 L 170 330 L 260 250 L 360 310 L 460 260 L 560 320 L 660 270 L 760 310 L 860 260 L 960 320 L 1020 300 L 1020 420 L -20 420 Z"
          fill="#6366f1"
          opacity={0.45}
        />
        <path
          d="M -20 400 L 60 340 L 150 380 L 240 320 L 340 370 L 440 330 L 540 380 L 640 330 L 740 380 L 840 340 L 940 380 L 1020 360 L 1020 440 L -20 440 Z"
          fill="#818cf8"
          opacity={0.55}
        />

        {clouds.map((c, i) => (
          <g key={i} transform={`translate(${c.cx} ${c.cy}) scale(${c.s})`} opacity={0.95}>
            <ellipse cx={0} cy={0} rx={34} ry={14} fill="#ffffff" />
            <ellipse cx={-22} cy={4} rx={20} ry={11} fill="#ffffff" />
            <ellipse cx={22} cy={4} rx={22} ry={12} fill="#ffffff" />
            <ellipse cx={0} cy={-10} rx={18} ry={10} fill="#ffffff" />
          </g>
        ))}

        <path
          d={`M -20 ${groundY - 30} Q 150 ${groundY - 80} 320 ${groundY - 30} T 640 ${groundY - 30} T 1020 ${groundY - 30} L 1020 ${groundY} L -20 ${groundY} Z`}
          fill={isForest ? "#166534" : "#4ade80"}
          opacity={0.85}
        />

        <rect x={-20} y={groundY} width={1040} height={soilY - groundY} fill={grassColor} />
        <rect x={-20} y={soilY} width={1040} height={600 - soilY + 20} fill={soilColor} />

        <g>
          {Array.from({ length: 40 }).map((_, i) => {
            const gx = i * 26 + 6;
            return (
              <polygon
                key={i}
                points={`${gx - 3},${groundY} ${gx},${groundY - 6} ${gx + 3},${groundY}`}
                fill={grassDark}
              />
            );
          })}
        </g>

        {trees.map((t, i) => (
          <Tree key={`t-${i}`} x={t.x} y={t.y} scale={t.s} dark={t.dark} />
        ))}

        {houses.map((h, i) => (
          <House key={`h-${i}`} x={h.x} y={h.y} scale={h.s} />
        ))}

        <Mushroom x={180} y={groundY + 2} scale={1} />
        <Mushroom x={470} y={groundY + 2} scale={0.8} />
        <Mushroom x={870} y={groundY + 2} scale={1.1} />

        <g transform={`translate(640 ${groundY})`}>
          <rect x={-2} y={-26} width={4} height={26} fill="#7c2d12" />
          <rect x={-16} y={-36} width={32} height={14} fill="#a16207" />
          <rect x={-14} y={-34} width={28} height={10} fill="#fde68a" />
        </g>

        <Flower x={120} y={groundY - 2} color="#f472b6" />
        <Flower x={340} y={groundY - 2} color="#facc15" />
        <Flower x={520} y={groundY - 2} color="#f87171" />
        <Flower x={760} y={groundY - 2} color="#c084fc" />
        <Flower x={960} y={groundY - 2} color="#fb923c" />
      </svg>

      <div className="absolute inset-0 pointer-events-none">{children}</div>
    </div>
  );
}

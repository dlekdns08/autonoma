/**
 * <AchievementBadge /> — pure presentational badge for Feature #12.
 *
 * Stateless. Color and star count are derived from the ``tier`` prop;
 * a hover tooltip is rendered via the native ``title`` attribute so we
 * stay dependency-free.
 */

import type { CSSProperties } from "react";

export type AchievementTier = "bronze" | "silver" | "gold" | string;

export interface AchievementBadgeProps {
  id: string;
  title: string;
  description?: string;
  tier?: AchievementTier;
  size?: "sm" | "md" | "lg";
}

const TIER_COLOR: Record<string, string> = {
  bronze: "#CD7F32",
  silver: "#C0C0C0",
  gold: "#FFD700",
};

const SLATE_FALLBACK = "#64748B"; // tailwind slate-500

const TIER_STARS: Record<string, number> = {
  bronze: 1,
  silver: 2,
  gold: 3,
};

const SIZE_PX: Record<NonNullable<AchievementBadgeProps["size"]>, number> = {
  sm: 40,
  md: 56,
  lg: 80,
};

const STAR_FONT_PX: Record<NonNullable<AchievementBadgeProps["size"]>, number> = {
  sm: 9,
  md: 11,
  lg: 14,
};

const TITLE_FONT_PX: Record<NonNullable<AchievementBadgeProps["size"]>, number> = {
  sm: 9,
  md: 10,
  lg: 12,
};

export default function AchievementBadge({
  id,
  title,
  description,
  tier,
  size = "md",
}: AchievementBadgeProps) {
  const tierKey = (tier ?? "").toLowerCase();
  const color = TIER_COLOR[tierKey] ?? SLATE_FALLBACK;
  const stars = TIER_STARS[tierKey] ?? 1;
  const dim = SIZE_PX[size];
  const starFont = STAR_FONT_PX[size];
  const titleFont = TITLE_FONT_PX[size];

  const tooltip = description ? `${title} — ${description}` : title;

  // Inline styles where we need the dynamic tier color; tailwind handles
  // structural layout so the visual identity stays consistent with the
  // rest of the admin UI.
  const ringStyle: CSSProperties = {
    width: dim,
    height: dim,
    boxShadow: `0 0 0 2px ${color}55, inset 0 0 12px ${color}33`,
    borderColor: color,
    color,
  };

  return (
    <div
      data-achievement-id={id}
      title={tooltip}
      aria-label={tooltip}
      className="group flex flex-col items-center gap-1"
    >
      <div
        className="flex flex-col items-center justify-center rounded-full border bg-slate-900/60 transition-transform duration-150 group-hover:scale-105"
        style={ringStyle}
      >
        <div
          className="font-bold tracking-widest"
          style={{ color, fontSize: starFont, lineHeight: 1 }}
        >
          {"★".repeat(stars)}
        </div>
        <div
          className="mt-0.5 max-w-[90%] truncate px-1 text-center font-mono text-white/85"
          style={{ fontSize: titleFont, lineHeight: 1.1 }}
        >
          {title}
        </div>
      </div>
    </div>
  );
}

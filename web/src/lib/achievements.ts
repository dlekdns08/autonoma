/**
 * Achievements / badges — Feature #12 client helpers.
 *
 * Mirrors backend contracts:
 *   GET /api/agents/{character_uuid}/achievements
 *     -> { character_uuid, count, items: AgentAchievement[] }
 *   GET /api/achievements/recent?limit=20
 *     -> RecentAchievement[]
 *
 * All requests use ``credentials: "include"`` to carry the session cookie.
 */

import { API_BASE_URL } from "@/hooks/useSwarm";

/** Common tier vocabulary used by backend; ``string`` allows future tiers. */
export type AchievementTier = "bronze" | "silver" | "gold" | string;

/** Single row returned by the per-agent achievements endpoint. */
export interface AgentAchievement {
  achievement_id: string;
  title: string;
  description: string;
  tier: AchievementTier;
  xp_reward: number;
  earned_at: string;
  project_uuid: string;
}

/** Wrapper returned by ``GET /api/agents/{uuid}/achievements``. */
export interface AgentAchievementsResponse {
  character_uuid: string;
  count: number;
  items: AgentAchievement[];
}

/** Single row from the global recent-achievements feed. */
export interface RecentAchievement {
  character_name: string;
  species_emoji: string;
  achievement_id: string;
  title: string;
  tier: AchievementTier;
  earned_at: string;
}

/** Fetch all achievements earned by a single character. */
export async function fetchAgentAchievements(
  uuid: string,
): Promise<AgentAchievementsResponse> {
  const res = await fetch(
    `${API_BASE_URL}/api/agents/${encodeURIComponent(uuid)}/achievements`,
    {
      credentials: "include",
      headers: { Accept: "application/json" },
    },
  );
  if (!res.ok) {
    throw new Error(`fetchAgentAchievements: HTTP ${res.status}`);
  }
  return (await res.json()) as AgentAchievementsResponse;
}

/** Fetch the most recently earned achievements across the whole swarm. */
export async function fetchRecentAchievements(
  limit: number = 20,
): Promise<RecentAchievement[]> {
  const qs = `?limit=${encodeURIComponent(String(limit))}`;
  const res = await fetch(`${API_BASE_URL}/api/achievements/recent${qs}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new Error(`fetchRecentAchievements: HTTP ${res.status}`);
  }
  return (await res.json()) as RecentAchievement[];
}

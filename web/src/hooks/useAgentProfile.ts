"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

export interface AgentProfileCharacter {
  uuid: string;
  name: string;
  role: string;
  species: string;
  species_emoji: string;
  catchphrase: string;
  rarity: string;
  level: number;
  total_xp: number;
  runs_survived: number;
  runs_died: number;
  tasks_completed: number;
  files_created: number;
  traits: string[];
  stats: Record<string, number>;
  last_mood: string;
  is_alive: boolean;
  first_seen: string;
  last_seen: string;
}

export interface AgentJournalEntry {
  kind: "diary" | "memory" | "lore" | "note" | string;
  round: number;
  mood: string;
  text: string;
  at: string;
}

export interface AgentRelationship {
  to_uuid: string;
  trust: number;
  familiarity: number;
  sentiment: string;
  last_interaction: string;
}

export interface AgentProfile {
  character: AgentProfileCharacter;
  runs: number;
  journal: AgentJournalEntry[];
  relationships: AgentRelationship[];
}

export interface UseAgentProfile {
  profile: AgentProfile | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  pinNote: (text: string) => Promise<{ ok: boolean; reason?: string }>;
}

export function useAgentProfile(identifier: string): UseAgentProfile {
  const [profile, setProfile] = useState<AgentProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!identifier) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/agents/${encodeURIComponent(identifier)}/profile`,
        { credentials: "include", headers: { Accept: "application/json" } },
      );
      if (res.status === 404) {
        setProfile(null);
        setError("해당 이름의 캐릭터를 찾을 수 없습니다.");
        return;
      }
      if (!res.ok) {
        setError(`HTTP ${res.status}`);
        return;
      }
      const data = (await res.json()) as AgentProfile;
      setProfile(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [identifier]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const pinNote = useCallback(
    async (text: string): Promise<{ ok: boolean; reason?: string }> => {
      if (!profile) return { ok: false, reason: "no_profile" };
      const res = await fetch(
        `${API_BASE_URL}/api/agents/${encodeURIComponent(profile.character.uuid)}/journal/pin`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        },
      );
      if (res.ok) {
        await refresh();
        return { ok: true };
      }
      const body = await res.json().catch(() => ({}));
      const detail = body?.detail;
      const reason =
        detail && typeof detail === "object"
          ? String(detail.message || detail.code || `http_${res.status}`)
          : String(detail || `http_${res.status}`);
      return { ok: false, reason };
    },
    [profile, refresh],
  );

  return { profile, loading, error, refresh, pinNote };
}

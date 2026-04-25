"use client";

/**
 * Phase 3-#3 — cutscene CRUD + playback hook.
 *
 * The composer page uses ``useCutscenes`` to list / create / update /
 * delete cutscenes. Playback fan-out lives on the server (the
 * ``cutscenes`` router emits ``cutscene.step`` bus events); this hook
 * only exposes the catalog + a "Play" trigger.
 */

import { useCallback, useEffect, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

export type CutsceneStepKind = "clip" | "speech" | "sfx" | "delay";

export interface CutsceneStep {
  at_ms: number;
  kind: CutsceneStepKind;
  label: string;
  payload: Record<string, unknown>;
}

export interface CutsceneTrigger {
  kind: "manual" | "project_complete" | "achievement" | "boss_defeated";
  value: string;
}

export interface Cutscene {
  id: string;
  owner_user_id: string;
  name: string;
  description: string;
  steps: CutsceneStep[];
  trigger: CutsceneTrigger;
  created_at: string;
  updated_at: string;
}

export interface UseCutscenes {
  cutscenes: Cutscene[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  create: (draft: Partial<Cutscene>) => Promise<Cutscene | null>;
  update: (id: string, draft: Cutscene) => Promise<Cutscene | null>;
  remove: (id: string) => Promise<boolean>;
  play: (id: string) => Promise<boolean>;
}

async function readJson<T>(res: Response): Promise<T | null> {
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

function formatErr(detail: unknown, status: number): string {
  if (
    detail &&
    typeof detail === "object" &&
    "message" in (detail as Record<string, unknown>)
  ) {
    return String((detail as Record<string, unknown>).message);
  }
  return `HTTP ${status}`;
}

export function useCutscenes(): UseCutscenes {
  const [cutscenes, setCutscenes] = useState<Cutscene[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/cutscenes`, {
        credentials: "include",
      });
      if (!res.ok) {
        setError(`HTTP ${res.status}`);
        return;
      }
      const data = (await res.json()) as { cutscenes: Cutscene[] };
      setCutscenes(data.cutscenes ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const create = useCallback(
    async (draft: Partial<Cutscene>): Promise<Cutscene | null> => {
      const res = await fetch(`${API_BASE_URL}/api/cutscenes`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (!res.ok) {
        const body = await readJson<{ detail?: unknown }>(res);
        setError(formatErr(body?.detail, res.status));
        return null;
      }
      const body = (await res.json()) as { cutscene: Cutscene };
      setCutscenes((prev) => [body.cutscene, ...prev]);
      return body.cutscene;
    },
    [],
  );

  const update = useCallback(
    async (id: string, draft: Cutscene): Promise<Cutscene | null> => {
      const res = await fetch(
        `${API_BASE_URL}/api/cutscenes/${encodeURIComponent(id)}`,
        {
          method: "PUT",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(draft),
        },
      );
      if (!res.ok) {
        const body = await readJson<{ detail?: unknown }>(res);
        setError(formatErr(body?.detail, res.status));
        return null;
      }
      const body = (await res.json()) as { cutscene: Cutscene };
      setCutscenes((prev) =>
        prev.map((c) => (c.id === id ? body.cutscene : c)),
      );
      return body.cutscene;
    },
    [],
  );

  const remove = useCallback(async (id: string): Promise<boolean> => {
    const res = await fetch(
      `${API_BASE_URL}/api/cutscenes/${encodeURIComponent(id)}`,
      { method: "DELETE", credentials: "include" },
    );
    if (!res.ok) {
      const body = await readJson<{ detail?: unknown }>(res);
      setError(formatErr(body?.detail, res.status));
      return false;
    }
    setCutscenes((prev) => prev.filter((c) => c.id !== id));
    return true;
  }, []);

  const play = useCallback(async (id: string): Promise<boolean> => {
    const res = await fetch(
      `${API_BASE_URL}/api/cutscenes/${encodeURIComponent(id)}/play`,
      { method: "POST", credentials: "include" },
    );
    if (!res.ok) {
      const body = await readJson<{ detail?: unknown }>(res);
      setError(formatErr(body?.detail, res.status));
      return false;
    }
    return true;
  }, []);

  return {
    cutscenes,
    loading,
    error,
    refresh,
    create,
    update,
    remove,
    play,
  };
}

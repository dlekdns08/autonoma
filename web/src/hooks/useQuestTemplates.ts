"use client";

/**
 * Thin state holder for the per-user quest template bank.
 *
 * Responsibilities:
 *   1. Fetch the caller's templates on mount and expose a refresh fn.
 *   2. Wrap the save/delete mutations so callers get an optimistic
 *      refresh on success without each consumer re-implementing it.
 *   3. Swallow 401/403 by returning an empty list — anonymous viewers
 *      shouldn't see crashes, just no saved templates.
 *
 * Intentionally NOT a polling hook: templates change only on explicit
 * user action (save / delete), so a one-shot fetch + manual refresh
 * after mutations is plenty.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  deleteTemplate as apiDelete,
  listTemplates as apiList,
  QuestTemplateApiError,
  saveTemplate as apiSave,
  type QuestTemplate,
} from "@/lib/questTemplates";

export interface UseQuestTemplatesResult {
  templates: QuestTemplate[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  save: (text: string) => Promise<QuestTemplate | null>;
  remove: (id: number) => Promise<void>;
}

export function useQuestTemplates(): UseQuestTemplatesResult {
  const [templates, setTemplates] = useState<QuestTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const next = await apiList();
      // ``apiList`` already guards Array.isArray; double-guard here so
      // a future code path that bypasses the coercion can't blow up
      // the panel.
      setTemplates(Array.isArray(next) ? next : []);
      setError(null);
    } catch (err) {
      // 401/403 → caller isn't logged in or lacks active status. We
      // intentionally swallow these so the templates dropdown just
      // looks empty instead of crashing the parent panel.
      if (err instanceof QuestTemplateApiError && (err.status === 401 || err.status === 403)) {
        setTemplates([]);
        setError(null);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return null;
      try {
        const created = await apiSave(trimmed);
        await refresh();
        return created;
      } catch (err) {
        if (err instanceof QuestTemplateApiError && (err.status === 401 || err.status === 403)) {
          // Same policy as refresh: anonymous → no-op.
          return null;
        }
        setError(err instanceof Error ? err.message : String(err));
        return null;
      }
    },
    [refresh],
  );

  const remove = useCallback(
    async (id: number) => {
      try {
        await apiDelete(id);
        // Optimistic drop so the dropdown updates immediately even
        // before the refresh round-trips.
        setTemplates((prev) => prev.filter((t) => t.id !== id));
        await refresh();
      } catch (err) {
        if (err instanceof QuestTemplateApiError && (err.status === 401 || err.status === 403)) {
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [refresh],
  );

  return useMemo(
    () => ({ templates, loading, error, refresh, save, remove }),
    [templates, loading, error, refresh, save, remove],
  );
}

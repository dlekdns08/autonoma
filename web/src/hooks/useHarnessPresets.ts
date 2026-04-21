"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

// ── Harness policy model ──────────────────────────────────────────────
// These types shadow `HarnessPolicyContent` and `HarnessPolicy` on the
// server, but the *shape of fields within each section* is intentionally
// kept open (Record<string, unknown>) — the schema endpoint is the
// source of truth at runtime and the panel renders fields based on it.
// Narrowing the content type here would force every Literal addition in
// Python to be mirrored in TS before the form could show it.

export type HarnessSectionContent = Record<string, unknown>;
export type HarnessContent = Record<string, HarnessSectionContent>;

export interface HarnessPreset {
  id: string;
  owner_user_id: string | null;
  name: string;
  content: HarnessContent;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

// ── Schema (driven by GET /api/harness/schema) ────────────────────────

export type HarnessFieldType = "enum" | "int" | "float" | "bool" | "unknown";

export interface HarnessFieldSpec {
  type: HarnessFieldType;
  default: unknown;
  options?: string[];
  min?: number;
  max?: number;
}

export interface HarnessSchema {
  sections: Record<string, Record<string, HarnessFieldSpec>>;
}

// ── Result discriminated unions ───────────────────────────────────────

export type PresetMutationReason =
  | "invalid"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "unauthorized"
  | "network";

export type CreateResult =
  | { ok: true; preset: HarnessPreset }
  | { ok: false; reason: PresetMutationReason; detail?: unknown };

export type UpdateResult = CreateResult;

export type DeleteResult =
  | { ok: true }
  | { ok: false; reason: PresetMutationReason; detail?: unknown };

// ── Hook return type ──────────────────────────────────────────────────

export interface UseHarnessPresetsReturn {
  presets: HarnessPreset[];
  schema: HarnessSchema | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createPreset: (name: string, content: HarnessContent) => Promise<CreateResult>;
  updatePreset: (
    id: string,
    patch: { name?: string; content?: HarnessContent },
  ) => Promise<UpdateResult>;
  deletePreset: (id: string) => Promise<DeleteResult>;
}

const JSON_HEADERS: HeadersInit = { "Content-Type": "application/json" };

function statusToReason(status: number): PresetMutationReason {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422 || status === 400) return "invalid";
  return "network";
}

/**
 * useHarnessPresets — preset CRUD + schema hydration.
 *
 * On mount it fires two requests in parallel: the schema and the list.
 * The schema doesn't change per session so it's cached in state; callers
 * don't need to re-fetch it after mutations. Mutations refresh the list
 * optimistically via `refresh()` rather than rewriting state in place,
 * since the server controls `id`/`updated_at`/`is_default`.
 */
export function useHarnessPresets(options?: {
  enabled?: boolean;
}): UseHarnessPresetsReturn {
  const enabled = options?.enabled ?? true;

  const [presets, setPresets] = useState<HarnessPreset[]>([]);
  const [schema, setSchema] = useState<HarnessSchema | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [error, setError] = useState<string | null>(null);

  const fetchPresets = useCallback(async (): Promise<HarnessPreset[]> => {
    const res = await fetch(`${API_BASE_URL}/api/harness/presets`, {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (res.status === 401) return [];
    if (!res.ok) throw new Error(`presets status ${res.status}`);
    const data = (await res.json()) as { presets: HarnessPreset[] };
    return data.presets ?? [];
  }, []);

  const fetchSchema = useCallback(async (): Promise<HarnessSchema | null> => {
    const res = await fetch(`${API_BASE_URL}/api/harness/schema`, {
      method: "GET",
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (res.status === 401) return null;
    if (!res.ok) throw new Error(`schema status ${res.status}`);
    return (await res.json()) as HarnessSchema;
  }, []);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setError(null);
    try {
      const [nextSchema, nextPresets] = await Promise.all([
        // Only refetch schema the first time — it's static per server
        // build, so later refreshes skip it.
        schema ? Promise.resolve(schema) : fetchSchema(),
        fetchPresets(),
      ]);
      setSchema(nextSchema);
      setPresets(nextPresets);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [enabled, fetchPresets, fetchSchema, schema]);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    // `refresh` depends on `schema` but we intentionally only want the
    // mount-time hydrate here; downstream updates go through explicit
    // refresh() calls from mutation handlers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  const createPreset = useCallback(
    async (name: string, content: HarnessContent): Promise<CreateResult> => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/harness/presets`, {
          method: "POST",
          credentials: "include",
          headers: JSON_HEADERS,
          body: JSON.stringify({ name, content }),
        });
        if (res.status === 201) {
          const preset = (await res.json()) as HarnessPreset;
          setPresets((prev) => [...prev, preset]);
          return { ok: true, preset };
        }
        const detail = await res.json().catch(() => undefined);
        return { ok: false, reason: statusToReason(res.status), detail };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  const updatePreset = useCallback(
    async (
      id: string,
      patch: { name?: string; content?: HarnessContent },
    ): Promise<UpdateResult> => {
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/harness/presets/${encodeURIComponent(id)}`,
          {
            method: "PUT",
            credentials: "include",
            headers: JSON_HEADERS,
            body: JSON.stringify(patch),
          },
        );
        if (res.status === 200) {
          const preset = (await res.json()) as HarnessPreset;
          setPresets((prev) =>
            prev.map((p) => (p.id === preset.id ? preset : p)),
          );
          return { ok: true, preset };
        }
        const detail = await res.json().catch(() => undefined);
        return { ok: false, reason: statusToReason(res.status), detail };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  const deletePreset = useCallback(
    async (id: string): Promise<DeleteResult> => {
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/harness/presets/${encodeURIComponent(id)}`,
          { method: "DELETE", credentials: "include" },
        );
        if (res.status === 204) {
          setPresets((prev) => prev.filter((p) => p.id !== id));
          return { ok: true };
        }
        const detail = await res.json().catch(() => undefined);
        return { ok: false, reason: statusToReason(res.status), detail };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  return {
    presets,
    schema,
    loading,
    error,
    refresh,
    createPreset,
    updatePreset,
    deletePreset,
  };
}

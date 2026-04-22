"use client";

/**
 * ``useMocapBindings`` — the global (vrm_file, trigger_kind, trigger_value)
 * → clip_id lookup table. Bindings are shared across every viewer, so
 * this hook is the single source of truth for the binding editor, for
 * the dashboard VRMCharacter's per-frame lookup, and for the ``/mocap``
 * page's library browser.
 *
 * Refresh strategy:
 *   - On mount: one-shot GET so the UI has data immediately.
 *   - On ``refreshToken`` bump: re-fetch. Parent passes a counter that
 *     increments whenever a ``mocap.bindings.updated`` event arrives
 *     via the WS bridge (see ``useSwarm``).
 *   - On upsert/remove: optimistic local write plus a background GET to
 *     reconcile against any concurrent edits from another viewer.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";
import type { BindingRow } from "@/lib/mocap/clipFormat";
import type { TriggerKind } from "@/lib/mocap/triggers";

export interface BindingKey {
  vrmFile: string;
  kind: TriggerKind;
  value: string;
}

export type UpsertResult =
  | { ok: true; binding: BindingRow }
  | { ok: false; reason: string };

export interface UseMocapBindings {
  bindings: BindingRow[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  /** O(1) lookup used by the render loop. */
  lookup: (key: BindingKey) => BindingRow | undefined;
  /** All bindings for one .vrm file, grouped by kind. */
  forVrm: (vrmFile: string) => {
    mood: BindingRow[];
    emote: BindingRow[];
    state: BindingRow[];
    manual: BindingRow[];
  };
  upsert: (key: BindingKey, clipId: string) => Promise<UpsertResult>;
  remove: (key: BindingKey) => Promise<boolean>;
}

const JSON_HEADERS: HeadersInit = {
  "Content-Type": "application/json",
  Accept: "application/json",
};

function bindingIndex(row: BindingRow): string {
  return `${row.vrm_file}|${row.trigger_kind}|${row.trigger_value}`;
}

function keyIndex(k: BindingKey): string {
  return `${k.vrmFile}|${k.kind}|${k.value}`;
}

export function useMocapBindings(refreshToken: number = 0): UseMocapBindings {
  const [bindings, setBindings] = useState<BindingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqSeqRef = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++reqSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/mocap-bindings`, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { bindings: BindingRow[] };
      if (seq !== reqSeqRef.current) return;
      setBindings(data.bindings ?? []);
    } catch (err) {
      if (seq !== reqSeqRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (seq === reqSeqRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, refreshToken]);

  // Indexed by "vrm|kind|value" so the dashboard's per-frame lookup is
  // O(1). useMemo rebuilds the map only when the list changes.
  const index = useMemo(() => {
    const m = new Map<string, BindingRow>();
    for (const b of bindings) m.set(bindingIndex(b), b);
    return m;
  }, [bindings]);

  const lookup = useCallback(
    (key: BindingKey) => index.get(keyIndex(key)),
    [index],
  );

  const forVrm = useCallback(
    (vrmFile: string) => {
      const out = {
        mood: [] as BindingRow[],
        emote: [] as BindingRow[],
        state: [] as BindingRow[],
        manual: [] as BindingRow[],
      };
      for (const b of bindings) {
        if (b.vrm_file !== vrmFile) continue;
        (out[b.trigger_kind as TriggerKind] ??= []).push(b);
      }
      return out;
    },
    [bindings],
  );

  const upsert = useCallback(
    async (key: BindingKey, clipId: string): Promise<UpsertResult> => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/mocap-bindings`, {
          method: "PUT",
          credentials: "include",
          headers: JSON_HEADERS,
          body: JSON.stringify({
            vrm_file: key.vrmFile,
            trigger_kind: key.kind,
            trigger_value: key.value,
            clip_id: clipId,
          }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          const reason = typeof body?.detail === "string" ? body.detail : `http_${res.status}`;
          return { ok: false, reason };
        }
        const data = (await res.json()) as { binding: BindingRow };
        setBindings((prev) => {
          const ix = bindingIndex(data.binding);
          return [data.binding, ...prev.filter((b) => bindingIndex(b) !== ix)];
        });
        return { ok: true, binding: data.binding };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  const remove = useCallback(async (key: BindingKey): Promise<boolean> => {
    const url = new URL(`${API_BASE_URL || ""}/api/mocap-bindings`, window.location.origin);
    url.searchParams.set("vrm_file", key.vrmFile);
    url.searchParams.set("trigger_kind", key.kind);
    url.searchParams.set("trigger_value", key.value);
    const res = await fetch(url.toString(), {
      method: "DELETE",
      credentials: "include",
    });
    if (res.status !== 204) return false;
    const ix = keyIndex(key);
    setBindings((prev) => prev.filter((b) => bindingIndex(b) !== ix));
    return true;
  }, []);

  return { bindings, loading, error, refresh, lookup, forVrm, upsert, remove };
}

"use client";

/**
 * ``useVoiceBindings`` — global vrm_file → voice profile lookup. Mirrors
 * ``useMocapBindings`` but with a single-column PK because a VRM has one
 * voice, not a multi-trigger table.
 *
 * Refresh strategy is the same three-tier model:
 *   - One-shot GET on mount so the UI has data immediately.
 *   - Full GET on ``refreshToken`` bump (WS reconnect, dropped events).
 *   - Row-level patch on ``remoteEvent`` tick — peers' edits skip the
 *     round-trip.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL, type VoiceBindingEvent } from "@/hooks/useSwarm";

export interface VoiceBindingRow {
  vrm_file: string;
  profile_id: string;
  updated_by: string | null;
  updated_at: string;
}

export type VoiceUpsertResult =
  | { ok: true; binding: VoiceBindingRow }
  | { ok: false; reason: string };

export interface UseVoiceBindings {
  bindings: VoiceBindingRow[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  /** O(1) lookup by vrm_file. */
  lookup: (vrmFile: string) => VoiceBindingRow | undefined;
  upsert: (vrmFile: string, profileId: string) => Promise<VoiceUpsertResult>;
  remove: (vrmFile: string) => Promise<boolean>;
}

const JSON_HEADERS: HeadersInit = {
  "Content-Type": "application/json",
  Accept: "application/json",
};

export function useVoiceBindings(
  refreshToken: number = 0,
  remoteEvent: VoiceBindingEvent | null = null,
): UseVoiceBindings {
  const [bindings, setBindings] = useState<VoiceBindingRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqSeqRef = useRef(0);
  const lastAppliedEventSeqRef = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++reqSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/voice-bindings`, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { bindings: VoiceBindingRow[] };
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

  useEffect(() => {
    if (!remoteEvent) return;
    if (remoteEvent.seq <= lastAppliedEventSeqRef.current) return;
    lastAppliedEventSeqRef.current = remoteEvent.seq;
    setBindings((prev) => {
      const without = prev.filter((b) => b.vrm_file !== remoteEvent.vrm_file);
      if (remoteEvent.removed || !remoteEvent.profile_id) return without;
      const patched: VoiceBindingRow = {
        vrm_file: remoteEvent.vrm_file,
        profile_id: remoteEvent.profile_id,
        updated_at: new Date().toISOString(),
        updated_by: null,
      };
      return [patched, ...without];
    });
  }, [remoteEvent]);

  const index = useMemo(() => {
    const m = new Map<string, VoiceBindingRow>();
    for (const b of bindings) m.set(b.vrm_file, b);
    return m;
  }, [bindings]);

  const lookup = useCallback(
    (vrmFile: string) => index.get(vrmFile),
    [index],
  );

  const upsert = useCallback(
    async (vrmFile: string, profileId: string): Promise<VoiceUpsertResult> => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/voice-bindings`, {
          method: "PUT",
          credentials: "include",
          headers: JSON_HEADERS,
          body: JSON.stringify({ vrm_file: vrmFile, profile_id: profileId }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          const reason =
            typeof body?.detail === "string" ? body.detail : `http_${res.status}`;
          return { ok: false, reason };
        }
        const data = (await res.json()) as { binding: VoiceBindingRow };
        setBindings((prev) => {
          const without = prev.filter((b) => b.vrm_file !== data.binding.vrm_file);
          return [data.binding, ...without];
        });
        return { ok: true, binding: data.binding };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  const remove = useCallback(async (vrmFile: string): Promise<boolean> => {
    const url = new URL(
      `${API_BASE_URL || ""}/api/voice-bindings`,
      window.location.origin,
    );
    url.searchParams.set("vrm_file", vrmFile);
    const res = await fetch(url.toString(), {
      method: "DELETE",
      credentials: "include",
    });
    if (res.status !== 204) return false;
    setBindings((prev) => prev.filter((b) => b.vrm_file !== vrmFile));
    return true;
  }, []);

  return { bindings, loading, error, refresh, lookup, upsert, remove };
}

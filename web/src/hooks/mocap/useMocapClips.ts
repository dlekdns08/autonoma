"use client";

/**
 * ``useMocapClips`` — CRUD façade for ``/api/mocap-clips``.
 *
 * Returns the list of clips the active user can see (own clips + clips
 * referenced by any global binding) alongside upload / rename / delete
 * helpers. Consumers pass a refresh token (e.g. a monotonic counter
 * bumped on ``mocap.bindings.updated``) so the list re-fetches when a
 * clip's visibility could have changed without a direct mutation from
 * this hook.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";
import type { ClipSummary } from "@/lib/mocap/clipFormat";

export interface UploadClipInput {
  name: string;
  sourceVrm: string;
  /** Gzipped, base64-encoded MocapClip payload. */
  payloadGzB64: string;
  /** Raw (pre-gzip) JSON size — server cross-checks this. */
  expectedSizeBytes: number;
}

export type UploadResult =
  | { ok: true; clip: ClipSummary }
  | { ok: false; reason: string };

export interface UseMocapClips {
  clips: ClipSummary[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  upload: (input: UploadClipInput) => Promise<UploadResult>;
  rename: (clipId: string, name: string) => Promise<boolean>;
  remove: (clipId: string) => Promise<boolean>;
}

const JSON_HEADERS: HeadersInit = {
  "Content-Type": "application/json",
  Accept: "application/json",
};

export function useMocapClips(refreshToken: number = 0): UseMocapClips {
  const [clips, setClips] = useState<ClipSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Latest-wins guard — if the user fires two refreshes in quick
  // succession the stale response shouldn't overwrite the fresh one.
  const reqSeqRef = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++reqSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/mocap-clips`, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { clips: ClipSummary[] };
      if (seq !== reqSeqRef.current) return;
      setClips(data.clips ?? []);
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

  const upload = useCallback(
    async (input: UploadClipInput): Promise<UploadResult> => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/mocap-clips`, {
          method: "POST",
          credentials: "include",
          headers: JSON_HEADERS,
          body: JSON.stringify({
            name: input.name,
            source_vrm: input.sourceVrm,
            payload_gz_b64: input.payloadGzB64,
            expected_size_bytes: input.expectedSizeBytes,
          }),
        });
        if (res.status === 201) {
          const data = (await res.json()) as { clip: ClipSummary };
          setClips((prev) => [data.clip, ...prev.filter((c) => c.id !== data.clip.id)]);
          return { ok: true, clip: data.clip };
        }
        const body = await res.json().catch(() => ({}));
        const reason = typeof body?.detail === "string" ? body.detail : `http_${res.status}`;
        return { ok: false, reason };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  const rename = useCallback(
    async (clipId: string, name: string): Promise<boolean> => {
      const res = await fetch(
        `${API_BASE_URL}/api/mocap-clips/${encodeURIComponent(clipId)}`,
        {
          method: "PATCH",
          credentials: "include",
          headers: JSON_HEADERS,
          body: JSON.stringify({ name }),
        },
      );
      if (!res.ok) return false;
      const data = (await res.json()) as { clip: ClipSummary };
      setClips((prev) => prev.map((c) => (c.id === clipId ? data.clip : c)));
      return true;
    },
    [],
  );

  const remove = useCallback(async (clipId: string): Promise<boolean> => {
    const res = await fetch(
      `${API_BASE_URL}/api/mocap-clips/${encodeURIComponent(clipId)}`,
      { method: "DELETE", credentials: "include" },
    );
    if (res.status !== 204) return false;
    setClips((prev) => prev.filter((c) => c.id !== clipId));
    return true;
  }, []);

  return { clips, loading, error, refresh, upload, rename, remove };
}

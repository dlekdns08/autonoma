"use client";

/**
 * ``useVoiceProfiles`` — listing + CRUD for OmniVoice reference profiles.
 * Profiles are metadata-only in the list payload (raw WAV bytes are
 * served lazily via ``/api/voice-profiles/{id}/audio``).
 *
 * Unlike bindings, profiles have no WS broadcast yet: uploads/deletes
 * echo through local list mutation, and any peer's change shows up on
 * the next refresh. This is fine because profile churn is rare and the
 * admin is already sitting on the page when they upload.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

export interface VoiceProfileSummary {
  id: string;
  owner_user_id: string;
  name: string;
  ref_text: string;
  ref_audio_mime: string;
  duration_s: number;
  size_bytes: number;
  created_at: string;
  updated_at: string;
}

export type VoiceCreateResult =
  | { ok: true; profile: VoiceProfileSummary }
  | { ok: false; reason: string };

export interface UseVoiceProfiles {
  profiles: VoiceProfileSummary[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  create: (args: {
    name: string;
    refText: string;
    refAudio: File;
  }) => Promise<VoiceCreateResult>;
  remove: (id: string) => Promise<{ ok: boolean; reason?: string }>;
}

export function useVoiceProfiles(): UseVoiceProfiles {
  const [profiles, setProfiles] = useState<VoiceProfileSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqSeqRef = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++reqSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/voice-profiles`, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as { profiles: VoiceProfileSummary[] };
      if (seq !== reqSeqRef.current) return;
      setProfiles(data.profiles ?? []);
    } catch (err) {
      if (seq !== reqSeqRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (seq === reqSeqRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const create = useCallback(
    async ({
      name,
      refText,
      refAudio,
    }: {
      name: string;
      refText: string;
      refAudio: File;
    }): Promise<VoiceCreateResult> => {
      try {
        const fd = new FormData();
        fd.append("name", name);
        fd.append("ref_text", refText);
        fd.append("ref_audio", refAudio);
        const res = await fetch(`${API_BASE_URL}/api/voice-profiles`, {
          method: "POST",
          credentials: "include",
          body: fd,
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          const reason =
            typeof body?.detail === "string" ? body.detail : `http_${res.status}`;
          return { ok: false, reason };
        }
        const data = (await res.json()) as { profile: VoiceProfileSummary };
        setProfiles((prev) => [data.profile, ...prev]);
        return { ok: true, profile: data.profile };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  const remove = useCallback(
    async (id: string): Promise<{ ok: boolean; reason?: string }> => {
      const res = await fetch(
        `${API_BASE_URL}/api/voice-profiles/${encodeURIComponent(id)}`,
        { method: "DELETE", credentials: "include" },
      );
      if (res.status === 204) {
        setProfiles((prev) => prev.filter((p) => p.id !== id));
        return { ok: true };
      }
      const body = await res.json().catch(() => ({}));
      const reason =
        typeof body?.detail === "string" ? body.detail : `http_${res.status}`;
      return { ok: false, reason };
    },
    [],
  );

  return { profiles, loading, error, refresh, create, remove };
}

export function voiceProfileAudioUrl(id: string): string {
  return `${API_BASE_URL}/api/voice-profiles/${encodeURIComponent(id)}/audio`;
}

export async function testVoiceProfile(args: {
  id: string;
  text: string;
}): Promise<{ ok: true; blob: Blob } | { ok: false; reason: string }> {
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/voice-profiles/${encodeURIComponent(args.id)}/test`,
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Accept: "audio/wav",
        },
        body: JSON.stringify({ text: args.text }),
      },
    );
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const reason =
        typeof body?.detail === "string" ? body.detail : `http_${res.status}`;
      return { ok: false, reason };
    }
    const blob = await res.blob();
    return { ok: true, blob };
  } catch {
    return { ok: false, reason: "network" };
  }
}

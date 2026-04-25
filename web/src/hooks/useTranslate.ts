"use client";

import { useCallback, useRef } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

/**
 * Phase 4-B — translate-on-demand hook.
 *
 * Caches results per-(text, from, to) so the subtitle overlay doesn't
 * thrash the backend when the same line repeats. The cache lives for
 * the lifetime of the hook instance — refreshing the page rebuilds it
 * from scratch, which is fine for streaming use cases.
 */

export interface UseTranslate {
  translate: (
    text: string,
    fromLang: string,
    toLang: string,
  ) => Promise<string>;
}

interface CacheEntry {
  text: string;
  promise: Promise<string>;
}

export function useTranslate(): UseTranslate {
  const cacheRef = useRef<Map<string, CacheEntry>>(new Map());

  const translate = useCallback(
    async (text: string, fromLang: string, toLang: string): Promise<string> => {
      const trimmed = text.trim();
      if (!trimmed) return "";
      if (fromLang === toLang) return text;
      const key = `${fromLang}|${toLang}|${trimmed}`;
      const hit = cacheRef.current.get(key);
      if (hit) return hit.promise;
      const promise = (async () => {
        try {
          const res = await fetch(`${API_BASE_URL}/api/translate`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              text,
              from_lang: fromLang,
              to_lang: toLang,
            }),
          });
          if (!res.ok) {
            throw new Error(`HTTP ${res.status}`);
          }
          const body = (await res.json()) as { text: string };
          return body.text;
        } catch (err) {
          // Fall back to the original text rather than crashing the
          // overlay — a missing translation is better than no caption.
          // eslint-disable-next-line no-console
          console.warn("[translate] failed", err);
          return text;
        }
      })();
      cacheRef.current.set(key, { text, promise });
      return promise;
    },
    [],
  );

  return { translate };
}

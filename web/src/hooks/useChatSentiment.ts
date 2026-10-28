"use client";

/**
 * Browser-side viewer-chat sentiment ("chat mood"), opt-in.
 *
 * Nothing is downloaded until the viewer flips the toggle: the
 * ``@huggingface/transformers`` runtime (and ONNX weights) are pulled in
 * through a dynamic import inside ``loadClassifier``, which only runs
 * once ``enabled`` is true. The preference persists in localStorage
 * (guarded — private mode / blocked storage just means "off").
 *
 * Device: WebGPU when ``navigator.gpu`` exists, otherwise WASM. The
 * model is a small SST-2 DistilBERT by default; override with
 * ``NEXT_PUBLIC_CHAT_MOOD_MODEL`` (any text-classification model on the
 * Hub with ONNX weights, e.g. a multilingual one for non-English chat).
 *
 * Consumers (``useSwarm``) call ``classify(text)`` per incoming viewer
 * line and map the result onto the agent emote plumbing.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type ChatSentiment = "positive" | "negative" | "neutral";
export type ChatMoodStatus = "off" | "loading" | "ready" | "error";

export const CHAT_MOOD_STORAGE_KEY = "autonoma_chat_mood";
export const CHAT_MOOD_MODEL =
  process.env.NEXT_PUBLIC_CHAT_MOOD_MODEL ||
  "Xenova/distilbert-base-uncased-finetuned-sst-2-english";

/** Emote icon per sentiment. VRMCharacter maps these to gestures
 *  (💖 → hype, 💔 → headshake, 💬 → nod); the pixel stage shows the
 *  icon above the sprite as with every other emote. */
export const CHAT_MOOD_EMOTE: Record<ChatSentiment, string> = {
  positive: "💖",
  negative: "💔",
  neutral: "💬",
};

/** What the UI needs to render a toggle. */
export interface ChatMoodControl {
  enabled: boolean;
  setEnabled: (on: boolean) => void;
  status: ChatMoodStatus;
  /** 0..100 while loading. */
  progress: number;
  error: string | null;
}

export function readChatMoodPref(): boolean {
  try {
    return typeof window !== "undefined" && window.localStorage.getItem(CHAT_MOOD_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeChatMoodPref(on: boolean): void {
  try {
    if (on) window.localStorage.setItem(CHAT_MOOD_STORAGE_KEY, "1");
    else window.localStorage.removeItem(CHAT_MOOD_STORAGE_KEY);
  } catch {
    /* storage blocked — the toggle still works for this session */
  }
}

/** Map a classifier label (SST-2 ``POSITIVE``/``NEGATIVE``, 3-way
 *  ``positive``/``neutral``/``negative``, or star ratings ``1 star``…
 *  ``5 stars``) to our three buckets. Low-confidence calls are neutral. */
export function labelToSentiment(label: string, score: number): ChatSentiment {
  if (!(score >= 0.6)) return "neutral";
  const l = label.toUpperCase();
  if (l.includes("POS") || /^[45]\b/.test(l)) return "positive";
  if (l.includes("NEG") || /^[12]\b/.test(l)) return "negative";
  return "neutral";
}

/** Which agent a chat line is talking to: an ``@name`` mention first,
 *  then any agent name used as a word, else the provided fallback (the
 *  agent currently on stage). Case-insensitive; returns the canonical
 *  name from ``agentNames``. */
export function resolveAddressedAgent(
  text: string,
  agentNames: readonly string[],
  fallback: string | null,
): string | null {
  if (agentNames.length === 0) return null;
  const lower = text.toLowerCase();
  const byLower = new Map(agentNames.map((n) => [n.toLowerCase(), n] as const));
  const mention = /@([\p{L}\p{N}_-]+)/u.exec(text);
  if (mention) {
    const hit = byLower.get(mention[1].toLowerCase());
    if (hit) return hit;
  }
  for (const [ln, name] of byLower) {
    if (ln.length < 3) continue;
    const idx = lower.indexOf(ln);
    if (idx === -1) continue;
    const before = idx === 0 ? "" : lower[idx - 1];
    const after = lower[idx + ln.length] ?? "";
    const boundary = (c: string) => c === "" || !/[\p{L}\p{N}_]/u.test(c);
    if (boundary(before) && boundary(after)) return name;
  }
  return fallback && agentNames.includes(fallback) ? fallback : null;
}

type ClassifierOutput = Array<{ label: string; score: number }> | { label: string; score: number };
type Classifier = (text: string, opts?: { top_k?: number | null }) => Promise<ClassifierOutput>;
type ProgressEvent = { status?: string; progress?: number; file?: string };

let classifierPromise: Promise<Classifier> | null = null;

/** Lazily build the text-classification pipeline (module-level so the
 *  download happens once per page even if the toggle flips). */
function loadClassifier(onProgress?: (pct: number) => void): Promise<Classifier> {
  if (!classifierPromise) {
    classifierPromise = (async () => {
      const tf = await import("@huggingface/transformers");
      // Hub only — there is no local model directory to probe.
      tf.env.allowLocalModels = false;
      const device =
        typeof navigator !== "undefined" && "gpu" in navigator ? "webgpu" : "wasm";
      const pipe = await tf.pipeline("sentiment-analysis", CHAT_MOOD_MODEL, {
        device,
        // Quantised weights on WASM keep the download small; WebGPU
        // runs the fp32 graph (q8 kernels are not universally supported).
        dtype: device === "webgpu" ? "fp32" : "q8",
        progress_callback: (ev: ProgressEvent) => {
          if (ev.status === "progress" && typeof ev.progress === "number") {
            onProgress?.(Math.max(0, Math.min(100, ev.progress)));
          }
        },
      });
      return pipe as unknown as Classifier;
    })().catch((err: unknown) => {
      classifierPromise = null;
      throw err;
    });
  }
  return classifierPromise;
}

export interface UseChatSentiment extends ChatMoodControl {
  /** Classify one chat line. Resolves ``null`` when disabled or on error. */
  classify: (text: string) => Promise<ChatSentiment | null>;
}

export function useChatSentiment(): UseChatSentiment {
  // Start "off" on both server and client, then adopt the stored
  // preference after mount so hydration markup matches.
  const [enabled, setEnabledState] = useState(false);
  const [status, setStatus] = useState<ChatMoodStatus>("off");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const enabledRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (!cancelled && readChatMoodPref()) setEnabledState(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const setEnabled = useCallback((on: boolean) => {
    setEnabledState(on);
    writeChatMoodPref(on);
  }, []);

  // Load (once) when enabled; report status. The classifier itself is
  // kept after a toggle-off so re-enabling is instant.
  useEffect(() => {
    enabledRef.current = enabled;
    let cancelled = false;
    void Promise.resolve().then(async () => {
      if (cancelled) return;
      if (!enabled) {
        setStatus("off");
        return;
      }
      setStatus("loading");
      setError(null);
      try {
        await loadClassifier((pct) => {
          if (!cancelled) setProgress(pct);
        });
        if (!cancelled) setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setError(err instanceof Error ? err.message : String(err));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  const classify = useCallback(async (text: string): Promise<ChatSentiment | null> => {
    if (!enabledRef.current) return null;
    try {
      const run = await loadClassifier();
      if (!enabledRef.current) return null;
      const out = await run(text, { top_k: 1 });
      const first = Array.isArray(out) ? out[0] : out;
      if (!first) return null;
      return labelToSentiment(first.label, first.score);
    } catch {
      return null;
    }
  }, []);

  return { enabled, setEnabled, status, progress, error, classify };
}

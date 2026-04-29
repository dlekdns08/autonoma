"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

/**
 * Push-to-talk recorder backed by the server-side Cohere ASR endpoint.
 *
 * Two modes:
 *   - ``batch`` (default, original): MediaRecorder → blob → POST
 *     ``/api/voice/command`` once on stop. Simple, one round-trip.
 *   - ``stream``: open WS to ``/api/voice/stream``, push audio chunks
 *     every 500ms, receive ``partial`` transcripts every ~1.5s while
 *     the user holds the button, and a ``final`` event on stop. The
 *     partials are surfaced via ``onPartial`` so the UI can show a
 *     live-caption bubble — perceived latency drops from
 *     "blocked until release" to "see your words appear as you speak".
 *
 * Cohere is encoder-decoder + ``model.generate``: there is no token
 * stream. The "streaming" here is rolling chunk transcription — every
 * tick the server re-transcribes the cumulative audio buffer.
 */

export interface PushToTalkResult {
  text: string;
  language: string | null;
  durationMs: number;
  route: { action: string; detail: string };
  rawAudioBytes: number;
}

export type PushToTalkMode = "batch" | "stream";

export interface UsePushToTalkOptions {
  /** Capture pipeline. Default ``batch`` keeps the original behaviour. */
  mode?: PushToTalkMode;
  /** Optional per-call agent target. Empty/undefined → routed to Director. */
  target?: string;
  /** Language hint passed to the ASR processor. Empty → server default. */
  language?: string;
  /** Notified whenever a transcription completes successfully. */
  onResult?: (result: PushToTalkResult) => void;
  /** Notified whenever an error happens at any stage. */
  onError?: (err: string) => void;
  /** Stream mode only — fires every time the server pushes a partial
   *  transcript. Ignored in batch mode. */
  onPartial?: (text: string) => void;
  /** Stream mode only — when ``false``, the server skips the
   *  ExternalInputRouter step and just returns the transcript. Default
   *  true to preserve existing behaviour for the dashboard mic. */
  route?: boolean;
  /** Barge-in (feature #2). Fired the instant ``start()`` is called so
   *  the caller can pause any in-flight TTS playback before the user's
   *  speech overlaps with it. Runs synchronously — keep the handler
   *  cheap (no awaits). */
  onInterrupt?: () => void;
}

export interface UsePushToTalk {
  recording: boolean;
  uploading: boolean;
  error: string | null;
  lastResult: PushToTalkResult | null;
  /** Latest partial transcript emitted while the user is recording. Empty
   *  string when not recording. Only populated in stream mode. */
  partialText: string;
  /** Begin capturing audio. Resolves when the recorder is actually live. */
  start: () => Promise<void>;
  /** Stop capturing and post the blob. The promise resolves with the
   *  result (or null on error). */
  stop: () => Promise<PushToTalkResult | null>;
  /** True only if the browser supports the MediaRecorder API. */
  supported: boolean;
}

function pickMimeType(): string | undefined {
  if (typeof window === "undefined") return undefined;
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  for (const m of candidates) {
    if (
      typeof MediaRecorder !== "undefined" &&
      MediaRecorder.isTypeSupported(m)
    ) {
      return m;
    }
  }
  return undefined;
}

// Derive the WS URL for /api/voice/stream from API_BASE_URL. We can't
// reuse useSwarm's getWsUrl() because that returns the swarm WS path;
// here we want the same host/scheme but the streaming voice path.
function getVoiceStreamUrl(): string {
  // API_BASE_URL is "" (same-origin), "http(s)://host", or unset on SSR.
  if (typeof window === "undefined") return "";
  let base = API_BASE_URL;
  if (!base) base = window.location.origin;
  // http → ws, https → wss
  const wsBase = base.replace(/^http/i, "ws");
  return `${wsBase}/api/voice/stream`;
}

export function usePushToTalk(options: UsePushToTalkOptions = {}): UsePushToTalk {
  const [recording, setRecording] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<PushToTalkResult | null>(null);
  const [partialText, setPartialText] = useState("");

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const stopResolverRef = useRef<((blob: Blob) => void) | null>(null);
  const optsRef = useRef(options);
  optsRef.current = options;

  // Stream-mode WS state. Held in refs so callbacks see the live socket
  // without re-running the start callback every time identity flips.
  const wsRef = useRef<WebSocket | null>(null);
  const wsReadyRef = useRef(false);
  const finalResolverRef = useRef<((r: PushToTalkResult | null) => void) | null>(null);
  const audioByteCountRef = useRef(0);

  const supported =
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    !!window.navigator?.mediaDevices?.getUserMedia;

  // Tear down the active stream + recorder + WS. Idempotent.
  const cleanup = useCallback(() => {
    const rec = recorderRef.current;
    if (rec && rec.state !== "inactive") {
      try {
        rec.stop();
      } catch {
        /* already stopped */
      }
    }
    recorderRef.current = null;
    const stream = streamRef.current;
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
    }
    streamRef.current = null;
    chunksRef.current = [];
    const ws = wsRef.current;
    if (ws && ws.readyState !== WebSocket.CLOSED) {
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    }
    wsRef.current = null;
    wsReadyRef.current = false;
    audioByteCountRef.current = 0;
  }, []);

  useEffect(() => () => cleanup(), [cleanup]);

  // ── Batch mode ────────────────────────────────────────────────────
  const startBatch = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = pickMimeType();
      const rec = mime
        ? new MediaRecorder(stream, { mimeType: mime })
        : new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      rec.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: mime || "audio/webm",
        });
        chunksRef.current = [];
        const stream = streamRef.current;
        streamRef.current = null;
        if (stream) stream.getTracks().forEach((t) => t.stop());
        const resolver = stopResolverRef.current;
        stopResolverRef.current = null;
        resolver?.(blob);
      };
      recorderRef.current = rec;
      rec.start(/* timeslice */);
      setRecording(true);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      optsRef.current.onError?.(msg);
      cleanup();
      setRecording(false);
    }
  }, [cleanup]);

  const stopBatch = useCallback(async (): Promise<PushToTalkResult | null> => {
    const rec = recorderRef.current;
    if (!rec || rec.state === "inactive") {
      setRecording(false);
      return null;
    }
    setRecording(false);
    setUploading(true);

    const blob = await new Promise<Blob>((resolve) => {
      stopResolverRef.current = resolve;
      try {
        rec.stop();
      } catch {
        resolve(new Blob(chunksRef.current, { type: "audio/webm" }));
      }
    });
    recorderRef.current = null;

    if (blob.size === 0) {
      setUploading(false);
      const msg = "녹음된 오디오가 없습니다.";
      setError(msg);
      optsRef.current.onError?.(msg);
      return null;
    }

    const form = new FormData();
    form.append("audio", blob, "command.webm");
    if (optsRef.current.target) form.append("target", optsRef.current.target);
    if (optsRef.current.language) form.append("language", optsRef.current.language);

    try {
      const res = await fetch(`${API_BASE_URL}/api/voice/command`, {
        method: "POST",
        credentials: "include",
        body: form,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const msg =
          (detail?.detail?.message as string | undefined) ??
          `HTTP ${res.status}`;
        setError(msg);
        optsRef.current.onError?.(msg);
        return null;
      }
      const data = (await res.json()) as {
        transcript: { text: string; language: string | null; duration_ms: number };
        route: { action: string; detail: string };
      };
      const result: PushToTalkResult = {
        text: data.transcript.text,
        language: data.transcript.language,
        durationMs: data.transcript.duration_ms,
        route: data.route,
        rawAudioBytes: blob.size,
      };
      setLastResult(result);
      optsRef.current.onResult?.(result);
      return result;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      optsRef.current.onError?.(msg);
      return null;
    } finally {
      setUploading(false);
    }
  }, []);

  // ── Stream mode ───────────────────────────────────────────────────
  const startStream = useCallback(async () => {
    setError(null);
    setPartialText("");
    audioByteCountRef.current = 0;

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      optsRef.current.onError?.(msg);
      return;
    }
    streamRef.current = stream;

    const wsUrl = getVoiceStreamUrl();
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      optsRef.current.onError?.(msg);
      cleanup();
      return;
    }
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    const mime = pickMimeType();

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          type: "start",
          language: optsRef.current.language ?? "",
          target: optsRef.current.target ?? "",
          // Pass through unless explicitly set to false.
          route: optsRef.current.route !== false,
        }),
      );
      // Tell the server to drop any pending TTS jobs (barge-in #2).
      // Sent right after ``start`` so the server has accepted the
      // session before we start mutating its state. The server-side
      // dispatcher ignores ``interrupt`` if it arrives pre-``start``.
      try {
        ws.send(JSON.stringify({ type: "interrupt" }));
      } catch {
        /* socket may already be closing */
      }
    };

    ws.onmessage = (ev) => {
      // We only ever receive text frames from the server.
      if (typeof ev.data !== "string") return;
      let frame: { type?: string; text?: string; code?: string; message?: string;
        language?: string | null; duration_ms?: number;
        route?: { action: string; detail: string } };
      try {
        frame = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (frame.type === "ready") {
        wsReadyRef.current = true;
        // Start the recorder NOW — before "ready" the server isn't
        // listening for binary frames yet.
        const rec = mime
          ? new MediaRecorder(stream, { mimeType: mime })
          : new MediaRecorder(stream);
        recorderRef.current = rec;
        rec.ondataavailable = (e) => {
          if (e.data.size <= 0) return;
          audioByteCountRef.current += e.data.size;
          // Forward the chunk as binary. ``e.data`` is a Blob; the WS
          // accepts Blob directly so we don't pay an arrayBuffer hop.
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(e.data);
          }
        };
        rec.onstop = () => {
          // Tell the server we're done — it'll flush a final transcribe
          // and respond with {type:"final"}.
          if (ws.readyState === WebSocket.OPEN) {
            try {
              ws.send(JSON.stringify({ type: "stop" }));
            } catch {
              /* socket already closing */
            }
          }
        };
        // 500ms timeslice gives the server ~3 chunks per partial pass.
        rec.start(500);
        setRecording(true);
      } else if (frame.type === "partial") {
        const t = (frame.text || "").trim();
        if (t) {
          setPartialText(t);
          optsRef.current.onPartial?.(t);
        }
      } else if (frame.type === "final") {
        const result: PushToTalkResult = {
          text: frame.text || "",
          language: frame.language ?? null,
          durationMs: frame.duration_ms ?? 0,
          route: frame.route ?? { action: "unknown", detail: "" },
          rawAudioBytes: audioByteCountRef.current,
        };
        setLastResult(result);
        optsRef.current.onResult?.(result);
        const resolver = finalResolverRef.current;
        finalResolverRef.current = null;
        resolver?.(result);
      } else if (frame.type === "error") {
        const msg = frame.message || frame.code || "voice stream error";
        setError(msg);
        optsRef.current.onError?.(msg);
        const resolver = finalResolverRef.current;
        finalResolverRef.current = null;
        resolver?.(null);
      }
    };

    ws.onerror = () => {
      const msg = "음성 스트림 연결에 실패했습니다.";
      setError(msg);
      optsRef.current.onError?.(msg);
      const resolver = finalResolverRef.current;
      finalResolverRef.current = null;
      resolver?.(null);
    };

    ws.onclose = () => {
      wsReadyRef.current = false;
      // If stop() is awaiting a final and the socket closed without one,
      // unblock the caller so the UI doesn't hang.
      const resolver = finalResolverRef.current;
      if (resolver) {
        finalResolverRef.current = null;
        resolver(null);
      }
      setRecording(false);
      setUploading(false);
      const s = streamRef.current;
      streamRef.current = null;
      if (s) s.getTracks().forEach((t) => t.stop());
      recorderRef.current = null;
      wsRef.current = null;
      // Don't clear partialText here — the button uses it to render the
      // last-seen caption briefly after release.
    };
  }, [cleanup]);

  const stopStream = useCallback(async (): Promise<PushToTalkResult | null> => {
    const rec = recorderRef.current;
    if (!rec || rec.state === "inactive") {
      setRecording(false);
      return null;
    }
    setRecording(false);
    setUploading(true);

    const final = await new Promise<PushToTalkResult | null>((resolve) => {
      finalResolverRef.current = resolve;
      try {
        rec.stop();
      } catch {
        resolve(null);
      }
    });

    setUploading(false);
    setPartialText("");
    return final;
  }, []);

  // ── Public wrappers ───────────────────────────────────────────────
  const start = useCallback(async () => {
    if (!supported) {
      const msg = "이 브라우저는 마이크 캡처를 지원하지 않습니다.";
      setError(msg);
      optsRef.current.onError?.(msg);
      return;
    }
    if (recorderRef.current) return; // already recording
    // Barge-in: pause any in-flight agent TTS *before* we open the
    // mic so the user's voice doesn't fight the speaker. Sync call —
    // a swallowed throw here would block the recorder, which is the
    // bigger UX problem.
    try {
      optsRef.current.onInterrupt?.();
    } catch {
      /* defensive — caller bug shouldn't block recording */
    }
    if (optsRef.current.mode === "stream") {
      await startStream();
    } else {
      await startBatch();
    }
  }, [supported, startBatch, startStream]);

  const stop = useCallback(async (): Promise<PushToTalkResult | null> => {
    if (optsRef.current.mode === "stream") {
      return await stopStream();
    }
    return await stopBatch();
  }, [stopBatch, stopStream]);

  return {
    recording,
    uploading,
    error,
    lastResult,
    partialText,
    start,
    stop,
    supported,
  };
}

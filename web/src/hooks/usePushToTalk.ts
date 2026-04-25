"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

/**
 * Phase 2-#4 — push-to-talk recorder backed by the server-side Cohere
 * ASR endpoint. The browser captures a WebM/Opus blob via MediaRecorder,
 * uploads it to ``/api/voice/command``, and the backend chains
 * transcribe → ExternalInputRouter so the Director (or a named target)
 * receives the utterance as a human-feedback message.
 *
 * Why server-side ASR even though the platform has Web Speech API?
 *   1. The user explicitly chose ``CohereLabs/cohere-transcribe-03-2026``
 *      (multilingual, robust to noise; far better than what Safari ships).
 *   2. Server-side STT also works on devices without SpeechRecognition,
 *      so a phone viewer can drive the swarm hands-free.
 */

export interface PushToTalkResult {
  text: string;
  language: string | null;
  durationMs: number;
  route: { action: string; detail: string };
  rawAudioBytes: number;
}

export interface UsePushToTalkOptions {
  /** Optional per-call agent target. Empty/undefined → routed to Director. */
  target?: string;
  /** Language hint passed to the ASR processor. Empty → server default. */
  language?: string;
  /** Notified whenever a transcription completes successfully. */
  onResult?: (result: PushToTalkResult) => void;
  /** Notified whenever an error happens at any stage. */
  onError?: (err: string) => void;
}

export interface UsePushToTalk {
  recording: boolean;
  uploading: boolean;
  error: string | null;
  lastResult: PushToTalkResult | null;
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

export function usePushToTalk(options: UsePushToTalkOptions = {}): UsePushToTalk {
  const [recording, setRecording] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<PushToTalkResult | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const stopResolverRef = useRef<((blob: Blob) => void) | null>(null);
  const optsRef = useRef(options);
  optsRef.current = options;

  const supported =
    typeof window !== "undefined" &&
    typeof MediaRecorder !== "undefined" &&
    !!window.navigator?.mediaDevices?.getUserMedia;

  // Tear down the active stream + recorder. Idempotent.
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
  }, []);

  useEffect(() => () => cleanup(), [cleanup]);

  const start = useCallback(async () => {
    if (!supported) {
      const msg = "이 브라우저는 마이크 캡처를 지원하지 않습니다.";
      setError(msg);
      optsRef.current.onError?.(msg);
      return;
    }
    if (recorderRef.current) return; // already recording
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
        // Stop also releases the mic so the browser tab indicator turns
        // off — release immediately rather than on next user gesture.
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
  }, [supported, cleanup]);

  const stop = useCallback(async (): Promise<PushToTalkResult | null> => {
    const rec = recorderRef.current;
    if (!rec || rec.state === "inactive") {
      setRecording(false);
      return null;
    }
    setRecording(false);
    setUploading(true);

    // Race the recorder's onstop. We resolve with the blob once stop()
    // has flushed the encoder.
    const blob = await new Promise<Blob>((resolve) => {
      stopResolverRef.current = resolve;
      try {
        rec.stop();
      } catch {
        // If stop throws there will be no onstop — fall back to whatever
        // chunks we accumulated so the upload still happens.
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

  return { recording, uploading, error, lastResult, start, stop, supported };
}

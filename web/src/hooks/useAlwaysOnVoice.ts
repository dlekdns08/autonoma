"use client";

/**
 * Always-on conversation mode — feature "Wave A".
 *
 * Push-to-talk asks the user to consciously open the mic. This hook
 * keeps the mic open and lets the browser's silero-vad (a tiny ONNX
 * model that runs locally) decide *when* the user is speaking. The
 * loop:
 *
 *   1. ``MicVAD`` watches the mic. When energy + a learned classifier
 *      both fire, ``onSpeechStart`` triggers — we immediately call
 *      ``onInterrupt`` so the agent's TTS goes silent (barge-in).
 *   2. When the user pauses (silero confirms a redemption window of
 *      silence) ``onSpeechEnd`` fires with a Float32 PCM buffer of
 *      the spoken segment, sample-rate 16 kHz, mono.
 *   3. We encode the buffer to a WAV blob and POST it to
 *      ``/api/voice/command`` — the same batch endpoint the
 *      original PushToTalk uses. The server transcribes + routes
 *      through ExternalInputRouter, the agent eventually replies,
 *      and TTS lands on the WS as usual.
 *
 * Why batch (POST), not the streaming WS?
 *  * The WS path expects WebM/Opus chunks shaped by MediaRecorder;
 *    silero hands us raw PCM. Re-encoding to WebM in the browser
 *    isn't free and the latency benefit of partials is small for
 *    always-on (the user never sees a "stop" button — they just
 *    finish talking and the answer arrives).
 *  * Batch is one round-trip, simpler error handling, and reuses
 *    the same routing/transcript-logging path we already harden-
 *    tested.
 *
 * Echo cancellation lives in the browser's getUserMedia constraints
 * (``echoCancellation: true``) — modern Chrome / Safari run an
 * AEC step before the audio reaches our VAD, so the agent's own
 * speaker output is mostly subtracted before silero sees it. With
 * headphones it's near-perfect; with speakers, false-positive
 * triggers are rare but not zero, which is why ``onInterrupt`` is
 * also keyed on the *positive* speech threshold (not just any
 * energy spike).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

export interface AlwaysOnResult {
  text: string;
  language: string | null;
  durationMs: number;
  route: { action: string; detail: string };
}

export interface UseAlwaysOnVoiceOptions {
  /** Optional per-call agent target. Empty/undefined → routed to Director. */
  target?: string;
  /** Language hint for the ASR processor. */
  language?: string;
  onResult?: (r: AlwaysOnResult) => void;
  onError?: (msg: string) => void;
  /** Fired the moment VAD detects the user starting to speak — caller
   *  uses this to pause TTS playback (barge-in). */
  onInterrupt?: () => void;
  /** True if VAD currently classifies the audio as speech. Useful
   *  for showing a "listening" pulse on the UI. */
  onSpeakingChange?: (speaking: boolean) => void;
}

export type AlwaysOnState =
  | "off"
  | "loading"   // VAD model is downloading + warming
  | "idle"      // listening but no speech detected
  | "speaking"  // user is speaking right now
  | "uploading" // posting the captured segment
  | "error";

export interface UseAlwaysOnVoice {
  state: AlwaysOnState;
  error: string | null;
  lastText: string;
  /** Start the always-on loop. Resolves after the VAD model is ready. */
  start: () => Promise<void>;
  /** Stop and release the mic. */
  stop: () => Promise<void>;
}

const TARGET_SAMPLE_RATE = 16000;

// Encode a Float32 mono PCM buffer to a 16-bit WAV blob. The Cohere
// processor accepts a wide range of containers — WAV is the simplest
// and avoids the WebM header/cluster gymnastics that the streaming
// path needs.
function pcmToWav(samples: Float32Array, sampleRate: number): Blob {
  const bytesPerSample = 2;
  const headerSize = 44;
  const dataSize = samples.length * bytesPerSample;
  const buf = new ArrayBuffer(headerSize + dataSize);
  const view = new DataView(buf);

  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };

  // RIFF header
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeStr(8, "WAVE");

  // fmt chunk (PCM, mono)
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);            // PCM chunk size
  view.setUint16(20, 1, true);             // format = PCM
  view.setUint16(22, 1, true);             // channels = mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true); // byte rate
  view.setUint16(32, bytesPerSample, true); // block align
  view.setUint16(34, 16, true);             // bits per sample

  // data chunk
  writeStr(36, "data");
  view.setUint32(40, dataSize, true);

  // Float32 [-1, 1] → int16. Clamp because the model has no
  // guarantee its outputs stay strictly inside the unit range.
  let off = headerSize;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    off += 2;
  }

  return new Blob([buf], { type: "audio/wav" });
}

export function useAlwaysOnVoice(
  options: UseAlwaysOnVoiceOptions = {},
): UseAlwaysOnVoice {
  const [state, setState] = useState<AlwaysOnState>("off");
  const [error, setError] = useState<string | null>(null);
  const [lastText, setLastText] = useState("");

  const optsRef = useRef(options);
  optsRef.current = options;

  // VAD is a heavy ONNX model — load it lazily and hold a single
  // instance per hook lifetime. ``unknown`` until we actually
  // dynamic-import the module.
  const vadRef = useRef<{ start: () => void; pause: () => void; destroy: () => void } | null>(null);

  const uploadSegment = useCallback(async (audio: Float32Array) => {
    if (audio.length === 0) return;
    const wav = pcmToWav(audio, TARGET_SAMPLE_RATE);
    const form = new FormData();
    form.append("audio", wav, "always-on.wav");
    if (optsRef.current.target) form.append("target", optsRef.current.target);
    if (optsRef.current.language) form.append("language", optsRef.current.language);

    setState("uploading");
    try {
      const res = await fetch(`${API_BASE_URL}/api/voice/command`, {
        method: "POST",
        credentials: "include",
        body: form,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        const msg =
          (detail?.detail?.message as string | undefined) ?? `HTTP ${res.status}`;
        setError(msg);
        optsRef.current.onError?.(msg);
        // Don't flip to ``error`` permanently — a single transcribe
        // failure shouldn't kill the always-on loop. Drop back to
        // ``idle`` so the next utterance still gets a chance.
        setState("idle");
        return;
      }
      const data = (await res.json()) as {
        transcript: { text: string; language: string | null; duration_ms: number };
        route: { action: string; detail: string };
      };
      const text = (data.transcript.text || "").trim();
      setLastText(text);
      const result: AlwaysOnResult = {
        text,
        language: data.transcript.language,
        durationMs: data.transcript.duration_ms,
        route: data.route,
      };
      optsRef.current.onResult?.(result);
      setState("idle");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      optsRef.current.onError?.(msg);
      setState("idle");
    }
  }, []);

  const start = useCallback(async () => {
    if (vadRef.current) return; // already running
    setError(null);
    setState("loading");
    try {
      // Dynamic import so the ~5 MB ONNX runtime + silero weights are
      // only fetched when the user actually opts into always-on.
      const mod = await import("@ricky0123/vad-web");
      const { MicVAD } = mod;

      const vad = await MicVAD.new({
        // Silero-vad heuristics. Defaults are tuned for English; we
        // loosen ``negativeSpeechThreshold`` slightly for Korean
        // (vowel-heavy → energy stays high during quiet bits).
        positiveSpeechThreshold: 0.5,
        negativeSpeechThreshold: 0.3,
        // 8 frames × 32 ms ≈ 256 ms of confirmed silence before we
        // call onSpeechEnd. Short enough that pauses feel snappy,
        // long enough that "음..." pauses don't fragment the segment.
        redemptionFrames: 8,
        preSpeechPadFrames: 4,
        minSpeechFrames: 4,
        onSpeechStart: () => {
          setState("speaking");
          optsRef.current.onSpeakingChange?.(true);
          // Barge-in: tell the caller to mute the agent immediately.
          // We invoke this before any audio reaches the server so
          // the cut-over is instantaneous in the user's ear.
          try {
            optsRef.current.onInterrupt?.();
          } catch {
            /* defensive */
          }
        },
        onSpeechEnd: (audio: Float32Array) => {
          optsRef.current.onSpeakingChange?.(false);
          // Fire-and-forget: the upload mutates state asynchronously
          // and we want the VAD loop to keep listening for the next
          // utterance immediately, not block on the round-trip.
          void uploadSegment(audio);
        },
        onVADMisfire: () => {
          // Silero thought it was speech but the segment was too
          // short to count. Just drop back to idle.
          optsRef.current.onSpeakingChange?.(false);
          setState("idle");
        },
      });
      vad.start();
      vadRef.current = vad;
      setState("idle");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      optsRef.current.onError?.(msg);
      setState("error");
    }
  }, [uploadSegment]);

  const stop = useCallback(async () => {
    const vad = vadRef.current;
    vadRef.current = null;
    if (vad) {
      try {
        vad.pause();
        vad.destroy();
      } catch {
        /* already destroyed */
      }
    }
    setState("off");
  }, []);

  // Tear down on unmount so the mic indicator clears.
  useEffect(() => () => {
    void stop();
  }, [stop]);

  return { state, error, lastText, start, stop };
}

"use client";

/**
 * Highlight Clip Generator — rolling MediaRecorder buffer.
 *
 * The watch page renders a (potentially three.js) ``<canvas>`` for the
 * VTuber spotlight. This hook taps that canvas via ``captureStream(30)``,
 * pipes the resulting MediaStream into a MediaRecorder, and keeps a
 * rolling deque of encoded blob chunks covering the last
 * ``durationSec`` seconds. ``flush()`` returns a single ``Blob`` over
 * the entire retained window, ready for upload to ``POST /api/clips``.
 *
 * Why server-side compose is *not* used here:
 *
 *   1. We don't have a frame-accurate server-side render of the scene
 *      (the visual is produced in the browser by react-three-fiber +
 *      VRM). Pulling pixels off the browser canvas and uploading the
 *      encoded result is the cheap path to a shareable clip.
 *   2. MediaRecorder produces a fully-formed WebM/MP4 container — no
 *      mux step needed. We just hand the blob to the backend as-is.
 *
 * Known limitations / gaps (matching the MVP DoD):
 *
 *   - The captured stream is video-only. The hook doesn't currently
 *     attach the agent TTS audio track; clips play silently. A future
 *     iteration can call ``new MediaStream([...videoTracks,
 *     ...audioTracks])`` once we expose the synthesised audio element.
 *   - Codec selection: we prefer ``video/webm;codecs=vp9`` then
 *     ``video/webm`` then ``video/mp4``. Safari (which won't ship
 *     WebM until much later in Safari 17 across all devices) typically
 *     lands on MP4; everything else lands on WebM/VP9.
 *   - "Last N seconds" trimming is approximate: MediaRecorder emits
 *     timesliced chunks (1s by default here) but the container header
 *     lives in the *first* chunk. Slicing on chunk boundary and
 *     concatenating works fine for WebM (Cluster boundaries are seek
 *     points) but the rolling window may include slightly more than
 *     ``durationSec`` of footage. That's acceptable for the MVP — the
 *     viewer just wants "the last bit".
 *
 * The hook is feature-detection aware: when ``MediaRecorder`` is
 * unavailable (or no canvas is found) ``supported`` returns false and
 * ``start`` is a no-op. The caller can render a "Clipping not
 * supported" hint instead of the button.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** Public surface returned by ``useRollingRecorder``. */
export interface UseRollingRecorderResult {
  /** True when the env supports MediaRecorder + canvas capture. */
  supported: boolean;
  /** True between ``start()`` and ``stop()`` — i.e. recording is live. */
  recording: boolean;
  /** Start capturing the canvas. No-op if unsupported or already running. */
  start: () => void;
  /** Stop the recorder and release resources. */
  stop: () => void;
  /** Snapshot the rolling buffer as a single ``Blob``. Returns ``null``
   *  if nothing has been recorded yet. The mime is whatever the
   *  recorder negotiated at start time. */
  flush: () => Promise<{ blob: Blob; mime: string; durationMs: number } | null>;
}

/** Options bag — kept tiny on purpose; the MVP only exposes window length. */
export interface UseRollingRecorderOptions {
  /** Rolling window length in seconds. Defaults to 30s. */
  durationSec?: number;
  /** Optional explicit canvas accessor. When omitted the hook picks the
   *  largest visible canvas in the document, which matches the watch
   *  page layout where the VTuber spotlight is the dominant element. */
  getCanvas?: () => HTMLCanvasElement | null;
}

interface BufferedChunk {
  data: Blob;
  /** ``performance.now()`` at the moment the chunk was emitted. */
  ts: number;
}

/** Time between MediaRecorder ``dataavailable`` ticks. Tuning notes:
 *
 *   - Too small (e.g. 100 ms) and we churn through tiny chunks, paying
 *     mux overhead per slice.
 *   - Too large (e.g. 5 s) and the rolling-window granularity gets
 *     blocky; on a 30s window we'd retain up to 35s in the worst case.
 *
 * 1 s is the sweet spot for "feels instant when the user hits clip" and
 * keeps the buffer count manageable. */
const TIMESLICE_MS = 1000;

/** Candidate mime types in priority order. The first one supported by
 *  the browser wins. We deliberately *don't* include audio in any of
 *  the candidates — see the module docstring. */
const CANDIDATE_MIMES = [
  "video/webm;codecs=vp9",
  "video/webm;codecs=vp8",
  "video/webm",
  "video/mp4",
];

function pickMime(): string | null {
  if (typeof MediaRecorder === "undefined") return null;
  for (const m of CANDIDATE_MIMES) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return null;
}

function findLargestCanvas(): HTMLCanvasElement | null {
  if (typeof document === "undefined") return null;
  const all = Array.from(document.querySelectorAll("canvas"));
  if (all.length === 0) return null;
  let best: HTMLCanvasElement | null = null;
  let bestArea = 0;
  for (const c of all) {
    const rect = c.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const area = rect.width * rect.height;
    if (area > bestArea) {
      bestArea = area;
      best = c;
    }
  }
  return best;
}

export function useRollingRecorder(
  opts: UseRollingRecorderOptions = {},
): UseRollingRecorderResult {
  const durationSec = opts.durationSec ?? 30;
  const getCanvas = opts.getCanvas;

  // ``supported`` is computed once on mount because MediaRecorder
  // availability doesn't flip at runtime. We use ``useState`` with a
  // lazy initializer (React's canonical "compute once at mount" hatch)
  // rather than an effect: on the server the initializer returns
  // ``false`` so SSR is stable; the client re-evaluates on first paint.
  const [supported] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return pickMime() !== null && typeof HTMLCanvasElement !== "undefined";
  });
  const [recording, setRecording] = useState<boolean>(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BufferedChunk[]>([]);
  const mimeRef = useRef<string>("video/webm");
  const startedAtRef = useRef<number>(0);

  /** Drop chunks older than the configured rolling window. Called on
   *  every ``dataavailable`` so the buffer can't grow unbounded — even
   *  a viewer who leaves the page open for hours stays bounded to
   *  ``durationSec`` worth of memory. */
  const trim = useCallback(() => {
    const cutoff = performance.now() - durationSec * 1000;
    // Keep at least one chunk so we always have *something* to flush.
    while (chunksRef.current.length > 1 && chunksRef.current[0].ts < cutoff) {
      chunksRef.current.shift();
    }
  }, [durationSec]);

  const start = useCallback(() => {
    if (recorderRef.current) return; // already running
    const mime = pickMime();
    if (!mime) return;
    const canvas = (getCanvas ? getCanvas() : null) ?? findLargestCanvas();
    if (!canvas) return;

    let stream: MediaStream;
    try {
      // 30 fps gives a smooth-enough capture without doubling the
      // encode bill against the 60 fps the GPU may be rendering at.
      stream = canvas.captureStream(30);
    } catch (err) {
      // Some browsers throw on canvases backed by an OffscreenCanvas
      // (e.g. transferred to a worker) — three-fiber doesn't do that
      // by default but we guard anyway.
      console.warn("[clip] captureStream failed:", err);
      return;
    }

    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, { mimeType: mime });
    } catch (err) {
      console.warn("[clip] MediaRecorder construct failed:", err);
      stream.getTracks().forEach((t) => t.stop());
      return;
    }

    chunksRef.current = [];
    mimeRef.current = mime;
    startedAtRef.current = performance.now();

    recorder.addEventListener("dataavailable", (ev: BlobEvent) => {
      // ``ev.data`` is empty in the moment after stop() in some
      // browsers; skip those so an empty chunk doesn't survive trim().
      if (!ev.data || ev.data.size === 0) return;
      chunksRef.current.push({ data: ev.data, ts: performance.now() });
      trim();
    });
    recorder.addEventListener("error", (ev) => {
      console.warn("[clip] MediaRecorder error:", ev);
    });

    try {
      recorder.start(TIMESLICE_MS);
    } catch (err) {
      console.warn("[clip] MediaRecorder.start failed:", err);
      stream.getTracks().forEach((t) => t.stop());
      return;
    }

    recorderRef.current = recorder;
    streamRef.current = stream;
    setRecording(true);
  }, [getCanvas, trim]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    const stream = streamRef.current;
    recorderRef.current = null;
    streamRef.current = null;
    setRecording(false);
    try {
      if (recorder && recorder.state !== "inactive") recorder.stop();
    } catch {
      // recorder.stop() throws if already inactive — ignored.
    }
    if (stream) {
      stream.getTracks().forEach((t) => {
        try {
          t.stop();
        } catch {
          /* ignore */
        }
      });
    }
  }, []);

  /** Build a single Blob from the buffered chunks. We don't truncate
   *  to durationSec here — ``trim()`` already keeps the buffer to the
   *  rolling window, modulo the "keep at least one chunk" guard. The
   *  caller can take the blob and ship it directly to the backend.
   *
   *  Implementation note: ``MediaRecorder.requestData()`` would let us
   *  cut a fresh chunk at exactly "now". We deliberately don't call it
   *  because doing so changes the time origin of the next chunk and
   *  some browsers emit a malformed container header when slicing
   *  mid-Cluster. The "last full second is missing" UX cost is
   *  acceptable for the MVP. */
  const flush = useCallback(async () => {
    if (chunksRef.current.length === 0) return null;
    const mime = mimeRef.current;
    const blobs = chunksRef.current.map((c) => c.data);
    const blob = new Blob(blobs, { type: mime });
    const oldest = chunksRef.current[0].ts;
    const newest = chunksRef.current[chunksRef.current.length - 1].ts;
    const durationMs = Math.max(0, Math.round(newest - oldest));
    return { blob, mime, durationMs };
  }, []);

  // Cleanup on unmount — if the page navigates away mid-recording, we
  // need to stop the recorder and the stream tracks or Chrome leaks the
  // canvas tap (visible as a stuck "recording" indicator on some Linux
  // builds).
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);

  return { supported, recording, start, stop, flush };
}

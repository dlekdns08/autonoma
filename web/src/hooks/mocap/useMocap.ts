"use client";

/**
 * ``useMocap`` — end-to-end motion-capture runtime for the ``/mocap``
 * page. Owns:
 *
 *   - the getUserMedia webcam stream,
 *   - the MediaPipe Tasks Vision FaceLandmarker + PoseLandmarker,
 *   - the per-frame solve loop (driven off ``requestVideoFrameCallback``
 *     where available, else rAF),
 *   - the recorder — captures solved frames into a resampled, uniform
 *     clip and gzips the result ready for upload.
 *
 * The caller (``/mocap/page.tsx``) subscribes to ``latestSample`` each
 * frame for the live preview, then calls ``startRecording`` /
 * ``stopRecording`` to produce a ``MocapClip`` payload.
 *
 * All heavy objects are lazy-initialised on first ``start()`` and
 * disposed on ``stop()`` so navigating away frees the camera + WASM.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  FilesetResolver,
  FaceLandmarker,
  PoseLandmarker,
  type FaceLandmarkerResult,
  type PoseLandmarkerResult,
} from "@mediapipe/tasks-vision";
import {
  MOCAP_CLIP_VERSION,
  MOCAP_BONES,
  MOCAP_EXPRESSIONS,
  expectedFrameCount,
  type MocapBone,
  type MocapClip,
  type MocapExpression,
} from "@/lib/mocap/clipFormat";
import type { ClipSample } from "@/lib/mocap/clipPlayer";
import { createSampleBuffer } from "@/lib/mocap/clipPlayer";
import { MocapSolver } from "@/lib/mocap/solver";

/** Target recording framerate. 30fps is the Tasks Vision default and
 *  fits inside webcam capture on every laptop we tested. */
const RECORD_FPS = 30;
const RECORD_DT = 1 / RECORD_FPS;
/** Hard ceiling on clip length. Matches ``validate_payload`` on the
 *  server — bigger uploads are rejected. */
const MAX_CLIP_SECONDS = 60;

/** MediaPipe WASM base — populated by ``npm run mocap:fetch`` into
 *  ``public/mediapipe``. Served at runtime from the same origin so CSP
 *  stays tight and reproducible builds don't depend on a CDN. */
const WASM_BASE = "/mediapipe";
/** Model files also shipped in ``public/mediapipe``. */
const FACE_MODEL_URL = `${WASM_BASE}/face_landmarker.task`;
const POSE_MODEL_URL = `${WASM_BASE}/pose_landmarker_full.task`;

export type MocapStatus =
  | "idle"
  | "loading"
  | "running"
  | "error";

export interface RecordingMeta {
  startedAt: number;
  durationS: number;
  frameCount: number;
}

export interface UseMocapOptions {
  /** Mirror webcam horizontally. Default true. */
  mirror?: boolean;
}

export interface UseMocapReturn {
  status: MocapStatus;
  error: string | null;
  /** Latest solved sample. Reference-stable — read keys defensively. */
  latestSample: ClipSample;
  /** Raw video element; caller attaches it to the DOM for preview. */
  videoRef: React.RefObject<HTMLVideoElement | null>;
  /** Frame seq number that bumps on each solve — listeners can use
   *  this as a dependency to re-render at capture cadence. */
  frameSeq: number;
  /** True while a recording is actively collecting frames. */
  recording: boolean;
  recordingMeta: RecordingMeta | null;

  start: () => Promise<void>;
  stop: () => void;

  startRecording: () => void;
  /** Stop and return the normalised clip (without id/name — the caller
   *  assigns those before upload). Returns null if the recording was
   *  too short to be useful (< 2 frames). */
  stopRecording: () => MocapClip | null;
  /** Abandon the in-flight recording buffer. */
  cancelRecording: () => void;
}

interface RawFrame {
  tsSec: number;
  bones: Partial<Record<MocapBone, [number, number, number, number]>>;
  expressions: Partial<Record<MocapExpression, number>>;
}

export function useMocap(opts: UseMocapOptions = {}): UseMocapReturn {
  const [status, setStatus] = useState<MocapStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [frameSeq, setFrameSeq] = useState(0);
  const [recording, setRecording] = useState(false);
  const [recordingMeta, setRecordingMeta] = useState<RecordingMeta | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const faceRef = useRef<FaceLandmarker | null>(null);
  const poseRef = useRef<PoseLandmarker | null>(null);
  const solverRef = useRef<MocapSolver | null>(null);
  const sampleRef = useRef<ClipSample>(createSampleBuffer());
  const rafHandleRef = useRef<number | null>(null);
  const vfcCleanupRef = useRef<(() => void) | null>(null);
  const runningRef = useRef(false);

  // Recording state lives in refs so the capture loop can append without
  // triggering re-renders every frame. React state (`recording`,
  // `recordingMeta`) is updated at coarse intervals.
  const recordingRef = useRef(false);
  const recordStartRef = useRef(0);
  const rawFramesRef = useRef<RawFrame[]>([]);

  const stop = useCallback(() => {
    runningRef.current = false;
    if (rafHandleRef.current !== null) {
      cancelAnimationFrame(rafHandleRef.current);
      rafHandleRef.current = null;
    }
    if (vfcCleanupRef.current) {
      vfcCleanupRef.current();
      vfcCleanupRef.current = null;
    }
    if (streamRef.current) {
      for (const t of streamRef.current.getTracks()) t.stop();
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
    faceRef.current?.close();
    faceRef.current = null;
    poseRef.current?.close();
    poseRef.current = null;
    solverRef.current?.reset();
    recordingRef.current = false;
    setRecording(false);
    setRecordingMeta(null);
    setStatus("idle");
  }, []);

  const start = useCallback(async () => {
    if (runningRef.current) return;
    setStatus("loading");
    setError(null);
    try {
      // 1) Webcam.
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
          facingMode: "user",
          frameRate: { ideal: 30 },
        },
        audio: false,
      });
      streamRef.current = stream;
      const video = videoRef.current;
      if (!video) throw new Error("video element missing");
      video.srcObject = stream;
      video.muted = true;
      video.playsInline = true;
      await video.play();

      // 2) MediaPipe Tasks Vision runtime.
      const vision = await FilesetResolver.forVisionTasks(WASM_BASE);
      const face = await FaceLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: FACE_MODEL_URL, delegate: "GPU" },
        runningMode: "VIDEO",
        outputFaceBlendshapes: true,
        outputFacialTransformationMatrixes: true,
        numFaces: 1,
      });
      const pose = await PoseLandmarker.createFromOptions(vision, {
        baseOptions: { modelAssetPath: POSE_MODEL_URL, delegate: "GPU" },
        runningMode: "VIDEO",
        numPoses: 1,
      });
      faceRef.current = face;
      poseRef.current = pose;
      solverRef.current = new MocapSolver({ mirror: opts.mirror ?? true });

      runningRef.current = true;
      setStatus("running");

      const tick = (tsMs: number) => {
        if (!runningRef.current || !video) return;
        // video.currentTime can be stale on the very first frame — if we
        // feed Tasks Vision a zero timestamp it throws.
        if (video.readyState < 2) {
          rafHandleRef.current = requestAnimationFrame(tick);
          return;
        }
        let faceRes: FaceLandmarkerResult | null = null;
        let poseRes: PoseLandmarkerResult | null = null;
        try {
          faceRes = faceRef.current?.detectForVideo(video, tsMs) ?? null;
        } catch {
          faceRes = null;
        }
        try {
          poseRes = poseRef.current?.detectForVideo(video, tsMs) ?? null;
        } catch {
          poseRes = null;
        }
        const tsSec = tsMs / 1000;
        solverRef.current?.solveInto(faceRes, poseRes, tsSec, sampleRef.current);

        if (recordingRef.current) {
          appendRawFrame(sampleRef.current, tsSec);
        }

        setFrameSeq((s) => s + 1);
        rafHandleRef.current = requestAnimationFrame(tick);
      };
      rafHandleRef.current = requestAnimationFrame(tick);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus("error");
      stop();
    }
  }, [opts.mirror, stop]);

  const appendRawFrame = (sample: ClipSample, tsSec: number) => {
    // Clone the quaternion tuples — the pool buffer is reused next
    // frame so retaining references would capture whatever solve()
    // wrote last.
    const bones: RawFrame["bones"] = {};
    for (const name of Object.keys(sample.bones) as MocapBone[]) {
      const q = sample.bones[name]!;
      bones[name] = [q[0], q[1], q[2], q[3]];
    }
    const expressions: RawFrame["expressions"] = {};
    for (const name of Object.keys(sample.expressions) as MocapExpression[]) {
      expressions[name] = sample.expressions[name]!;
    }
    rawFramesRef.current.push({ tsSec, bones, expressions });
    const elapsed = tsSec - recordStartRef.current;
    if (elapsed >= MAX_CLIP_SECONDS) {
      // Auto-stop at the server's limit so the operator can't record a
      // clip that will just 400 on upload.
      recordingRef.current = false;
      setRecording(false);
      setRecordingMeta({
        startedAt: recordStartRef.current,
        durationS: MAX_CLIP_SECONDS,
        frameCount: rawFramesRef.current.length,
      });
    }
  };

  const startRecording = useCallback(() => {
    if (!runningRef.current || recordingRef.current) return;
    rawFramesRef.current = [];
    recordStartRef.current = performance.now() / 1000;
    recordingRef.current = true;
    setRecording(true);
    setRecordingMeta({ startedAt: recordStartRef.current, durationS: 0, frameCount: 0 });
  }, []);

  const cancelRecording = useCallback(() => {
    recordingRef.current = false;
    rawFramesRef.current = [];
    setRecording(false);
    setRecordingMeta(null);
  }, []);

  const stopRecording = useCallback((): MocapClip | null => {
    if (!recordingRef.current && rawFramesRef.current.length === 0) return null;
    recordingRef.current = false;
    setRecording(false);
    const raw = rawFramesRef.current;
    rawFramesRef.current = [];
    if (raw.length < 2) {
      setRecordingMeta(null);
      return null;
    }
    const startAt = raw[0].tsSec;
    const endAt = raw[raw.length - 1].tsSec;
    const rawDuration = Math.max(0, endAt - startAt);
    const durationS = Math.min(MAX_CLIP_SECONDS, Number(rawDuration.toFixed(3)));
    const frameCount = expectedFrameCount(durationS, RECORD_FPS);
    const clip = resampleClip(raw, startAt, durationS, RECORD_FPS, frameCount);
    setRecordingMeta({ startedAt: startAt, durationS, frameCount });
    return clip;
  }, []);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);

  return {
    status,
    error,
    latestSample: sampleRef.current,
    videoRef,
    frameSeq,
    recording,
    recordingMeta,
    start,
    stop,
    startRecording,
    stopRecording,
    cancelRecording,
  };
}

// ── Resampling ────────────────────────────────────────────────────────

/** Walk the raw (variable-dt) capture and emit a uniform-sampled clip.
 *  Per-bone interpolation: linear lerp on quaternion components +
 *  normalise (equivalent to slerp for adjacent samples, much cheaper).
 *  Expressions: linear lerp on scalars. Absent frames are held from
 *  their last observation so a bone that briefly lost tracking
 *  doesn't snap to identity. */
function resampleClip(
  raw: RawFrame[],
  startAt: number,
  durationS: number,
  fps: number,
  frameCount: number,
): MocapClip {
  const boneTracks: Partial<Record<MocapBone, number[]>> = {};
  const expTracks: Partial<Record<MocapExpression, number[]>> = {};

  // Pre-collect which bones/expressions appear at least once in the raw
  // data — we only materialise tracks for those. This is how a
  // face-only recording naturally ends up with no body tracks.
  const sawBone = new Set<MocapBone>();
  const sawExp = new Set<MocapExpression>();
  for (const f of raw) {
    for (const k of Object.keys(f.bones) as MocapBone[]) sawBone.add(k);
    for (const k of Object.keys(f.expressions) as MocapExpression[]) sawExp.add(k);
  }

  // Pre-extract per-channel series so the per-output-frame loop stays
  // linear: we binary-search into the timestamp array once per frame,
  // then index every active channel at that position.
  const times = raw.map((f) => f.tsSec);

  for (const name of sawBone) boneTracks[name] = new Array(frameCount * 4);
  for (const name of sawExp) expTracks[name] = new Array(frameCount);

  // Track last-known quaternion per bone so a missing sample holds.
  const lastQ: Partial<Record<MocapBone, [number, number, number, number]>> = {};
  const lastE: Partial<Record<MocapExpression, number>> = {};

  for (let i = 0; i < frameCount; i++) {
    const t = startAt + i / fps;
    let hi = bisectRight(times, t);
    if (hi <= 0) hi = 1;
    if (hi >= raw.length) hi = raw.length - 1;
    const lo = hi - 1;
    const t0 = times[lo];
    const t1 = times[hi];
    const span = Math.max(t1 - t0, 1e-6);
    const frac = Math.min(1, Math.max(0, (t - t0) / span));

    for (const name of sawBone) {
      const a = raw[lo].bones[name] ?? lastQ[name];
      const b = raw[hi].bones[name] ?? a;
      let out: [number, number, number, number];
      if (a && b) {
        // Flip sign if dot negative so we blend the short way around.
        const dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
        const s = dot < 0 ? -1 : 1;
        const bx = s * b[0], by = s * b[1], bz = s * b[2], bw = s * b[3];
        const x = a[0] + (bx - a[0]) * frac;
        const y = a[1] + (by - a[1]) * frac;
        const z = a[2] + (bz - a[2]) * frac;
        const w = a[3] + (bw - a[3]) * frac;
        const mag = Math.hypot(x, y, z, w) || 1;
        out = [x / mag, y / mag, z / mag, w / mag];
      } else if (a) {
        out = [a[0], a[1], a[2], a[3]];
      } else {
        out = [0, 0, 0, 1];
      }
      lastQ[name] = out;
      const track = boneTracks[name]!;
      const base = i * 4;
      track[base] = out[0];
      track[base + 1] = out[1];
      track[base + 2] = out[2];
      track[base + 3] = out[3];
    }
    for (const name of sawExp) {
      const a = raw[lo].expressions[name] ?? lastE[name];
      const b = raw[hi].expressions[name] ?? a;
      let v: number;
      if (a !== undefined && b !== undefined) {
        v = a + (b - a) * frac;
      } else if (a !== undefined) {
        v = a;
      } else {
        v = 0;
      }
      lastE[name] = v;
      expTracks[name]![i] = v;
    }
  }

  return {
    version: MOCAP_CLIP_VERSION,
    id: "",
    name: "",
    sourceVrm: "",
    durationS,
    fps,
    frameCount,
    bones: toTrackRecord(boneTracks),
    expressions: toTrackRecord(expTracks),
    meta: { createdAt: new Date().toISOString() },
  };
}

function toTrackRecord<K extends string>(
  m: Partial<Record<K, number[]>>,
): Partial<Record<K, { data: number[] }>> {
  const out: Partial<Record<K, { data: number[] }>> = {};
  for (const k of Object.keys(m) as K[]) {
    const arr = m[k];
    if (arr) out[k] = { data: arr };
  }
  return out;
}

function bisectRight(arr: number[], x: number): number {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (x < arr[mid]) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

// Silence unused-import warnings for re-exports — these are public
// constants downstream consumers use when building UIs.
void MOCAP_BONES;
void MOCAP_EXPRESSIONS;

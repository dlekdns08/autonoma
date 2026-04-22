/**
 * Clip playback runtime — samples bone quaternions + expression values
 * for a given time offset into a ``MocapClip``. Shared by the live
 * preview on ``/mocap`` and by the VRMCharacter playback path on the
 * main dashboard.
 *
 * Design notes
 * ────────────
 * - Sampling is frame-rate independent. Callers pass ``nowSec`` each
 *   frame; the runtime computes the relative offset itself so a 60 Hz
 *   display and a 30 Hz clip stay consistent.
 * - Interpolation: linear lerp between adjacent expression values and
 *   slerp between adjacent quaternions. The output quaternions are
 *   written into a reused pool so playback is allocation-free.
 * - Coverage map: which bones / expressions this clip drives. The
 *   VRMCharacter playback path reads this to know which idle writes
 *   to skip.
 * - ``clipCache`` memoises clips loaded from the server and coalesces
 *   concurrent ``ensure`` calls so two VRMCharacter instances rendering
 *   the same clip share one fetch.
 */

import {
  MOCAP_VOWELS,
  type MocapBone,
  type MocapClip,
  type MocapExpression,
} from "./clipFormat";

/** Per-frame sample buffers — reused across frames. */
export interface ClipSample {
  /** Keys present = bones the clip drives this frame. Each value is a
   *  4-tuple ``[x, y, z, w]`` (quaternion in humanoid-normalized space). */
  bones: Partial<Record<MocapBone, [number, number, number, number]>>;
  /** Keys present = expressions the clip drives. */
  expressions: Partial<Record<MocapExpression, number>>;
}

export function createSampleBuffer(): ClipSample {
  return { bones: {}, expressions: {} };
}

export interface ClipCoverage {
  bones: ReadonlySet<MocapBone>;
  expressions: ReadonlySet<MocapExpression>;
  /** True iff the clip contains any vowel track (suppresses amplitude
   *  lip-sync so the idle loop doesn't fight the recorded mouth shape). */
  coversMouth: boolean;
}

export function coverageFor(clip: MocapClip): ClipCoverage {
  const bones = new Set<MocapBone>(Object.keys(clip.bones) as MocapBone[]);
  const expressions = new Set<MocapExpression>(
    Object.keys(clip.expressions) as MocapExpression[],
  );
  const coversMouth = MOCAP_VOWELS.some((v) => expressions.has(v));
  return { bones, expressions, coversMouth };
}

function slerpInto(
  out: [number, number, number, number],
  ax: number,
  ay: number,
  az: number,
  aw: number,
  bx: number,
  by: number,
  bz: number,
  bw: number,
  t: number,
): void {
  // Short-path: pick sign that makes dot product non-negative.
  let dot = ax * bx + ay * by + az * bz + aw * bw;
  if (dot < 0) {
    bx = -bx;
    by = -by;
    bz = -bz;
    bw = -bw;
    dot = -dot;
  }
  // If quaternions are very close, linear interpolate to avoid sin(0).
  if (dot > 0.9995) {
    const nx = ax + t * (bx - ax);
    const ny = ay + t * (by - ay);
    const nz = az + t * (bz - az);
    const nw = aw + t * (bw - aw);
    const mag = Math.hypot(nx, ny, nz, nw) || 1;
    out[0] = nx / mag;
    out[1] = ny / mag;
    out[2] = nz / mag;
    out[3] = nw / mag;
    return;
  }
  const theta0 = Math.acos(Math.min(1, dot));
  const theta = theta0 * t;
  const sinTheta = Math.sin(theta);
  const sinTheta0 = Math.sin(theta0);
  const s0 = Math.cos(theta) - (dot * sinTheta) / sinTheta0;
  const s1 = sinTheta / sinTheta0;
  out[0] = s0 * ax + s1 * bx;
  out[1] = s0 * ay + s1 * by;
  out[2] = s0 * az + s1 * bz;
  out[3] = s0 * aw + s1 * bw;
}

export interface ClipRuntimeOptions {
  /** Looping clips never report ``done``. Default false. */
  loop?: boolean;
  /** Gesture priority the caller assigned. Not interpreted by the
   *  runtime — just surfaced so the VRMCharacter arbiter can compare
   *  priorities when a new request arrives mid-clip. */
  priority?: number;
}

export class ClipRuntime {
  readonly coverage: ClipCoverage;
  readonly priority: number;
  private readonly loop: boolean;
  private readonly startedAt: number;

  constructor(
    private readonly clip: MocapClip,
    nowSec: number,
    opts: ClipRuntimeOptions = {},
  ) {
    this.coverage = coverageFor(clip);
    this.loop = opts.loop ?? false;
    this.priority = opts.priority ?? 0;
    this.startedAt = nowSec;
  }

  get durationSec(): number {
    return this.clip.durationS;
  }

  elapsedSec(nowSec: number): number {
    return nowSec - this.startedAt;
  }

  isDone(nowSec: number): boolean {
    if (this.loop) return false;
    return this.elapsedSec(nowSec) >= this.clip.durationS;
  }

  /** Sample the clip at ``nowSec`` into ``out``. Old entries are
   *  cleared before new ones are written so the caller can iterate
   *  ``Object.keys(out.bones)`` to know what's covered this frame. */
  sampleInto(nowSec: number, out: ClipSample): void {
    for (const k of Object.keys(out.bones)) {
      delete out.bones[k as MocapBone];
    }
    for (const k of Object.keys(out.expressions)) {
      delete out.expressions[k as MocapExpression];
    }
    let t = this.elapsedSec(nowSec);
    if (this.loop) {
      t = ((t % this.clip.durationS) + this.clip.durationS) % this.clip.durationS;
    } else {
      t = Math.max(0, Math.min(this.clip.durationS, t));
    }
    const frameIdx = t * this.clip.fps;
    const i0 = Math.floor(frameIdx);
    const i1 = Math.min(i0 + 1, this.clip.frameCount - 1);
    const frac = frameIdx - i0;

    for (const [name, track] of Object.entries(this.clip.bones) as [
      MocapBone,
      { data: number[] },
    ][]) {
      const base = i0 * 4;
      const next = i1 * 4;
      const ax = track.data[base];
      const ay = track.data[base + 1];
      const az = track.data[base + 2];
      const aw = track.data[base + 3];
      const bx = track.data[next];
      const by = track.data[next + 1];
      const bz = track.data[next + 2];
      const bw = track.data[next + 3];
      const pool: [number, number, number, number] = out.bones[name] ?? [
        0, 0, 0, 1,
      ];
      slerpInto(pool, ax, ay, az, aw, bx, by, bz, bw, frac);
      out.bones[name] = pool;
    }
    for (const [name, track] of Object.entries(this.clip.expressions) as [
      MocapExpression,
      { data: number[] },
    ][]) {
      const a = track.data[i0];
      const b = track.data[i1];
      out.expressions[name] = a + (b - a) * frac;
    }
  }
}

// ── Clip cache (client-side, ref-counted) ──────────────────────────

type CacheEntry = {
  clip: MocapClip | null;
  error: Error | null;
  inflight: Promise<MocapClip> | null;
  /** How many active ``ClipRuntime`` instances hold this clip. */
  refs: number;
  /** Unix ms after which a zero-ref entry is eligible for GC. */
  expiresAt: number;
};

const CACHE = new Map<string, CacheEntry>();
/** Five minutes of idle before we throw a cold clip away. */
const IDLE_TTL_MS = 5 * 60 * 1000;

async function fetchClip(clipId: string): Promise<MocapClip> {
  const res = await fetch(`/api/mocap-clips/${encodeURIComponent(clipId)}`, {
    credentials: "same-origin",
  });
  if (!res.ok) {
    throw new Error(`fetch clip ${clipId}: ${res.status}`);
  }
  const body = (await res.json()) as {
    clip: { id: string; name: string; source_vrm: string };
    payload_gz_b64: string;
  };
  const gz = base64ToBytes(body.payload_gz_b64);
  const json = await inflate(gz);
  const clip = JSON.parse(json) as MocapClip;
  return clip;
}

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

async function inflate(bytes: Uint8Array): Promise<string> {
  // Modern browsers (the only ones running three-vrm + r3f) all ship
  // DecompressionStream — no library needed.
  const DS = (globalThis as unknown as { DecompressionStream?: typeof DecompressionStream })
    .DecompressionStream;
  if (!DS) {
    throw new Error("DecompressionStream not available");
  }
  const stream = new Blob([new Uint8Array(bytes)]).stream().pipeThrough(new DS("gzip"));
  const text = await new Response(stream).text();
  return text;
}

export const clipCache = {
  /** Kick off a background fetch so a later ``get`` resolves from
   *  memory. Safe to call repeatedly — concurrent callers share the
   *  same in-flight promise. */
  ensure(clipId: string): Promise<MocapClip> {
    let entry = CACHE.get(clipId);
    if (!entry) {
      entry = {
        clip: null,
        error: null,
        inflight: null,
        refs: 0,
        expiresAt: Date.now() + IDLE_TTL_MS,
      };
      CACHE.set(clipId, entry);
    }
    if (entry.clip) return Promise.resolve(entry.clip);
    if (entry.inflight) return entry.inflight;
    entry.inflight = fetchClip(clipId).then(
      (clip) => {
        entry!.clip = clip;
        entry!.inflight = null;
        return clip;
      },
      (err) => {
        entry!.error = err as Error;
        entry!.inflight = null;
        throw err;
      },
    );
    return entry.inflight;
  },
  /** Non-blocking lookup. Returns ``undefined`` if not yet loaded. */
  get(clipId: string): MocapClip | undefined {
    const entry = CACHE.get(clipId);
    return entry?.clip ?? undefined;
  },
  /** Invalidate a cached clip (e.g. after the clip was renamed). */
  invalidate(clipId: string): void {
    CACHE.delete(clipId);
  },
  /** Called by the VRMCharacter integration when a runtime starts/stops
   *  to keep the cache warm for active clips. */
  retain(clipId: string): void {
    const e = CACHE.get(clipId);
    if (e) e.refs += 1;
  },
  release(clipId: string): void {
    const e = CACHE.get(clipId);
    if (!e) return;
    e.refs = Math.max(0, e.refs - 1);
    if (e.refs === 0) e.expiresAt = Date.now() + IDLE_TTL_MS;
  },
  /** Periodic maintenance — callers can invoke on a long interval. */
  gc(): void {
    const now = Date.now();
    for (const [id, entry] of CACHE) {
      if (entry.refs === 0 && entry.expiresAt < now) CACHE.delete(id);
    }
  },
};

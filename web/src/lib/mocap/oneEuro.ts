/**
 * One-Euro filter — adaptive low-pass that is aggressive when the
 * underlying signal is static (kills jitter) and loose when it moves
 * fast (preserves responsiveness). Cheap and stable; the standard
 * choice for real-time human motion streams.
 *
 * Reference: Casiez, Roussel, Vogel — "1€ Filter: A Simple
 * Speed-based Low-pass Filter for Noisy Input in Interactive Systems"
 * (CHI 2012).
 */

import { ONE_EURO_DEFAULTS, type OneEuroConfig } from "./config";

export type { OneEuroConfig } from "./config";

function alpha(cutoff: number, dtSec: number): number {
  // α = 1 − exp(−2π · cutoff · dt) linearised for small dt.
  const tau = 1 / (2 * Math.PI * cutoff);
  return 1 / (1 + tau / Math.max(dtSec, 1e-5));
}

/** Scalar filter — one per channel you want to smooth. */
export class OneEuroScalar {
  private prevValue: number | null = null;
  private prevDeriv = 0;
  private prevTs = 0;
  constructor(private cfg: OneEuroConfig = ONE_EURO_DEFAULTS) {}

  reset(): void {
    this.prevValue = null;
    this.prevDeriv = 0;
    this.prevTs = 0;
  }

  filter(value: number, tsSec: number): number {
    if (this.prevValue === null) {
      this.prevValue = value;
      this.prevTs = tsSec;
      return value;
    }
    const dt = Math.max(tsSec - this.prevTs, 1e-4);
    const rawDeriv = (value - this.prevValue) / dt;
    const aD = alpha(this.cfg.dCutoff, dt);
    const deriv = aD * rawDeriv + (1 - aD) * this.prevDeriv;
    const cutoff = this.cfg.minCutoff + this.cfg.beta * Math.abs(deriv);
    const aV = alpha(cutoff, dt);
    const smoothed = aV * value + (1 - aV) * this.prevValue;
    this.prevValue = smoothed;
    this.prevDeriv = deriv;
    this.prevTs = tsSec;
    return smoothed;
  }
}

/** Quaternion smoother via slerp against a speed-adaptive alpha.
 *  Feed ``(x, y, z, w)`` and we return the smoothed components into
 *  the provided ``out`` tuple to avoid allocations. */
export class OneEuroQuat {
  private prev: [number, number, number, number] | null = null;
  private prevDeriv = 0;
  private prevTs = 0;
  constructor(private cfg: OneEuroConfig = ONE_EURO_DEFAULTS) {}

  reset(): void {
    this.prev = null;
    this.prevDeriv = 0;
    this.prevTs = 0;
  }

  filter(
    x: number,
    y: number,
    z: number,
    w: number,
    tsSec: number,
    out: [number, number, number, number],
  ): void {
    if (this.prev === null) {
      this.prev = [x, y, z, w];
      this.prevTs = tsSec;
      out[0] = x;
      out[1] = y;
      out[2] = z;
      out[3] = w;
      return;
    }
    const dt = Math.max(tsSec - this.prevTs, 1e-4);
    // Angular velocity proxy — dot product with previous. 1 = identical,
    // 0 = orthogonal (180°). Convert to a rough "radians/sec" magnitude.
    const [px, py, pz, pw] = this.prev;
    const dot = Math.abs(px * x + py * y + pz * z + pw * w);
    const angle = 2 * Math.acos(Math.min(1, dot));
    const rawDeriv = angle / dt;
    const aD = alpha(this.cfg.dCutoff, dt);
    const deriv = aD * rawDeriv + (1 - aD) * this.prevDeriv;
    const cutoff = this.cfg.minCutoff + this.cfg.beta * deriv;
    const aV = alpha(cutoff, dt);

    // Slerp prev → current by aV. For small angles a linear lerp +
    // normalise is indistinguishable and cheaper; we use it.
    // Handle double-cover: flip sign if dot negative so we interpolate
    // the short way around.
    const sign = px * x + py * y + pz * z + pw * w < 0 ? -1 : 1;
    const nx = (1 - aV) * px + aV * sign * x;
    const ny = (1 - aV) * py + aV * sign * y;
    const nz = (1 - aV) * pz + aV * sign * z;
    const nw = (1 - aV) * pw + aV * sign * w;
    const mag = Math.hypot(nx, ny, nz, nw) || 1;
    out[0] = nx / mag;
    out[1] = ny / mag;
    out[2] = nz / mag;
    out[3] = nw / mag;
    this.prev = [out[0], out[1], out[2], out[3]];
    this.prevDeriv = deriv;
    this.prevTs = tsSec;
  }
}

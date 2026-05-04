// Procedural BGM driven by swarm mood/events. Pure Web Audio — no asset
// files. Three layers (calm / focus / tension) run continuously once the
// engine is started; `setMood` crossfades the layer gains so transitions
// are smooth. One-shot pulses (kick/snare/fanfare/celebrate) are mixed
// in via the same master bus.
//
// IMPORTANT: `start()` MUST be invoked from inside a user gesture
// handler (click/keypress). Browsers gate AudioContext creation /
// resume on user activation, so calling `start()` from `useEffect` on
// mount will leave the context suspended until something else resumes
// it. The hook + toggle component in this PR honour that contract.
//
// SSR safety: every public method early-returns on the server
// (`typeof window === 'undefined'`).

export type Mood = "calm" | "focus" | "tension";
export type Pulse = "kick" | "snare" | "fanfare" | "celebrate";

const FADE_SECONDS = 1.5;

interface Layer {
  // Per-layer gain — what `setMood` ramps. Connected to master.
  gain: GainNode;
  // All sources/oscillators that need to be torn down on `stop`.
  nodes: AudioScheduledSourceNode[];
  // Optional ticker for layers that schedule notes ahead of time
  // (focus arpeggio, tension snare). Cleared on `stop`.
  interval?: ReturnType<typeof setInterval>;
}

export class BGMEngine {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private layers: Partial<Record<Mood, Layer>> = {};
  private noiseBuffer: AudioBuffer | null = null;
  private masterVolume = 0.35;
  private currentMood: Mood = "calm";
  private started = false;

  // Initialise AudioContext + build the three persistent layer graphs.
  // Safe to call multiple times — subsequent calls are no-ops once
  // started. Must be invoked from a user gesture for the context to
  // actually produce sound.
  start(): void {
    if (typeof window === "undefined") return;
    if (this.started) {
      // Already started — best-effort resume in case the context was
      // suspended (tab backgrounded, autoplay policy etc).
      this.ctx?.resume().catch(() => {});
      return;
    }
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;

    const ctx = new Ctor();
    const master = ctx.createGain();
    master.gain.value = this.masterVolume;
    master.connect(ctx.destination);
    this.ctx = ctx;
    this.master = master;

    // Pre-render a 1s white-noise buffer; reused for the snare on the
    // tension layer + any noise-based pulses.
    this.noiseBuffer = makeNoiseBuffer(ctx, 1.0);

    // Build all three layers up front so crossfades only ever touch
    // gain values — no startup latency on mood change.
    this.layers.calm = this.buildCalmLayer(ctx, master);
    this.layers.focus = this.buildFocusLayer(ctx, master);
    this.layers.tension = this.buildTensionLayer(ctx, master);

    // Start with whatever mood is currently set (default calm at full,
    // others at 0).
    for (const m of ["calm", "focus", "tension"] as const) {
      const layer = this.layers[m];
      if (!layer) continue;
      layer.gain.gain.value = m === this.currentMood ? 1 : 0;
    }

    this.started = true;
    // Resume in case the constructor returned a suspended context
    // (Safari / autoplay policy).
    ctx.resume().catch(() => {});
  }

  // Tear everything down. After `stop` the engine can be re-started
  // with `start()`.
  stop(): void {
    if (typeof window === "undefined") return;
    if (!this.started) return;
    for (const m of ["calm", "focus", "tension"] as const) {
      const layer = this.layers[m];
      if (!layer) continue;
      if (layer.interval) clearInterval(layer.interval);
      for (const node of layer.nodes) {
        try {
          node.stop();
        } catch {
          // Already stopped — fine.
        }
      }
    }
    this.layers = {};
    if (this.ctx) {
      this.ctx.close().catch(() => {});
    }
    this.ctx = null;
    this.master = null;
    this.noiseBuffer = null;
    this.started = false;
  }

  // Crossfade layer gains over FADE_SECONDS. Cheap — only mutates
  // AudioParam ramps; oscillators keep running underneath.
  setMood(mood: Mood): void {
    if (typeof window === "undefined") return;
    this.currentMood = mood;
    if (!this.started || !this.ctx) return;
    const now = this.ctx.currentTime;
    for (const m of ["calm", "focus", "tension"] as const) {
      const layer = this.layers[m];
      if (!layer) continue;
      const target = m === mood ? 1 : 0;
      const param = layer.gain.gain;
      // Cancel pending ramps and re-anchor at the current value, then
      // linear-ramp to the target. setTargetAtTime would feel smoother
      // but the exact 1.5s deadline is easier to reason about with a
      // linear ramp.
      param.cancelScheduledValues(now);
      param.setValueAtTime(param.value, now);
      param.linearRampToValueAtTime(target, now + FADE_SECONDS);
    }
  }

  // One-shot pulse mixed into master. Independent of the persistent
  // mood layers — won't disturb their state.
  pulse(kind: Pulse): void {
    if (typeof window === "undefined") return;
    if (!this.started || !this.ctx || !this.master) return;
    const ctx = this.ctx;
    const out = this.master;
    const t0 = ctx.currentTime;
    switch (kind) {
      case "kick":
        kickDrum(ctx, out, t0);
        break;
      case "snare":
        snareDrum(ctx, out, t0, this.noiseBuffer);
        break;
      case "fanfare":
        fanfareArp(ctx, out, t0, 1.5);
        break;
      case "celebrate":
        // Slightly longer celebration — fanfare + extra octave on top.
        fanfareArp(ctx, out, t0, 1.5);
        fanfareArp(ctx, out, t0 + 0.05, 1.5, 2);
        break;
    }
  }

  setMasterVolume(v: number): void {
    const clamped = Math.max(0, Math.min(1, v));
    this.masterVolume = clamped;
    if (typeof window === "undefined") return;
    if (this.master && this.ctx) {
      this.master.gain.setTargetAtTime(clamped, this.ctx.currentTime, 0.05);
    }
  }

  // ── Layer builders ────────────────────────────────────────────────

  // CALM: slow C major pad (C3/E3/G3) — sine + triangle through a
  // gentle low-pass, with a slow LFO modulating detune for vibrato.
  private buildCalmLayer(ctx: AudioContext, master: GainNode): Layer {
    const layerGain = ctx.createGain();
    layerGain.gain.value = 0;
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 800;
    lp.Q.value = 0.5;
    layerGain.connect(master);
    lp.connect(layerGain);

    const nodes: AudioScheduledSourceNode[] = [];

    // C major triad pad — root, third, fifth in the low-mid register.
    const chord: Array<{ freq: number; type: OscillatorType; level: number }> = [
      { freq: 130.81, type: "sine", level: 0.35 }, // C3
      { freq: 164.81, type: "triangle", level: 0.22 }, // E3
      { freq: 196.0, type: "sine", level: 0.22 }, // G3
    ];

    // Shared slow LFO -> detune for a breathing vibrato.
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 0.25; // 4-second cycle
    const lfoGain = ctx.createGain();
    lfoGain.gain.value = 6; // ±6 cents
    lfo.connect(lfoGain);
    lfo.start();
    nodes.push(lfo);

    for (const note of chord) {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = note.type;
      osc.frequency.value = note.freq;
      g.gain.value = note.level;
      lfoGain.connect(osc.detune);
      osc.connect(g).connect(lp);
      osc.start();
      nodes.push(osc);
    }

    return { gain: layerGain, nodes };
  }

  // FOCUS: triangle-wave arpeggio over the same C major triad,
  // mid-range, band-pass for clarity. Notes scheduled by a setInterval
  // tick (every 16th of the bar at ~110 BPM ≈ 136ms).
  private buildFocusLayer(ctx: AudioContext, master: GainNode): Layer {
    const layerGain = ctx.createGain();
    layerGain.gain.value = 0;
    const bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = 1200;
    bp.Q.value = 0.8;
    bp.connect(layerGain);
    layerGain.connect(master);

    // C major triad up an octave — arpeggio sequence with a passing
    // octave to keep it from feeling looped.
    const seq = [261.63, 329.63, 392.0, 523.25, 392.0, 329.63];
    let i = 0;
    const stepSec = 0.18;

    const interval = setInterval(() => {
      if (!this.ctx || !this.started) return;
      const now = this.ctx.currentTime;
      const f = seq[i % seq.length];
      i++;
      pluck(ctx, bp, f, now, stepSec * 1.6, "triangle", 0.18);
    }, stepSec * 1000);

    return { gain: layerGain, nodes: [], interval };
  }

  // TENSION: sub-bass square (C2) + snare-like noise on the off-beats.
  // High-pass on the noise so it cuts through; low-pass on the sub so
  // it doesn't get muddy.
  private buildTensionLayer(ctx: AudioContext, master: GainNode): Layer {
    const layerGain = ctx.createGain();
    layerGain.gain.value = 0;
    layerGain.connect(master);

    // Sub-bass — square through a low-pass for a warm growl.
    const subLp = ctx.createBiquadFilter();
    subLp.type = "lowpass";
    subLp.frequency.value = 220;
    subLp.Q.value = 1.2;
    subLp.connect(layerGain);

    const sub = ctx.createOscillator();
    const subGain = ctx.createGain();
    sub.type = "square";
    sub.frequency.value = 65.41; // C2
    subGain.gain.value = 0.28;
    sub.connect(subGain).connect(subLp);
    sub.start();

    // Slow LFO on sub gain for a pulsing, anxious feel.
    const subLfo = ctx.createOscillator();
    const subLfoGain = ctx.createGain();
    subLfo.frequency.value = 1.6; // ≈100 BPM quarter
    subLfoGain.gain.value = 0.15;
    subLfo.connect(subLfoGain).connect(subGain.gain);
    subLfo.start();

    const stepSec = 0.27; // ≈110 BPM eighths
    let beat = 0;
    const interval = setInterval(() => {
      if (!this.ctx || !this.started) return;
      const now = this.ctx.currentTime;
      // Snare on off-beats (1, 3 of every 4 ticks).
      if (beat % 2 === 1) {
        snareDrum(ctx, layerGain, now, this.noiseBuffer, 0.18);
      }
      beat++;
    }, stepSec * 1000);

    return { gain: layerGain, nodes: [sub, subLfo], interval };
  }
}

// ── Synth primitives ────────────────────────────────────────────────

function makeNoiseBuffer(ctx: AudioContext, seconds: number): AudioBuffer {
  const len = Math.max(1, Math.floor(ctx.sampleRate * seconds));
  const buffer = ctx.createBuffer(1, len, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < len; i++) {
    data[i] = Math.random() * 2 - 1;
  }
  return buffer;
}

function pluck(
  ctx: AudioContext,
  out: AudioNode,
  freq: number,
  start: number,
  duration: number,
  type: OscillatorType,
  peak: number,
): void {
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  g.gain.setValueAtTime(0, start);
  g.gain.linearRampToValueAtTime(peak, start + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  osc.connect(g).connect(out);
  osc.start(start);
  osc.stop(start + duration + 0.05);
}

function kickDrum(ctx: AudioContext, out: AudioNode, start: number): void {
  // Pitch-swept sine: 120Hz -> 40Hz over 150ms with a sharp envelope.
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(120, start);
  osc.frequency.exponentialRampToValueAtTime(40, start + 0.12);
  g.gain.setValueAtTime(0, start);
  g.gain.linearRampToValueAtTime(0.6, start + 0.005);
  g.gain.exponentialRampToValueAtTime(0.001, start + 0.18);
  osc.connect(g).connect(out);
  osc.start(start);
  osc.stop(start + 0.22);
}

function snareDrum(
  ctx: AudioContext,
  out: AudioNode,
  start: number,
  noise: AudioBuffer | null,
  peak = 0.25,
): void {
  // Filtered noise burst — the cheap-but-recognisable snare.
  const buffer = noise ?? makeNoiseBuffer(ctx, 0.2);
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  const hp = ctx.createBiquadFilter();
  hp.type = "highpass";
  hp.frequency.value = 1500;
  const g = ctx.createGain();
  g.gain.setValueAtTime(0, start);
  g.gain.linearRampToValueAtTime(peak, start + 0.005);
  g.gain.exponentialRampToValueAtTime(0.0001, start + 0.18);
  src.connect(hp).connect(g).connect(out);
  src.start(start);
  src.stop(start + 0.25);
}

function fanfareArp(
  ctx: AudioContext,
  out: AudioNode,
  start: number,
  totalSeconds: number,
  octaveShift = 1,
): void {
  // Triumphant ascending C major arp over `totalSeconds`.
  const base = [261.63, 329.63, 392.0, 523.25, 659.25, 783.99, 1046.5];
  const freqs = base.map((f) => f * octaveShift);
  const step = totalSeconds / freqs.length;
  freqs.forEach((f, i) => {
    pluck(ctx, out, f, start + i * step, step * 1.5, "square", 0.22);
  });
}

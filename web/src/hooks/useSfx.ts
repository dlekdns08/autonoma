"use client";

import { useCallback, useEffect, useRef } from "react";

// 8-bit synth SFX driven by the Web Audio API. No asset files; every
// sound is rendered on the fly by chaining a few oscillators + envelopes.
// Each preset is tuned so the swarm's chunky chiptune vibe survives
// browsers throttling autoplay (we lazily resume the AudioContext on the
// first user gesture and on every `play()`).

export type SfxName =
  | "level_up"
  | "achievement"
  | "tier_complete"
  | "boss_appear"
  | "boss_defeat"
  | "guild_form"
  | "fortune"
  | "spawn"
  | "complete"
  | "ghost"
  | "blip";

interface UseSfxOptions {
  enabled?: boolean;
  // Master gain, 0..1. Defaults are tuned to sit under TTS without
  // ducking it.
  volume?: number;
}

const STORAGE_KEY = "autonoma_sfx_enabled";

export function useSfx(options: UseSfxOptions = {}) {
  const ctxRef = useRef<AudioContext | null>(null);
  const masterRef = useRef<GainNode | null>(null);
  const enabledRef = useRef<boolean>(options.enabled ?? true);
  const volumeRef = useRef<number>(options.volume ?? 0.25);

  // Hydrate enabled state from localStorage once on mount so the user's
  // mute preference survives reload. Falls back to the `enabled` option.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored === "0") enabledRef.current = false;
      if (stored === "1") enabledRef.current = true;
    } catch {
      // localStorage may be disabled — silently ignore.
    }
  }, []);

  const ensureContext = useCallback(() => {
    if (typeof window === "undefined") return null;
    if (!ctxRef.current) {
      const Ctor =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return null;
      const ctx = new Ctor();
      const master = ctx.createGain();
      master.gain.value = volumeRef.current;
      master.connect(ctx.destination);
      ctxRef.current = ctx;
      masterRef.current = master;
    }
    if (ctxRef.current.state === "suspended") {
      // Best-effort resume — browsers gate this on a user gesture, so
      // call sites only get audio after the first click/keypress.
      ctxRef.current.resume().catch(() => {});
    }
    return ctxRef.current;
  }, []);

  const setEnabled = useCallback((value: boolean) => {
    enabledRef.current = value;
    if (typeof window !== "undefined") {
      try {
        window.localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
      } catch {
        /* ignore */
      }
    }
  }, []);

  const setVolume = useCallback((value: number) => {
    const clamped = Math.max(0, Math.min(1, value));
    volumeRef.current = clamped;
    if (masterRef.current) masterRef.current.gain.value = clamped;
  }, []);

  const play = useCallback(
    (name: SfxName) => {
      if (!enabledRef.current) return;
      const ctx = ensureContext();
      if (!ctx || !masterRef.current) return;
      const t0 = ctx.currentTime;
      const master = masterRef.current;
      switch (name) {
        case "level_up":
          // Ascending major arpeggio — C5, E5, G5, C6
          arpeggio(ctx, master, [523.25, 659.25, 783.99, 1046.5], t0, 0.09, "square");
          break;
        case "achievement":
          // Bright dyad sting + sparkle tail
          arpeggio(ctx, master, [880, 1318.51, 1760], t0, 0.08, "triangle");
          sparkle(ctx, master, t0 + 0.18, 0.35);
          break;
        case "tier_complete":
          // Bigger fanfare for tier completion (4 stacked ascending)
          arpeggio(ctx, master, [523.25, 659.25, 783.99, 1046.5, 1318.51], t0, 0.1, "square");
          sparkle(ctx, master, t0 + 0.4, 0.5);
          break;
        case "boss_appear":
          // Descending menace — low brass-ish saw
          arpeggio(ctx, master, [220, 174.61, 138.59, 110], t0, 0.16, "sawtooth");
          break;
        case "boss_defeat":
          // Triumphant arpeggio with crash
          arpeggio(ctx, master, [261.63, 392, 523.25, 783.99, 1046.5], t0, 0.1, "square");
          noiseBurst(ctx, master, t0 + 0.45, 0.3);
          break;
        case "guild_form":
          // Two-tone bell ding
          tone(ctx, master, 1046.5, t0, 0.18, "triangle", 0.18);
          tone(ctx, master, 1318.51, t0 + 0.08, 0.2, "triangle", 0.18);
          break;
        case "fortune":
          // Twinkly cookie pickup
          arpeggio(ctx, master, [1567.98, 2093, 1567.98], t0, 0.06, "triangle");
          break;
        case "spawn":
          // Quick rising blip
          sweep(ctx, master, 220, 660, t0, 0.18, "square");
          break;
        case "complete":
          // Project complete — long ascending celebration
          arpeggio(
            ctx,
            master,
            [261.63, 329.63, 392, 523.25, 659.25, 783.99, 1046.5],
            t0,
            0.1,
            "square",
          );
          sparkle(ctx, master, t0 + 0.6, 0.6);
          break;
        case "ghost":
          // Wobbly minor descend
          sweep(ctx, master, 440, 220, t0, 0.6, "sine", { detune: -50 });
          break;
        case "blip":
          tone(ctx, master, 880, t0, 0.05, "square", 0.12);
          break;
      }
    },
    [ensureContext],
  );

  return {
    play,
    setEnabled,
    setVolume,
    isEnabled: () => enabledRef.current,
  };
}

// ── Synth primitives ──────────────────────────────────────────────────

function tone(
  ctx: AudioContext,
  out: AudioNode,
  freq: number,
  start: number,
  duration: number,
  type: OscillatorType = "square",
  peak: number = 0.2,
) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  gain.gain.setValueAtTime(0, start);
  gain.gain.linearRampToValueAtTime(peak, start + 0.005);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  osc.connect(gain).connect(out);
  osc.start(start);
  osc.stop(start + duration + 0.02);
}

function arpeggio(
  ctx: AudioContext,
  out: AudioNode,
  freqs: number[],
  start: number,
  step: number,
  type: OscillatorType = "square",
) {
  freqs.forEach((f, i) => {
    tone(ctx, out, f, start + i * step, step * 1.4, type, 0.18);
  });
}

function sweep(
  ctx: AudioContext,
  out: AudioNode,
  fromHz: number,
  toHz: number,
  start: number,
  duration: number,
  type: OscillatorType = "square",
  opts: { detune?: number } = {},
) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  if (opts.detune) osc.detune.value = opts.detune;
  osc.frequency.setValueAtTime(fromHz, start);
  osc.frequency.exponentialRampToValueAtTime(Math.max(20, toHz), start + duration);
  gain.gain.setValueAtTime(0, start);
  gain.gain.linearRampToValueAtTime(0.18, start + 0.005);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  osc.connect(gain).connect(out);
  osc.start(start);
  osc.stop(start + duration + 0.02);
}

function sparkle(ctx: AudioContext, out: AudioNode, start: number, duration: number) {
  // Random high-octave pings to suggest sparkle/confetti
  const choices = [1567.98, 1760, 2093, 2349.32, 2637.02];
  const count = 6;
  for (let i = 0; i < count; i++) {
    const t = start + (duration * i) / count + Math.random() * 0.04;
    const f = choices[Math.floor(Math.random() * choices.length)];
    tone(ctx, out, f, t, 0.12, "triangle", 0.1);
  }
}

function noiseBurst(ctx: AudioContext, out: AudioNode, start: number, duration: number) {
  const bufferSize = Math.floor(ctx.sampleRate * duration);
  const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < bufferSize; i++) {
    // Decaying noise — exponential envelope baked in
    const env = Math.pow(1 - i / bufferSize, 3);
    data[i] = (Math.random() * 2 - 1) * env;
  }
  const src = ctx.createBufferSource();
  const gain = ctx.createGain();
  src.buffer = buffer;
  gain.gain.value = 0.18;
  src.connect(gain).connect(out);
  src.start(start);
}

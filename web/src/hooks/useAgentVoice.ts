"use client";

/**
 * Per-agent TTS playback + lip-sync amplitude.
 *
 * Wire-up: the parent (useSwarm) calls `pushAudioEvent(event, data)` for
 * each `agent.speech_audio_*` message that arrives over the WS. We
 * accumulate base64-encoded mp3 chunks per (agent, seq), then on the
 * matching `_end` event we build a Blob, hand it to a per-agent
 * `<audio>` element, and route the element through a shared AudioContext
 * → AnalyserNode so callers can sample mouth amplitude on a render loop.
 *
 * The hook is a pure event consumer with no UI — it returns:
 *   - pushAudioEvent: ingestion entry point for the WS
 *   - getMouthAmplitude(agent): 0..1 envelope, sampled on demand (cheap)
 *   - speakingAgents: Set of names currently producing sound (for emote UI)
 *
 * Why no MediaSource? Safari does not support MSE for audio/mpeg, and
 * decoding-on-end keeps the implementation small. The cost is one
 * utterance of latency (~500ms typical for an Azure round-trip), which
 * is acceptable for VTuber dialogue. If we ever need sub-100ms playback
 * we can layer MSE in for Chrome/Firefox without changing the contract.
 */

import { useCallback, useEffect, useRef, useState } from "react";

type AudioEventName =
  | "agent.speech_audio_start"
  | "agent.speech_audio_chunk"
  | "agent.speech_audio_end"
  | "agent.speech_audio_dropped";

interface AudioEventData {
  agent?: string;
  seq?: number;
  index?: number;
  b64?: string;
  voice?: string;
  mood?: string;
  mime?: string;
  total_bytes?: number;
  reason?: string;
}

interface AgentSlot {
  /** Sequence of the in-flight utterance. Chunks with a different seq
   * are dropped (they belong to a superseded line). */
  seq: number;
  /** Accumulated mp3 bytes for the in-flight utterance, in order. */
  chunks: Uint8Array[];
  /** Persistent <audio> element so MediaElementSource only attaches once. */
  audio: HTMLAudioElement;
  /** Lazy — created the first time we connect this slot to the AudioContext. */
  source: MediaElementAudioSourceNode | null;
  analyser: AnalyserNode | null;
  /** Reused scratch buffer for time-domain reads. */
  buf: Uint8Array<ArrayBuffer> | null;
  /** Last sampled amplitude (0..1). Decays toward zero between samples. */
  amp: number;
  /** True between audio_start and audio.ended. Drives speakingAgents. */
  speaking: boolean;
}

function decodeBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export interface UseAgentVoiceResult {
  pushAudioEvent: (event: AudioEventName | string, data: AudioEventData) => void;
  getMouthAmplitude: (agent: string) => number;
  /** Names of agents whose audio is currently playing. Reactive — drives UI. */
  speakingAgents: Set<string>;
  /** Tear down everything (call on unmount or hard reset). */
  reset: () => void;
}

export function useAgentVoice(): UseAgentVoiceResult {
  const slotsRef = useRef<Map<string, AgentSlot>>(new Map());
  const ctxRef = useRef<AudioContext | null>(null);
  const [speakingAgents, setSpeakingAgents] = useState<Set<string>>(() => new Set());

  const ensureContext = useCallback((): AudioContext | null => {
    if (typeof window === "undefined") return null;
    if (ctxRef.current) return ctxRef.current;
    type WindowWithWebkit = Window & {
      webkitAudioContext?: typeof AudioContext;
    };
    const w = window as WindowWithWebkit;
    const Ctor = window.AudioContext ?? w.webkitAudioContext;
    if (!Ctor) return null;
    ctxRef.current = new Ctor();
    return ctxRef.current;
  }, []);

  const ensureSlot = useCallback((agent: string): AgentSlot => {
    const existing = slotsRef.current.get(agent);
    if (existing) return existing;
    const audio = new Audio();
    audio.preload = "auto";
    // Crossfade out the old src is unnecessary — we just overwrite on each
    // utterance. Loop must be off; controls hidden (it's never in the DOM).
    audio.loop = false;
    const slot: AgentSlot = {
      seq: 0,
      chunks: [],
      audio,
      source: null,
      analyser: null,
      buf: null,
      amp: 0,
      speaking: false,
    };
    slotsRef.current.set(agent, slot);
    return slot;
  }, []);

  const connectAnalyser = useCallback(
    (slot: AgentSlot) => {
      if (slot.source && slot.analyser) return;
      const ctx = ensureContext();
      if (!ctx) return;
      // Some browsers gate AudioContext until a user gesture. The first
      // start command is a click on "speak", which counts — but if we got
      // here pre-gesture, resume() is a no-op until then.
      if (ctx.state === "suspended") {
        void ctx.resume().catch(() => {});
      }
      try {
        const src = ctx.createMediaElementSource(slot.audio);
        const an = ctx.createAnalyser();
        an.fftSize = 256;
        an.smoothingTimeConstant = 0.6;
        src.connect(an);
        an.connect(ctx.destination);
        slot.source = src;
        slot.analyser = an;
        slot.buf = new Uint8Array(an.fftSize);
      } catch {
        // createMediaElementSource throws if called twice on the same
        // element — slot.source acts as the once-flag, so this catch is
        // only hit on truly unexpected failure (e.g. context closed).
      }
    },
    [ensureContext],
  );

  const setSpeaking = useCallback((agent: string, on: boolean) => {
    setSpeakingAgents((prev) => {
      const has = prev.has(agent);
      if (on === has) return prev;
      const next = new Set(prev);
      if (on) next.add(agent);
      else next.delete(agent);
      return next;
    });
  }, []);

  const pushAudioEvent = useCallback(
    (event: string, data: AudioEventData) => {
      const agent = data.agent;
      if (!agent) return;
      const slot = ensureSlot(agent);

      switch (event) {
        case "agent.speech_audio_start": {
          // A new utterance supersedes any in-flight chunks. Bumping seq
          // causes any straggling chunks for the old line to be dropped
          // by the chunk handler below.
          slot.seq = data.seq ?? slot.seq + 1;
          slot.chunks = [];
          // Don't stop the currently-playing audio yet — let it finish
          // the prior word so the cut-over doesn't sound jarring. The
          // new src will replace it when audio_end fires.
          break;
        }

        case "agent.speech_audio_chunk": {
          if (data.seq !== slot.seq) return; // orphan from a superseded line
          if (!data.b64) return;
          slot.chunks.push(decodeBase64(data.b64));
          break;
        }

        case "agent.speech_audio_end": {
          if (data.seq !== slot.seq) return;
          if (slot.chunks.length === 0) {
            // Stub provider yields no bytes; nothing to play, but we
            // still want a brief speaking flicker so the UI shows the
            // line was processed.
            setSpeaking(agent, true);
            window.setTimeout(() => setSpeaking(agent, false), 200);
            return;
          }
          // Concatenate into one Blob and hand off to the audio element.
          let total = 0;
          for (const c of slot.chunks) total += c.byteLength;
          const merged = new Uint8Array(total);
          let off = 0;
          for (const c of slot.chunks) {
            merged.set(c, off);
            off += c.byteLength;
          }
          slot.chunks = [];
          const mime = data.mime || "audio/mpeg";
          const blob = new Blob([merged.buffer], { type: mime });
          const url = URL.createObjectURL(blob);

          // Tear down any prior URL so we don't leak. It's safe to revoke
          // immediately after .play() resolves — the browser holds its own
          // reference to the underlying bytes.
          const prevSrc = slot.audio.src;
          slot.audio.src = url;
          slot.audio.onended = () => {
            slot.speaking = false;
            slot.amp = 0;
            setSpeaking(agent, false);
            URL.revokeObjectURL(url);
          };
          slot.audio.onerror = () => {
            slot.speaking = false;
            setSpeaking(agent, false);
            URL.revokeObjectURL(url);
          };
          // Connect the analyser only on first play — createMediaElementSource
          // can only be called once per element.
          connectAnalyser(slot);
          slot.speaking = true;
          setSpeaking(agent, true);
          slot.audio.play().catch(() => {
            // Autoplay rejected (no user gesture yet). We still emit the
            // speaking state briefly so the bubble shows.
            slot.speaking = false;
            setSpeaking(agent, false);
            URL.revokeObjectURL(url);
          });
          if (prevSrc && prevSrc.startsWith("blob:")) {
            // Revoke the previous URL after a short delay so any final
            // decode doesn't reach for a freed buffer.
            window.setTimeout(() => URL.revokeObjectURL(prevSrc), 1000);
          }
          break;
        }

        case "agent.speech_audio_dropped": {
          // Nothing to render — backend already chose to skip. Could log
          // here for telemetry but the events panel shows it already.
          break;
        }
      }
    },
    [connectAnalyser, ensureSlot, setSpeaking],
  );

  const getMouthAmplitude = useCallback((agent: string): number => {
    const slot = slotsRef.current.get(agent);
    if (!slot) return 0;
    if (!slot.speaking || !slot.analyser || !slot.buf) {
      // Decay so the mouth eases shut even if no events arrive.
      slot.amp *= 0.85;
      return slot.amp;
    }
    const buf = slot.buf;
    slot.analyser.getByteTimeDomainData(buf);
    // Time-domain bytes are centered at 128. RMS gives a reasonable
    // envelope for vowel/consonant energy without the cost of an FFT.
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / buf.length);
    // Boost a touch — speech RMS sits around 0.05–0.2; we want 0..1.
    const target = Math.min(1, rms * 4);
    // Smooth toward the target so single quiet samples don't snap the
    // mouth shut between phonemes.
    slot.amp = slot.amp * 0.5 + target * 0.5;
    return slot.amp;
  }, []);

  const reset = useCallback(() => {
    for (const slot of slotsRef.current.values()) {
      try {
        slot.audio.pause();
        slot.audio.removeAttribute("src");
        slot.audio.load();
      } catch {
        /* element may already be torn down */
      }
    }
    slotsRef.current.clear();
    setSpeakingAgents(new Set());
  }, []);

  useEffect(() => {
    return () => {
      reset();
      const ctx = ctxRef.current;
      if (ctx) {
        void ctx.close().catch(() => {});
        ctxRef.current = null;
      }
    };
  }, [reset]);

  return { pushAudioEvent, getMouthAmplitude, speakingAgents, reset };
}

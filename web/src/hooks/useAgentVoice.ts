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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
  /** Speak text directly via Web Speech API (browser built-in). Used when server TTS is not configured. */
  speakText: (agentName: string, text: string) => void;
  /**
   * TTS-independent "this agent is speaking" flag driven purely by the
   * text of the line. Sets ``speakingAgents`` true, oscillates fake
   * amplitude for lip-sync, and clears after a duration derived from
   * word count. Safe to call alongside server TTS / Web Speech — if
   * either of those fires first and sets real amplitude, the fake
   * oscillation won't overwrite it (we only write fake amp while the
   * slot.amp remains below a threshold).
   */
  markSpeakingFromText: (agentName: string, text: string) => void;
  /**
   * Unified speech entry point for the WS ``agent.speech`` event:
   * - immediately marks the agent as speaking (drives spotlight + VRM
   *   talking overlay),
   * - schedules Web Speech API playback with a short delay so that if
   *   server TTS audio lands within that window, Web Speech is skipped
   *   (avoids hearing the same line twice, out of sync).
   * The pending timer is cancelled from ``pushAudioEvent`` when an
   * ``agent.speech_audio_start`` arrives for the same agent.
   */
  requestSpeak: (agentName: string, text: string) => void;
  /** Drop a specific agent's resources (call on agent.despawned). */
  cleanupAgent: (agentName: string) => void;
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

  const pickWebSpeechVoice = useCallback((agentName: string): SpeechSynthesisVoice | null => {
    if (typeof window === "undefined") return null;
    const voices = window.speechSynthesis.getVoices();
    if (voices.length === 0) return null;
    // Hash agent name to pick a consistent voice
    let h = 5381;
    for (let i = 0; i < agentName.length; i++) {
      h = ((h << 5) + h + agentName.charCodeAt(i)) >>> 0;
    }
    return voices[h % voices.length];
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
          // Server TTS is about to take over — cancel any deferred Web
          // Speech kick-off for this agent and stop any utterance it
          // already started, so we never hear the same line twice.
          const pending = pendingWebSpeechRef.current.get(agent);
          if (pending) {
            clearTimeout(pending);
            pendingWebSpeechRef.current.delete(agent);
          }
          if (typeof window !== "undefined" && window.speechSynthesis) {
            window.speechSynthesis.cancel();
          }
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

  const speakText = useCallback((agentName: string, text: string) => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    // Don't cancel() globally — that fires onend on every in-flight
    // utterance and causes ``speakingAgents`` to flicker whenever two
    // agents speak in quick succession (onend clears the previous agent
    // while the new one hasn't fired onstart yet). Letting the browser
    // queue is visually better than the flicker.

    const slot = ensureSlot(agentName);
    const utterance = new SpeechSynthesisUtterance(text);

    // Deterministic pitch/rate so each agent sounds slightly different
    let h = 5381;
    for (let i = 0; i < agentName.length; i++) {
      h = ((h << 5) + h + agentName.charCodeAt(i)) >>> 0;
    }
    utterance.pitch = 0.85 + ((h % 30) / 100);  // 0.85 – 1.14
    utterance.rate  = 0.95 + ((h % 15) / 100);  // 0.95 – 1.09
    utterance.volume = 0.9;

    const voice = pickWebSpeechVoice(agentName);
    if (voice) utterance.voice = voice;

    // Fake amplitude oscillation for lip-sync while speaking
    let fakeAmpTimer: ReturnType<typeof setInterval> | null = null;

    utterance.onstart = () => {
      slot.speaking = true;
      slot.amp = 0;
      setSpeaking(agentName, true);
      // Oscillate amp to drive mouth animation
      // Smoothly interpolate toward a random target amplitude so the
    // lip-sync envelope eases in and out rather than jumping. This
    // prevents the sharp amplitude spikes that triggered rapid head-nods.
    let fakeTarget = 0.3;
    fakeAmpTimer = setInterval(() => {
        if (!slot.speaking) {
          if (fakeAmpTimer) clearInterval(fakeAmpTimer);
          return;
        }
        fakeTarget = 0.1 + Math.random() * 0.45;
        slot.amp = slot.amp * 0.55 + fakeTarget * 0.45; // smooth blend
      }, 120);
    };

    utterance.onend = () => {
      if (fakeAmpTimer) clearInterval(fakeAmpTimer);
      slot.speaking = false;
      slot.amp = 0;
      setSpeaking(agentName, false);
    };

    utterance.onerror = () => {
      if (fakeAmpTimer) clearInterval(fakeAmpTimer);
      slot.speaking = false;
      slot.amp = 0;
      setSpeaking(agentName, false);
    };

    window.speechSynthesis.speak(utterance);
  }, [ensureSlot, setSpeaking, pickWebSpeechVoice]);

  // Per-agent fallback timers for markSpeakingFromText. Kept in a ref so
  // each call can cancel the prior timer/interval without rerendering.
  // `number` matches the DOM-typed `window.setTimeout` / `setInterval`
  // return types; avoid `ReturnType<typeof setTimeout>` which resolves to
  // `NodeJS.Timeout` when @types/node is present and fails the assignment.
  const fallbackTimersRef = useRef<
    Map<string, { clear: number; amp: number }>
  >(new Map());

  // Pending Web Speech kick-off timers — keyed by agent. Lets
  // ``pushAudioEvent`` cancel the browser TTS the instant server TTS
  // audio arrives, avoiding double-speak.
  const pendingWebSpeechRef = useRef<Map<string, number>>(new Map());

  const markSpeakingFromText = useCallback(
    (agentName: string, text: string) => {
      if (!agentName || !text) return;
      const slot = ensureSlot(agentName);
      // Duration estimate: ~3.2 words/sec is a comfortable talking cadence.
      // Floor at 900ms so even one-word lines produce a visible speak flash.
      const wordCount = Math.max(1, text.trim().split(/\s+/).length);
      const durationMs = Math.max(900, Math.min(8000, (wordCount / 3.2) * 1000));

      // Cancel any in-flight fallback for this agent before starting new.
      const prior = fallbackTimersRef.current.get(agentName);
      if (prior) {
        clearInterval(prior.amp);
        clearTimeout(prior.clear);
      }

      slot.speaking = true;
      setSpeaking(agentName, true);

      // Fake amplitude oscillation. Yields only when the real analyser
      // isn't already producing a stronger signal, so when server TTS
      // audio lands mid-line the real envelope takes over cleanly.
      let target = 0.25;
      const ampTimer = setInterval(() => {
        target = 0.12 + Math.random() * 0.4;
        if (slot.amp < 0.55) {
          slot.amp = slot.amp * 0.55 + target * 0.45;
        }
      }, 130);

      const clearTimer = window.setTimeout(() => {
        clearInterval(ampTimer);
        fallbackTimersRef.current.delete(agentName);
        // Only drop the speaking flag if no other source (audio element
        // or Web Speech) is currently holding it true.
        if (!slot.audio.src || slot.audio.paused || slot.audio.ended) {
          slot.speaking = false;
          slot.amp = 0;
          setSpeaking(agentName, false);
        }
      }, durationMs);

      fallbackTimersRef.current.set(agentName, {
        clear: clearTimer,
        amp: ampTimer,
      });
    },
    [ensureSlot, setSpeaking],
  );

  const requestSpeak = useCallback(
    (agentName: string, text: string) => {
      if (!agentName || !text) return;
      // Instant UI feedback.
      markSpeakingFromText(agentName, text);
      // Defer Web Speech by ~400ms so server TTS audio_start (which
      // cancels the pending timer) usually wins the race on setups that
      // have server TTS configured.
      const prior = pendingWebSpeechRef.current.get(agentName);
      if (prior !== undefined) window.clearTimeout(prior);
      const t = window.setTimeout(() => {
        pendingWebSpeechRef.current.delete(agentName);
        speakText(agentName, text);
      }, 400);
      pendingWebSpeechRef.current.set(agentName, t);
    },
    [markSpeakingFromText, speakText],
  );

  const cleanupAgent = useCallback((agentName: string) => {
    const slot = slotsRef.current.get(agentName);
    if (slot) {
      try {
        slot.audio.pause();
        const src = slot.audio.src;
        slot.audio.removeAttribute("src");
        slot.audio.load();
        if (src && src.startsWith("blob:")) URL.revokeObjectURL(src);
      } catch {
        /* element may already be torn down */
      }
      slotsRef.current.delete(agentName);
    }
    const fallback = fallbackTimersRef.current.get(agentName);
    if (fallback) {
      clearInterval(fallback.amp);
      clearTimeout(fallback.clear);
      fallbackTimersRef.current.delete(agentName);
    }
    const pending = pendingWebSpeechRef.current.get(agentName);
    if (pending) {
      clearTimeout(pending);
      pendingWebSpeechRef.current.delete(agentName);
    }
    setSpeakingAgents((prev) => {
      if (!prev.has(agentName)) return prev;
      const next = new Set(prev);
      next.delete(agentName);
      return next;
    });
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
    for (const t of fallbackTimersRef.current.values()) {
      clearInterval(t.amp);
      clearTimeout(t.clear);
    }
    fallbackTimersRef.current.clear();
    for (const t of pendingWebSpeechRef.current.values()) {
      clearTimeout(t);
    }
    pendingWebSpeechRef.current.clear();
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

  // Memoize the returned object so consumers that include this value in
  // effect/callback deps (e.g. useSwarm's handleMessage → connect) don't
  // see a fresh reference on every render — which previously caused the
  // WebSocket to be torn down and re-created in a tight loop.
  return useMemo(
    () => ({
      pushAudioEvent,
      getMouthAmplitude,
      speakingAgents,
      reset,
      speakText,
      markSpeakingFromText,
      requestSpeak,
      cleanupAgent,
    }),
    [
      pushAudioEvent,
      getMouthAmplitude,
      speakingAgents,
      reset,
      speakText,
      markSpeakingFromText,
      requestSpeak,
      cleanupAgent,
    ],
  );
}

"use client";

import { useCallback, useEffect, useRef } from "react";

import { BGMEngine, type Mood, type Pulse } from "@/lib/bgm";

// Public event shape consumed by the hook. `id` must be monotonic /
// unique per emission so the hook can dedupe — we track the highest
// id we've ever played and skip anything ≤ it. `kind` maps directly
// to BGMEngine.pulse().
export interface BGMEvent {
  id: number | string;
  kind: Pulse;
}

interface UseBGMOptions {
  enabled: boolean;
  // Free-form mood string from the swarm. We map anything we don't
  // recognise to "calm" so adding new swarm moods is non-breaking.
  mood: string;
  events?: BGMEvent[];
  // 0..1 master volume. Defaults to 0.35.
  volume?: number;
}

export interface UseBGMReturn {
  start: () => void;
  stop: () => void;
  setVolume: (v: number) => void;
}

// Map the swarm's mood vocabulary to one of three BGM layers. Anything
// not explicitly enumerated falls through to "calm".
export function moodToLayer(mood: string): Mood {
  switch (mood.toLowerCase()) {
    case "happy":
    case "excited":
      return "focus";
    case "tired":
    case "frustrated":
    case "worried":
      return "tension";
    default:
      return "calm";
  }
}

export function useBGM(opts: UseBGMOptions): UseBGMReturn {
  const engineRef = useRef<BGMEngine | null>(null);
  // Track which event ids we've already pulsed for. Compared with `>`
  // for numeric ids; falls back to a Set for string ids.
  const lastSeenNumIdRef = useRef<number>(-Infinity);
  const seenStringIdsRef = useRef<Set<string>>(new Set());
  const startedRef = useRef(false);

  // Lazy-init the engine on first render in the browser. The engine
  // itself is dormant until `start()` is called from a user gesture.
  if (engineRef.current === null && typeof window !== "undefined") {
    engineRef.current = new BGMEngine();
  }

  const start = useCallback(() => {
    if (!engineRef.current) return;
    engineRef.current.start();
    startedRef.current = true;
    if (typeof opts.volume === "number") {
      engineRef.current.setMasterVolume(opts.volume);
    }
  }, [opts.volume]);

  const stop = useCallback(() => {
    if (!engineRef.current) return;
    engineRef.current.stop();
    startedRef.current = false;
  }, []);

  const setVolume = useCallback((v: number) => {
    engineRef.current?.setMasterVolume(v);
  }, []);

  // Auto start/stop in response to the `enabled` prop. Note: calling
  // `start()` here only succeeds if the parent flipped `enabled` from
  // inside a user gesture (which BGMToggle does). Otherwise the
  // AudioContext stays suspended until the user interacts.
  useEffect(() => {
    if (!engineRef.current) return;
    if (opts.enabled && !startedRef.current) {
      engineRef.current.start();
      startedRef.current = true;
    } else if (!opts.enabled && startedRef.current) {
      engineRef.current.stop();
      startedRef.current = false;
    }
  }, [opts.enabled]);

  // Push mood changes into the engine. Cheap — a no-op if nothing's
  // started.
  useEffect(() => {
    if (!engineRef.current || !startedRef.current) return;
    engineRef.current.setMood(moodToLayer(opts.mood));
  }, [opts.mood]);

  // Volume changes.
  useEffect(() => {
    if (!engineRef.current) return;
    if (typeof opts.volume === "number") {
      engineRef.current.setMasterVolume(opts.volume);
    }
  }, [opts.volume]);

  // Drain new events. We don't replay anything we've already seen.
  useEffect(() => {
    if (!engineRef.current || !startedRef.current) return;
    const events = opts.events;
    if (!events || events.length === 0) return;
    for (const ev of events) {
      if (typeof ev.id === "number") {
        if (ev.id <= lastSeenNumIdRef.current) continue;
        lastSeenNumIdRef.current = ev.id;
      } else {
        if (seenStringIdsRef.current.has(ev.id)) continue;
        seenStringIdsRef.current.add(ev.id);
      }
      engineRef.current.pulse(ev.kind);
    }
  }, [opts.events]);

  // On unmount, make sure the AudioContext is released.
  useEffect(() => {
    return () => {
      engineRef.current?.stop();
      engineRef.current = null;
    };
  }, []);

  return { start, stop, setVolume };
}

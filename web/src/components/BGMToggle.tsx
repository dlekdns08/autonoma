"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useBGM, type BGMEvent } from "@/hooks/useBGM";

// Small fixed-position mute toggle + hover-revealed volume slider.
// Persists `enabled` and `volume` in localStorage under a single
// key so the user's preference survives reload.

const STORAGE_KEY = "autonoma:bgm";

interface StoredPrefs {
  enabled: boolean;
  volume: number;
}

const DEFAULT_PREFS: StoredPrefs = {
  // Default off — BGM only starts after the user clicks (which both
  // satisfies the user-gesture requirement and respects users who
  // never wanted music in the first place).
  enabled: false,
  volume: 0.35,
};

function readPrefs(): StoredPrefs {
  if (typeof window === "undefined") return DEFAULT_PREFS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw) as Partial<StoredPrefs>;
    return {
      enabled: typeof parsed.enabled === "boolean" ? parsed.enabled : DEFAULT_PREFS.enabled,
      volume:
        typeof parsed.volume === "number" && parsed.volume >= 0 && parsed.volume <= 1
          ? parsed.volume
          : DEFAULT_PREFS.volume,
    };
  } catch {
    return DEFAULT_PREFS;
  }
}

function writePrefs(prefs: StoredPrefs): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // localStorage may be disabled — silently ignore.
  }
}

interface BGMToggleProps {
  // Current swarm mood. Free-form; the hook maps it to a layer.
  mood?: string;
  // Stream of one-shot events (boss spawn, raid victory, etc.).
  events?: BGMEvent[];
}

export default function BGMToggle({ mood = "calm", events }: BGMToggleProps) {
  // Hydrated state — start with defaults so SSR + client agree on the
  // first render, then load from localStorage in an effect.
  const [enabled, setEnabled] = useState<boolean>(DEFAULT_PREFS.enabled);
  const [volume, setVolume] = useState<number>(DEFAULT_PREFS.volume);
  const [hover, setHover] = useState(false);
  const hydratedRef = useRef(false);

  useEffect(() => {
    const prefs = readPrefs();
    setEnabled(prefs.enabled);
    setVolume(prefs.volume);
    hydratedRef.current = true;
  }, []);

  // Persist on change (post-hydration only — avoids overwriting stored
  // prefs with defaults during the first render).
  useEffect(() => {
    if (!hydratedRef.current) return;
    writePrefs({ enabled, volume });
  }, [enabled, volume]);

  useBGM({ enabled, mood, events, volume });

  const toggle = useCallback(() => {
    // Click handler — runs inside a user gesture, so the BGMEngine's
    // AudioContext can resume immediately when we flip enabled on.
    setEnabled((prev) => !prev);
  }, []);

  return (
    <div
      // Pinned to bottom-right; on narrow screens (< sm) we drop the
      // slider entirely (hover doesn't exist on touch) and shift the
      // button up so it doesn't collide with the watch-page betting
      // widget or the chat panel's typing indicator. Use ``pointer-events:auto``
      // explicitly so the wrapper doesn't block any underlying overlays.
      className="pointer-events-auto fixed bottom-2 right-2 z-50 flex items-center gap-2 sm:bottom-2 max-sm:bottom-16"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {/* Volume slider — slides in from the right on hover when BGM
          is enabled. Hidden when muted to keep the UI uncluttered. */}
      <div
        className={`overflow-hidden transition-all duration-200 ${
          hover && enabled ? "w-28 opacity-100" : "w-0 opacity-0"
        }`}
      >
        <input
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={volume}
          onChange={(e) => setVolume(parseFloat(e.target.value))}
          aria-label="BGM volume"
          className="w-full accent-violet-500"
        />
      </div>

      <button
        type="button"
        onClick={toggle}
        title={enabled ? "Mute BGM" : "Unmute BGM"}
        aria-label={enabled ? "Mute background music" : "Unmute background music"}
        aria-pressed={enabled}
        className="flex h-7 w-7 items-center justify-center rounded-full border border-white/10 bg-slate-950/80 text-sm backdrop-blur-sm transition-colors hover:border-violet-500/40 hover:bg-slate-900/90"
        style={{ boxShadow: "0 0 8px rgba(139,92,246,0.08)" }}
      >
        {enabled ? "🔊" : "🔇"}
      </button>
    </div>
  );
}

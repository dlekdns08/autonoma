"use client";

/**
 * Feature #6 — Multi-viewer cursors + stickers.
 *
 * Drop this absolutely-positioned overlay into any host page that has
 * a stage area (e.g. ``/watch/[code]``). It captures local mousemove
 * inside its own bounding box and broadcasts normalised coordinates
 * via ``sendCommand``; remote viewers' cursors and stickers come back
 * in through ``remote`` (typically from ``useViewerOverlay``).
 *
 * Animations are pure React — a ``requestAnimationFrame`` loop drives
 * a render-counter so per-sticker progress is recomputed each frame.
 * No styled-jsx, no external animation libs. Tailwind v4 is used for
 * static styling; the time-varying transforms live in ``style``.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RemoteOverlayState } from "@/lib/viewerOverlayTypes";

export interface ViewerOverlayProps {
  /** Stable id for *this* viewer (echoed in outgoing messages). */
  viewerId: string;
  /** Display name attached to outgoing cursor + sticker payloads. */
  displayName: string;
  /** Sends a JSON-serialisable command over the room WebSocket. */
  sendCommand: (cmd: object) => void;
  /** Remote-cursor + sticker state from ``useViewerOverlay``. */
  remote: RemoteOverlayState;
}

/** Sticker palette — kept short so the bar fits on a phone. */
const STICKERS: readonly string[] = [
  "✨",
  "💥",
  "❤️",
  "🌟",
  "🔥",
  "🎉",
  "👏",
  "🎯",
];

/** Cursor send throttle — ~30 Hz as specified. */
const CURSOR_SEND_INTERVAL_MS = 1000 / 30;

/** Cursors not refreshed within this window fade out, then disappear. */
const CURSOR_FADE_AFTER_MS = 5_000;
const CURSOR_HARD_CUTOFF_MS = 6_000;

/** Sticker animation duration (ms). */
const STICKER_ANIM_MS = 1_500;

/** Locally-spawned sticker (immediate visual feedback before WS round-trip). */
interface LocalSticker {
  id: string;
  emoji: string;
  x: number;
  y: number;
  startedAt: number;
}

export default function ViewerOverlay({
  viewerId,
  displayName,
  sendCommand,
  remote,
}: ViewerOverlayProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Local stickers are rendered in addition to remote ones so the
  // clicker doesn't wait a network hop to see their own emoji fly.
  const [localStickers, setLocalStickers] = useState<LocalSticker[]>([]);

  // ── Mousemove → throttled cursor broadcast ───────────────────────
  const lastSendRef = useRef<number>(0);
  const pendingMoveRef = useRef<{ x: number; y: number } | null>(null);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flushCursor = useCallback(() => {
    flushTimerRef.current = null;
    const pending = pendingMoveRef.current;
    pendingMoveRef.current = null;
    if (!pending) return;
    lastSendRef.current = performance.now();
    sendCommand({
      command: "viewer_overlay",
      kind: "cursor",
      x: pending.x,
      y: pending.y,
      viewerId,
      displayName,
    });
  }, [sendCommand, viewerId, displayName]);

  const handleMouseMove = useCallback(
    (ev: React.MouseEvent<HTMLDivElement>) => {
      const root = rootRef.current;
      if (!root) return;
      const rect = root.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const x = clamp01((ev.clientX - rect.left) / rect.width);
      const y = clamp01((ev.clientY - rect.top) / rect.height);

      pendingMoveRef.current = { x, y };
      const now = performance.now();
      const elapsed = now - lastSendRef.current;
      if (elapsed >= CURSOR_SEND_INTERVAL_MS) {
        flushCursor();
        return;
      }
      if (flushTimerRef.current === null) {
        flushTimerRef.current = setTimeout(
          flushCursor,
          CURSOR_SEND_INTERVAL_MS - elapsed,
        );
      }
    },
    [flushCursor],
  );

  // Cancel any pending throttle on unmount so we don't fire after teardown.
  useEffect(() => {
    return () => {
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
    };
  }, []);

  // ── rAF tick: drives sticker animations + cursor fade without
  // forcing a global state update for every frame. We bump a counter
  // and read ``performance.now()`` during render. The loop stops when
  // there's nothing animating to avoid a permanent wakeup. ──────────
  const [, setFrameTick] = useState<number>(0);
  const cursorList = useMemo(
    () => Object.values(remote.cursors).filter((c) => c.viewerId !== viewerId),
    [remote.cursors, viewerId],
  );
  const hasAnimating =
    cursorList.length > 0 ||
    remote.stickers.length > 0 ||
    localStickers.length > 0;

  useEffect(() => {
    if (!hasAnimating) return;
    let raf = 0;
    const loop = () => {
      setFrameTick((n) => (n + 1) & 0xffff);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [hasAnimating]);

  // Sweep local stickers once their animation completes.
  useEffect(() => {
    if (localStickers.length === 0) return;
    const handle = setInterval(() => {
      const now = performance.now();
      setLocalStickers((prev) =>
        prev.filter((s) => now - s.startedAt < STICKER_ANIM_MS),
      );
    }, 250);
    return () => clearInterval(handle);
  }, [localStickers.length]);

  // ── Sticker click: locally animate + broadcast ───────────────────
  const handleStickerClick = useCallback(
    (emoji: string) => {
      // Random landing spot, biased to the centre band so stickers
      // don't pile up under the sticker bar.
      const x = 0.15 + Math.random() * 0.7;
      const y = 0.2 + Math.random() * 0.5;
      const now = performance.now();
      const id =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `${viewerId}-${now}-${Math.random()}`;
      setLocalStickers((prev) => [
        ...prev,
        { id, emoji, x, y, startedAt: now },
      ]);
      sendCommand({
        command: "viewer_overlay",
        kind: "sticker",
        emoji,
        x,
        y,
        viewerId,
        displayName,
      });
    },
    [sendCommand, viewerId, displayName],
  );

  const now = performance.now();

  return (
    <div
      ref={rootRef}
      onMouseMove={handleMouseMove}
      // The overlay must let normal stage interactions through. We
      // capture mousemove on the root (which still bubbles), but
      // children that need clicks (sticker bar) re-enable pointer events.
      className="pointer-events-auto absolute inset-0 select-none"
      aria-hidden="true"
    >
      {/* ── Remote cursors ─────────────────────────────────────────── */}
      {cursorList.map((c) => {
        const age = now - c.lastSeen;
        if (age > CURSOR_HARD_CUTOFF_MS) return null;
        // Linear fade across the last second before the hard cutoff.
        const opacity =
          age <= CURSOR_FADE_AFTER_MS
            ? 1
            : Math.max(
                0,
                1 -
                  (age - CURSOR_FADE_AFTER_MS) /
                    (CURSOR_HARD_CUTOFF_MS - CURSOR_FADE_AFTER_MS),
              );
        return (
          <div
            key={c.viewerId}
            className="pointer-events-none absolute -translate-x-1 -translate-y-1"
            style={{
              left: `${c.x * 100}%`,
              top: `${c.y * 100}%`,
              opacity,
              transition: "left 80ms linear, top 80ms linear",
            }}
          >
            <CursorIcon color={colorForViewer(c.viewerId)} />
            <span
              className="ml-3 mt-0.5 inline-block whitespace-nowrap rounded-md px-1.5 py-0.5 font-mono text-[10px] text-white shadow"
              style={{ backgroundColor: colorForViewer(c.viewerId) }}
            >
              {c.displayName || "viewer"}
            </span>
          </div>
        );
      })}

      {/* ── Stickers (remote + local) ──────────────────────────────── */}
      {remote.stickers.map((s) => {
        const t = (now - s.startedAt) / STICKER_ANIM_MS;
        if (t >= 1) return null;
        return (
          <StickerSprite
            key={s.id}
            emoji={s.emoji}
            x={s.x}
            y={s.y}
            progress={t}
          />
        );
      })}
      {localStickers.map((s) => {
        const t = (now - s.startedAt) / STICKER_ANIM_MS;
        if (t >= 1) return null;
        return (
          <StickerSprite
            key={s.id}
            emoji={s.emoji}
            x={s.x}
            y={s.y}
            progress={t}
          />
        );
      })}

      {/* ── Sticker bar ────────────────────────────────────────────── */}
      <div className="pointer-events-auto absolute bottom-3 left-1/2 flex -translate-x-1/2 gap-1 rounded-full border border-white/10 bg-black/55 px-2 py-1 backdrop-blur">
        {STICKERS.map((emoji) => (
          <button
            key={emoji}
            type="button"
            onClick={() => handleStickerClick(emoji)}
            className="grid h-8 w-8 place-items-center rounded-full text-base transition hover:bg-white/10 active:scale-90"
            aria-label={`Send ${emoji} sticker`}
          >
            <span aria-hidden="true">{emoji}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── helpers ─────────────────────────────────────────────────────────

function clamp01(v: number): number {
  if (Number.isNaN(v)) return 0;
  if (v < 0) return 0;
  if (v > 1) return 1;
  return v;
}

/** Stable hash → hue so each viewer gets a consistent label colour. */
function colorForViewer(viewerId: string): string {
  let h = 0;
  for (let i = 0; i < viewerId.length; i += 1) {
    h = (h * 31 + viewerId.charCodeAt(i)) | 0;
  }
  const hue = Math.abs(h) % 360;
  return `hsl(${hue}, 70%, 55%)`;
}

interface CursorIconProps {
  color: string;
}

function CursorIcon({ color }: CursorIconProps) {
  // Classic arrow cursor, tinted per-viewer.
  return (
    <svg
      width={18}
      height={18}
      viewBox="0 0 18 18"
      className="drop-shadow"
      aria-hidden="true"
    >
      <path
        d="M2 1.5 L2 14 L5.7 10.6 L8.1 16 L10.5 14.9 L8.1 9.7 L13 9.7 Z"
        fill={color}
        stroke="white"
        strokeWidth={1}
        strokeLinejoin="round"
      />
    </svg>
  );
}

interface StickerSpriteProps {
  emoji: string;
  /** Normalised origin x. */
  x: number;
  /** Normalised origin y. */
  y: number;
  /** 0..1 anim progress. */
  progress: number;
}

function StickerSprite({ emoji, x, y, progress }: StickerSpriteProps) {
  // Rise 80px over the lifetime, ease-out, fade in fast then out slow.
  const eased = 1 - (1 - progress) * (1 - progress);
  const dy = -80 * eased;
  const fadeIn = Math.min(1, progress / 0.1);
  const fadeOut = 1 - Math.max(0, (progress - 0.6) / 0.4);
  const opacity = Math.max(0, Math.min(fadeIn, fadeOut));
  const scale = 0.6 + 0.7 * eased;
  return (
    <div
      className="pointer-events-none absolute select-none text-3xl"
      style={{
        left: `${x * 100}%`,
        top: `${y * 100}%`,
        transform: `translate(-50%, calc(-50% + ${dy}px)) scale(${scale})`,
        opacity,
      }}
    >
      <span aria-hidden="true">{emoji}</span>
    </div>
  );
}

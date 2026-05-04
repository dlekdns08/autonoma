"use client";

/**
 * Feature #6 — Multi-viewer cursors + stickers.
 *
 * Subscribes to a live WebSocket and surfaces other viewers' cursor
 * positions and recent sticker bursts. Outgoing cursor messages are
 * throttled to ~60 Hz (16 ms) since the server fans them out to every
 * room member; stickers are unthrottled because they're rare clicks.
 *
 * The hook never owns the socket — it ``addEventListener``s on whatever
 * WS is passed in, so it composes cleanly with the existing
 * ``useSwarm`` connection. Pass ``null`` while the socket is still
 * connecting.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  isOverlayMsg,
  type OverlayMsg,
  type RemoteOverlayState,
  type RemoteSticker,
} from "@/lib/viewerOverlayTypes";

/** Cursors older than this are dropped from state. */
const CURSOR_TTL_MS = 6_000;
/** Stickers older than this are dropped (matches the 1.5s fly-anim with slack). */
const STICKER_TTL_MS = 2_000;
/** Outgoing cursor send throttle. */
const CURSOR_SEND_INTERVAL_MS = 16;
/** How often we sweep stale entries out of state. */
const PRUNE_INTERVAL_MS = 1_000;

export interface UseViewerOverlayResult {
  state: RemoteOverlayState;
  /** Send any overlay command. Cursor sends are throttled; stickers pass through. */
  send: (msg: OverlayMsg) => void;
}

const EMPTY_STATE: RemoteOverlayState = { cursors: {}, stickers: [] };

export function useViewerOverlay(ws: WebSocket | null): UseViewerOverlayResult {
  const [state, setState] = useState<RemoteOverlayState>(EMPTY_STATE);

  // Refs for the throttled send path so we don't re-bind ``send`` on every state tick.
  const wsRef = useRef<WebSocket | null>(ws);
  const lastCursorSendRef = useRef<number>(0);
  const pendingCursorRef = useRef<OverlayMsg | null>(null);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Keep the latest socket reachable from inside ``send`` without re-creating
  // the callback (which would tear down listeners on the consumer side).
  useEffect(() => {
    wsRef.current = ws;
  }, [ws]);

  // ── Inbound: dispatch overlay messages into local state ───────────
  useEffect(() => {
    if (!ws) return;

    const onMessage = (ev: MessageEvent) => {
      // The WS multiplexes lots of commands; drop anything not JSON.
      if (typeof ev.data !== "string") return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (!isOverlayMsg(parsed)) return;
      const msg: OverlayMsg = parsed;

      const now = performance.now();
      if (msg.kind === "cursor") {
        setState((prev) => ({
          cursors: {
            ...prev.cursors,
            [msg.viewerId]: {
              viewerId: msg.viewerId,
              displayName: msg.displayName,
              x: msg.x,
              y: msg.y,
              lastSeen: now,
            },
          },
          stickers: prev.stickers,
        }));
      } else {
        // ``msg.kind === "sticker"``
        const sticker: RemoteSticker = {
          // ``crypto.randomUUID`` exists in modern browsers; fall back so the
          // hook stays resilient inside older webviews.
          id:
            typeof crypto !== "undefined" && "randomUUID" in crypto
              ? crypto.randomUUID()
              : `${msg.viewerId}-${now}-${Math.random()}`,
          viewerId: msg.viewerId,
          displayName: msg.displayName,
          emoji: msg.emoji,
          x: msg.x,
          y: msg.y,
          startedAt: now,
        };
        setState((prev) => ({
          cursors: prev.cursors,
          stickers: [...prev.stickers, sticker],
        }));
      }
    };

    ws.addEventListener("message", onMessage);
    return () => {
      ws.removeEventListener("message", onMessage);
    };
  }, [ws]);

  // ── Background prune so stale cursors fade out and sticker arrays
  // don't grow without bound. We compare against ``performance.now()``
  // rather than wall time to stay monotonic across system clock drift.
  useEffect(() => {
    const handle = setInterval(() => {
      setState((prev) => {
        const now = performance.now();
        let cursorsChanged = false;
        const nextCursors: Record<string, (typeof prev.cursors)[string]> = {};
        for (const [id, cursor] of Object.entries(prev.cursors)) {
          if (now - cursor.lastSeen <= CURSOR_TTL_MS) {
            nextCursors[id] = cursor;
          } else {
            cursorsChanged = true;
          }
        }
        const nextStickers = prev.stickers.filter(
          (s) => now - s.startedAt <= STICKER_TTL_MS,
        );
        const stickersChanged = nextStickers.length !== prev.stickers.length;
        if (!cursorsChanged && !stickersChanged) return prev;
        return {
          cursors: cursorsChanged ? nextCursors : prev.cursors,
          stickers: stickersChanged ? nextStickers : prev.stickers,
        };
      });
    }, PRUNE_INTERVAL_MS);
    return () => clearInterval(handle);
  }, []);

  // ── Outbound: throttle cursors, pass stickers through ────────────
  const flushCursor = useCallback(() => {
    flushTimerRef.current = null;
    const pending = pendingCursorRef.current;
    pendingCursorRef.current = null;
    if (!pending) return;
    const sock = wsRef.current;
    if (!sock || sock.readyState !== WebSocket.OPEN) return;
    try {
      sock.send(JSON.stringify(pending));
      lastCursorSendRef.current = performance.now();
    } catch {
      // Socket might have closed between the readyState check and send;
      // swallow — the consumer will see a disconnect through their own hook.
    }
  }, []);

  const send = useCallback(
    (msg: OverlayMsg) => {
      const sock = wsRef.current;
      if (!sock || sock.readyState !== WebSocket.OPEN) return;

      if (msg.kind === "sticker") {
        try {
          sock.send(JSON.stringify(msg));
        } catch {
          // Same rationale as flushCursor: drop silently on a closed socket.
        }
        return;
      }

      // Cursor path: ensure at most one send per ``CURSOR_SEND_INTERVAL_MS``.
      pendingCursorRef.current = msg;
      const now = performance.now();
      const elapsed = now - lastCursorSendRef.current;
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

  // Cancel any pending throttle timer on unmount so we don't fire after teardown.
  useEffect(() => {
    return () => {
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
    };
  }, []);

  return { state, send };
}

/**
 * Feature #6 — Multi-viewer cursors + stickers.
 *
 * Wire format for the ``viewer_overlay`` WS broadcast. The server
 * fan-outs whatever clients send under ``command="viewer_overlay"``
 * verbatim, so the schema lives entirely on the frontend. Keep the
 * payload tiny — a 30 Hz cursor channel multiplied by every viewer in
 * a popular room adds up fast.
 *
 * Coordinates are normalised (0..1, 0..1) over the overlay's bounding
 * box. The receiver scales back to its own box, so a viewer on a phone
 * and a viewer on a desktop see each other's cursors in the same
 * relative spot regardless of stage size.
 */

/** Discriminator value baked into every overlay message. */
export const VIEWER_OVERLAY_COMMAND = "viewer_overlay" as const;

/** Live cursor position from one viewer. Throttled to ~30 Hz. */
export interface CursorMsg {
  command: typeof VIEWER_OVERLAY_COMMAND;
  kind: "cursor";
  /** Stable id for the sending viewer. Usually a session uuid. */
  viewerId: string;
  /** Display label drawn next to the remote cursor. */
  displayName: string;
  /** Normalised x in [0, 1]. */
  x: number;
  /** Normalised y in [0, 1]. */
  y: number;
}

/** One-shot emoji sticker thrown onto the stage. Animates then fades. */
export interface StickerMsg {
  command: typeof VIEWER_OVERLAY_COMMAND;
  kind: "sticker";
  viewerId: string;
  displayName: string;
  emoji: string;
  /** Normalised x in [0, 1]. */
  x: number;
  /** Normalised y in [0, 1]. */
  y: number;
}

/** Discriminated union — switch on ``msg.kind``. */
export type OverlayMsg = CursorMsg | StickerMsg;

/** Type guard for blind WS payloads. */
export function isOverlayMsg(value: unknown): value is OverlayMsg {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (v.command !== VIEWER_OVERLAY_COMMAND) return false;
  if (typeof v.viewerId !== "string") return false;
  if (typeof v.displayName !== "string") return false;
  if (typeof v.x !== "number" || typeof v.y !== "number") return false;
  if (v.kind === "cursor") return true;
  if (v.kind === "sticker") return typeof v.emoji === "string";
  return false;
}

/** State shape exposed by ``useViewerOverlay`` and consumed by the component. */
export interface RemoteCursor {
  viewerId: string;
  displayName: string;
  /** Normalised. */
  x: number;
  /** Normalised. */
  y: number;
  /** ``performance.now()`` of the last update. Used for fade + prune. */
  lastSeen: number;
}

export interface RemoteSticker {
  /** Locally-generated id so we can key the React list. */
  id: string;
  viewerId: string;
  displayName: string;
  emoji: string;
  x: number;
  y: number;
  /** ``performance.now()`` when this sticker was received. */
  startedAt: number;
}

export interface RemoteOverlayState {
  cursors: Record<string, RemoteCursor>;
  stickers: RemoteSticker[];
}

"use client";

/**
 * Phase 1-X — mobile-first read-only viewer for an Autonoma room.
 *
 *   /watch/<CODE>
 *
 * The dashboard at ``/`` is a desk-class UI: tasks panel, file tree,
 * chat composer, harness controls. On a phone, none of that fits and
 * none of it is what a viewer needs anyway. This route ships the
 * spectator-mode subset:
 *
 *   - VTuber spotlight up top (responsive, 16:9 cap)
 *   - 2D pixel map below (square cap)
 *   - Chat overlay docked at the bottom
 *   - Auto-join the room from the URL slug; no auth modal blocking the
 *     stage if the user isn't logged in (the WS ``join_room`` command
 *     works for any session — admin-gated commands aren't sent here).
 *
 * No controls, no composer, no record button. Audio plays through the
 * existing TTS pipeline so phone viewers hear agents the same way.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useSwarm } from "@/hooks/useSwarm";
import VTuberStage from "@/components/vtuber/VTuberStage";
import ChatOverlay from "@/components/vtuber/ChatOverlay";
import Stage from "@/components/Stage";
import ViewerOverlay from "@/components/ViewerOverlay";
import ViewerBettingLiveWidget from "@/components/ViewerBettingLiveWidget";
import { useViewerOverlay } from "@/hooks/useViewerOverlay";

export default function WatchPage() {
  const params = useParams<{ code: string }>();
  const code = useMemo(
    () => (params?.code ? decodeURIComponent(params.code).toUpperCase() : ""),
    [params?.code],
  );

  const {
    state,
    connected,
    chat,
    joinRoom,
    getMouthAmplitude,
    speakingAgents,
    room,
    wsRef,
    sessionId,
  } = useSwarm();

  // Lock the body to the viewport so a long event log can't push the
  // VTuber stage off-screen on mobile. The default page allows scroll
  // because the desktop UI has lots of content; here we want a kiosk
  // feel.
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Auto-join the URL-encoded room as soon as the WS is up. Re-joining
  // is idempotent on the server side, so a reconnect simply re-issues
  // the join — no client-side bookkeeping needed.
  useEffect(() => {
    if (!connected || !code) return;
    if (room?.code === code) return;
    joinRoom(code);
  }, [connected, code, room?.code, joinRoom]);

  // ── Viewer-overlay (cursors + stickers) ──────────────────────────
  // ``useSwarm`` now exposes ``wsRef`` so we can attach the overlay's
  // listener directly to the live socket. ``connected`` flips on the
  // first re-render after open, so reading ``wsRef.current`` here is
  // correct: stale-closure noise is impossible because the hook
  // re-subscribes on every render anyway.
  const overlayWs: WebSocket | null = connected ? wsRef.current : null;
  const { state: overlayState } = useViewerOverlay(overlayWs);
  const sendOverlayCommand = useCallback(
    (cmd: object) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(cmd));
      }
    },
    [wsRef],
  );
  // Stable per-mount viewer id. ``useState`` with a lazy initializer
  // is the canonical "compute once at mount" hatch — and it's the only
  // place React's purity rule lets us call ``crypto.randomUUID`` /
  // ``Date.now`` / ``Math.random`` without complaint, since the
  // initializer runs once before render. SSR returns ``"viewer"`` so
  // hydration matches; the client overrides it on the first paint.
  const [viewerId] = useState<string>(() => {
    if (typeof window === "undefined") return "viewer";
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
      return crypto.randomUUID();
    }
    return `viewer-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  });
  // The ViewerOverlay component expects a stable function reference;
  // ``sendOverlayCommand`` (defined above) already captures wsRef so
  // it's stable across renders, but we alias it here for the prop name
  // the component uses.
  const overlaySendCommand = sendOverlayCommand;

  const idle = state.agents.length === 0;

  return (
    <div className="flex h-[100dvh] w-screen flex-col bg-[#0a0a12] text-white">
      {/* ── Top bar — minimal, just the room code and a back link ── */}
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-white/10 bg-black/60 px-3 py-2 backdrop-blur">
        <Link
          href="/"
          className="rounded-md border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-[11px] text-white/60 hover:bg-white/10"
        >
          ← exit
        </Link>
        <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-white/60">
          <span
            className={`inline-block h-1.5 w-1.5 rounded-full ${
              connected ? "bg-emerald-400" : "bg-rose-500"
            } shadow`}
          />
          <span>watching</span>
          <span className="rounded bg-white/10 px-1.5 py-0.5 text-white/85">{code}</span>
        </div>
        <div className="w-[64px]" /> {/* spacer to balance the back link */}
      </header>

      {/* ── Main column ──────────────────────────────────────────── */}
      <main className="relative flex-1 overflow-hidden">
        {idle ? (
          <div className="flex h-full items-center justify-center px-6 text-center font-mono text-xs text-white/40">
            <p>
              방의 캐스트가 아직 도착하지 않았어요.
              <br />
              호스트가 swarm을 시작하면 여기에 표시됩니다.
            </p>
          </div>
        ) : (
          <div className="flex h-full flex-col">
            {/* VTuber spotlight — fills available width on phone, capped
                on tablet+ so it doesn't dominate. */}
            <div className="relative aspect-[9/16] w-full shrink-0 sm:aspect-video sm:max-h-[55%]">
              <VTuberStage
                agents={state.agents}
                getMouthAmplitude={getMouthAmplitude}
                speakingAgents={speakingAgents}
                obsMode
                backdrop="studio"
              />
            </div>
            {/* Pixel map — collapses on very small viewports because
                Stage assumes pointer interaction. We render a tiny
                preview tile instead so the UI doesn't feel empty. */}
            <div className="relative flex-1 overflow-hidden border-t border-white/10">
              <Stage
                agents={state.agents}
                sky={state.sky}
                boss={state.boss}
                cookies={state.cookies}
                getMouthAmplitude={getMouthAmplitude}
              />
            </div>
          </div>
        )}

        {/* Chat overlay floats on top of the map; tap-through is fine
            because there are no other interactive elements here. */}
        <ChatOverlay messages={chat} />

        {/* Viewer-side betting widget — only meaningful once the host
            has a swarm session attached to the room (sessionId > 0).
            The widget itself short-circuits when the operator has
            betting disabled, so this gate is purely about not flashing
            "start a swarm session to enable betting" at spectators
            while the WS hello round-trip is in flight. */}
        {sessionId !== null && sessionId > 0 ? (
          <div className="pointer-events-auto absolute bottom-2 right-2 z-30 w-[280px] max-w-[90vw]">
            <ViewerBettingLiveWidget sessionId={sessionId} />
          </div>
        ) : null}

        {/* Viewer overlay — cursors + sticker bar. Sits on top of every
            other element in the viewing area (last child of <main>) so
            its absolute-positioned children can capture mouse moves and
            sticker clicks without fighting z-index with the chat
            overlay. */}
        <ViewerOverlay
          viewerId={viewerId}
          displayName={room?.code ?? "viewer"}
          sendCommand={overlaySendCommand}
          remote={overlayState}
        />
      </main>
    </div>
  );
}

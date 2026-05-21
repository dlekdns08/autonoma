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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useSwarm } from "@/hooks/useSwarm";
import VTuberStage from "@/components/vtuber/VTuberStage";
import ChatOverlay from "@/components/vtuber/ChatOverlay";
import Stage from "@/components/Stage";
import ViewerOverlay from "@/components/ViewerOverlay";
import ViewerBettingLiveWidget from "@/components/ViewerBettingLiveWidget";
import { useViewerOverlay } from "@/hooks/useViewerOverlay";
import { useTranslate } from "@/hooks/useTranslate";

// MVP language picker for live subtitles. KO is the source (= original,
// no translation). Switching to any other code lazily translates each
// new speech line via /api/translate and overlays it under the bubble.
type SubtitleLang = "ko" | "en" | "ja" | "zh" | "es";
const LANG_OPTIONS: { code: SubtitleLang; label: string }[] = [
  { code: "ko", label: "KO" },
  { code: "en", label: "EN" },
  { code: "ja", label: "JA" },
  { code: "zh", label: "ZH" },
  { code: "es", label: "ES" },
];
// Debounce window — partial streaming tokens land fast; wait for the
// line to settle before kicking a translation request.
const TRANSLATE_DEBOUNCE_MS = 800;

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

  // ── Betting market resolution fan-out (I1) ─────────────────────────
  // Forward the latest ``betting.market_resolved`` bus event into the
  // widget so balance/leaderboard refresh immediately rather than
  // waiting for the next 5s poll. We only keep the most-recent
  // resolved market — the polling fallback handles the rare case
  // where multiple markets settle in the same WS tick.
  const lastBettingEventIdRef = useRef<number>(-1);
  const [liveBettingResolution, setLiveBettingResolution] = useState<
    import("@/lib/viewerBettingApi").ApiResolveSummary | null
  >(null);
  useEffect(() => {
    if (state.events.length === 0) return;
    let highest = lastBettingEventIdRef.current;
    let next: import("@/lib/viewerBettingApi").ApiResolveSummary | null = null;
    for (const entry of state.events) {
      if (entry.id <= lastBettingEventIdRef.current) continue;
      if (entry.id > highest) highest = entry.id;
      if (entry.event !== "betting.market_resolved") continue;
      const d = entry.data;
      const marketId = d.market_id as string | undefined;
      const winning = d.winning_option as string | undefined;
      if (!marketId || !winning) continue;
      next = {
        market_id: marketId,
        winning_option: winning,
        total_stake: (d.total_stake as number | undefined) ?? 0,
        total_payout: (d.total_payout as number | undefined) ?? 0,
        winners: (d.winners as number | undefined) ?? 0,
        losers: (d.losers as number | undefined) ?? 0,
      };
    }
    if (highest > lastBettingEventIdRef.current) {
      lastBettingEventIdRef.current = highest;
    }
    if (next) {
      setLiveBettingResolution(next);
    }
  }, [state.events]);

  const idle = state.agents.length === 0;

  // ── Live subtitles ────────────────────────────────────────────────
  // Mirror VTuberStage's spotlight selection so the translation we
  // overlay always corresponds to the bubble the viewer can see.
  // We don't have access to the stage's internal `pinned` state, but
  // the watch kiosk never lets viewers pin manually — so
  // `firstSpeaker ?? agents[0]` matches the stage's resolution.
  const [targetLang, setTargetLang] = useState<SubtitleLang>("ko");
  const { translate } = useTranslate();
  const firstSpeaker = useMemo(() => {
    for (const name of speakingAgents) return name;
    return null;
  }, [speakingAgents]);
  const [lastSpeaker, setLastSpeaker] = useState<string | null>(null);
  useEffect(() => {
    if (firstSpeaker && firstSpeaker !== lastSpeaker) {
      setLastSpeaker(firstSpeaker);
    }
  }, [firstSpeaker, lastSpeaker]);
  const spotlightName = lastSpeaker ?? state.agents[0]?.name ?? null;
  const spotlightAgent = useMemo(
    () => state.agents.find((a) => a.name === spotlightName) ?? null,
    [state.agents, spotlightName],
  );
  // Strip the trailing ellipsis VTuberStage uses to mark "still streaming"
  // so the cache key matches the finalized line.
  const rawSpeech = spotlightAgent?.speech ?? "";
  const originalSpeech = rawSpeech.endsWith("…") ? rawSpeech.slice(0, -1) : rawSpeech;

  // Per-(speaker + original text) cache lives in component state so
  // language toggles instantly resurface already-translated lines and
  // partial streams don't trigger N requests.
  const [translations, setTranslations] = useState<Map<string, string>>(
    () => new Map(),
  );
  const translationKey =
    spotlightAgent && originalSpeech.trim() && targetLang !== "ko"
      ? `${targetLang}|${spotlightAgent.name}|${originalSpeech}`
      : null;

  // Debounce per-line: only translate once the speech text stops changing
  // for TRANSLATE_DEBOUNCE_MS. Streaming tokens reset the timer because
  // ``translationKey`` (and therefore the effect's deps) changes on
  // every partial.
  useEffect(() => {
    if (!translationKey) return;
    if (translations.has(translationKey)) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void translate(originalSpeech, "ko", targetLang).then((tr) => {
        if (cancelled) return;
        setTranslations((prev) => {
          if (prev.has(translationKey)) return prev;
          const next = new Map(prev);
          next.set(translationKey, tr);
          return next;
        });
      });
    }, TRANSLATE_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [translationKey, originalSpeech, targetLang, translate, translations]);

  const translatedLine = translationKey
    ? translations.get(translationKey) ?? null
    : null;
  const showTranslation =
    targetLang !== "ko" &&
    !!spotlightAgent &&
    !!originalSpeech.trim() &&
    !!translatedLine &&
    translatedLine !== originalSpeech;

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
        {/* Language picker chip — KO is the original (no translation),
            anything else fetches translations lazily and overlays them
            under each agent speech bubble. */}
        <div
          role="radiogroup"
          aria-label="Subtitle language"
          className="flex items-center gap-0.5 rounded-md border border-white/10 bg-white/5 p-0.5 font-mono text-[10px]"
        >
          {LANG_OPTIONS.map((opt) => {
            const active = targetLang === opt.code;
            return (
              <button
                key={opt.code}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setTargetLang(opt.code)}
                className={`rounded px-1.5 py-0.5 transition-colors ${
                  active
                    ? "bg-white/15 text-white"
                    : "text-white/55 hover:text-white"
                }`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
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
              {/* Translation overlay — sits just below the VTuberStage
                  subtitle bar (which lives at bottom-0 in obsMode). On
                  network errors useTranslate resolves to the original
                  text, so showTranslation goes false and nothing
                  renders — the original bubble stays untouched. */}
              {showTranslation && (
                <div
                  className="pointer-events-none absolute inset-x-0 bottom-1 z-50 flex justify-center px-6"
                  aria-live="polite"
                >
                  <div
                    className="max-w-[min(720px,92%)] rounded-md bg-black/55 px-4 py-1 text-center font-mono text-[12px] leading-snug text-white/65 backdrop-blur-sm"
                    style={{
                      textShadow:
                        "0 1px 2px rgba(0,0,0,0.85), 0 0 3px rgba(0,0,0,0.85)",
                    }}
                  >
                    {translatedLine}
                  </div>
                </div>
              )}
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
            <ViewerBettingLiveWidget
              sessionId={sessionId}
              liveResolution={liveBettingResolution}
            />
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

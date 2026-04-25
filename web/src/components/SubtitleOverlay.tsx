"use client";

/**
 * Phase 4-B — live subtitle overlay.
 *
 * Watches a stream of agent speech lines, runs each through
 * ``useTranslate`` if a target language is selected, and renders the
 * latest line at the bottom of the stage. Designed to layer on top of
 * VTuberStage / OBS / /watch — fixed positioning, no input capture.
 *
 * The component takes ``speechLines`` as a controlled prop so the
 * dashboard / OBS page can decide how to source them (WS event log,
 * filtered chat, etc.). Anything stringy with an ``agent`` and ``text``
 * works.
 */

import { useEffect, useRef, useState } from "react";
import { useTranslate } from "@/hooks/useTranslate";

export interface SpeechLine {
  /** Stable id so consecutive duplicates don't replay. */
  id: string;
  agent: string;
  text: string;
  /** ISO ts so we can pick the most recent if the array ordering jitters. */
  at?: string;
}

export interface SubtitleOverlayProps {
  speechLines: SpeechLine[];
  /** ISO 639-1 source language hint. ``"auto"`` lets the LLM detect. */
  fromLang?: string;
  /** Empty / undefined → captions disabled. */
  toLang?: string;
  /** Pass-through className for layout overrides. */
  className?: string;
  /** When true, the original line shows above the translation. */
  showOriginal?: boolean;
}

interface RenderState {
  id: string;
  agent: string;
  original: string;
  translated: string;
}

export default function SubtitleOverlay({
  speechLines,
  fromLang = "auto",
  toLang,
  className = "",
  showOriginal = true,
}: SubtitleOverlayProps) {
  const { translate } = useTranslate();
  const [current, setCurrent] = useState<RenderState | null>(null);
  const lastIdRef = useRef<string | null>(null);

  // Watch the latest line. Skip duplicates so an idempotent re-render
  // doesn't re-translate the same string.
  useEffect(() => {
    const last = speechLines[speechLines.length - 1];
    if (!last) return;
    if (last.id === lastIdRef.current) return;
    lastIdRef.current = last.id;

    if (!toLang || toLang === fromLang) {
      setCurrent({
        id: last.id,
        agent: last.agent,
        original: last.text,
        translated: last.text,
      });
      return;
    }

    let cancelled = false;
    void translate(last.text, fromLang, toLang).then((tr) => {
      if (cancelled) return;
      setCurrent({
        id: last.id,
        agent: last.agent,
        original: last.text,
        translated: tr,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [speechLines, fromLang, toLang, translate]);

  if (!current) return null;

  const showSeparate = showOriginal && current.original !== current.translated;

  return (
    <div
      className={`pointer-events-none absolute inset-x-0 bottom-6 flex justify-center px-4 ${className}`}
      aria-live="polite"
    >
      <div className="max-w-3xl rounded-xl border border-white/10 bg-black/65 px-4 py-2 text-center font-mono text-base text-white shadow-lg backdrop-blur-md">
        <div className="font-mono text-[11px] uppercase tracking-wider text-fuchsia-300/80">
          {current.agent}
        </div>
        <div className="mt-1 leading-snug">{current.translated}</div>
        {showSeparate ? (
          <div className="mt-1 font-mono text-xs text-white/45">
            {current.original}
          </div>
        ) : null}
      </div>
    </div>
  );
}

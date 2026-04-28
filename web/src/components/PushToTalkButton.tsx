"use client";

/**
 * Push-to-talk pill button. Hold to record, release to transcribe +
 * inject through the ExternalInputRouter. Fires success / failure via
 * the parent's ``onResult`` callback so it can surface a toast.
 *
 * In ``stream`` mode the button also renders a live caption bubble
 * above itself with the rolling partial transcript from the server.
 */

import { useCallback, useEffect } from "react";
import {
  usePushToTalk,
  type PushToTalkMode,
  type PushToTalkResult,
} from "@/hooks/usePushToTalk";

export interface PushToTalkButtonProps {
  /** Capture pipeline. ``stream`` shows live captions; ``batch`` is the
   *  original single-shot upload. */
  mode?: PushToTalkMode;
  /** When supplied, every utterance is routed to this agent. Empty
   *  string / undefined → Director picks it up. */
  target?: string;
  /** Forwarded to the ASR processor. Defaults to the server's setting. */
  language?: string;
  /** Optional toast hook. */
  onResult?: (result: PushToTalkResult) => void;
  onError?: (msg: string) => void;
  /** Stream mode only — fires on each partial transcript. */
  onPartial?: (text: string) => void;
  /** Stream mode only — set false to skip the ExternalInputRouter step
   *  on the server. Useful on the /voice studio page where there is no
   *  swarm to route into. */
  route?: boolean;
  /** Hold-to-talk also fires on Spacebar. Set false to disable hotkey. */
  spaceHotkey?: boolean;
  /** Override the default top-right placement for callers that want to
   *  drop it inside their own layout. */
  className?: string;
}

const HOTKEY = " "; // Spacebar

export default function PushToTalkButton({
  mode = "batch",
  target,
  language,
  onResult,
  onError,
  onPartial,
  route,
  spaceHotkey = true,
  className = "",
}: PushToTalkButtonProps) {
  const ptt = usePushToTalk({
    mode,
    target,
    language,
    onResult,
    onError,
    onPartial,
    route,
  });

  const beginPress = useCallback(() => {
    if (!ptt.supported || ptt.recording || ptt.uploading) return;
    void ptt.start();
  }, [ptt]);

  const endPress = useCallback(() => {
    if (!ptt.recording) return;
    void ptt.stop();
  }, [ptt]);

  useEffect(() => {
    if (!spaceHotkey) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== HOTKEY) return;
      // Don't hijack the spacebar inside text inputs / contenteditable
      // — that would steal typing in the chat composer.
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      // Repeat events fire while the key is held — only the first one
      // should kick off the recorder.
      if (e.repeat) return;
      e.preventDefault();
      beginPress();
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key !== HOTKEY) return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      e.preventDefault();
      endPress();
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [spaceHotkey, beginPress, endPress]);

  if (!ptt.supported) return null;

  const label = ptt.uploading
    ? "업로드 중…"
    : ptt.recording
      ? "■ 녹음 중 — 떼면 전송"
      : spaceHotkey
        ? "🎤 push-to-talk (Space)"
        : "🎤 push-to-talk";

  const tone = ptt.recording
    ? "border-rose-400/60 bg-rose-500/30 text-rose-100 shadow-rose-500/20"
    : ptt.uploading
      ? "border-amber-400/40 bg-amber-500/20 text-amber-100"
      : "border-fuchsia-400/40 bg-fuchsia-500/15 text-fuchsia-100 hover:bg-fuchsia-500/25";

  // Stream mode renders a caption bubble above the button while a
  // partial transcript is in flight. The bubble is positioned with a
  // wrapper so the button itself keeps its layout footprint unchanged
  // (callers can still drop it into a flex row without surprise gaps).
  const showBubble = mode === "stream" && (ptt.recording || ptt.uploading) && !!ptt.partialText;

  return (
    <div className={`relative inline-block ${className}`}>
      {showBubble && (
        <div
          className="pointer-events-none absolute bottom-full right-0 mb-2 max-w-[28rem] rounded-lg border border-fuchsia-400/40 bg-slate-950/90 px-3 py-1.5 font-mono text-[11px] leading-snug text-fuchsia-100 shadow-lg backdrop-blur"
          aria-live="polite"
        >
          <span className="text-fuchsia-400/70">자막:</span>{" "}
          <span className="text-fuchsia-50">{ptt.partialText}</span>
        </div>
      )}
      <button
        type="button"
        onMouseDown={beginPress}
        onMouseUp={endPress}
        onMouseLeave={endPress}
        onTouchStart={beginPress}
        onTouchEnd={endPress}
        title="버튼을 누르고 있는 동안 말하세요. (Space 키도 가능)"
        className={`select-none rounded-full border px-4 py-2 font-mono text-xs font-semibold backdrop-blur transition ${tone}`}
      >
        {label}
      </button>
    </div>
  );
}

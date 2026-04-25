"use client";

/**
 * Phase 2-#4 — push-to-talk pill button. Hold to record, release to
 * transcribe + inject. Fires success / failure via the parent's
 * ``onResult`` callback so it can surface a toast.
 */

import { useCallback, useEffect } from "react";
import { usePushToTalk, type PushToTalkResult } from "@/hooks/usePushToTalk";

export interface PushToTalkButtonProps {
  /** When supplied, every utterance is routed to this agent. Empty
   *  string / undefined → Director picks it up. */
  target?: string;
  /** Forwarded to the ASR processor. Defaults to the server's setting. */
  language?: string;
  /** Optional toast hook. */
  onResult?: (result: PushToTalkResult) => void;
  onError?: (msg: string) => void;
  /** Hold-to-talk also fires on Spacebar. Set false to disable hotkey. */
  spaceHotkey?: boolean;
  /** Override the default top-right placement for callers that want to
   *  drop it inside their own layout. */
  className?: string;
}

const HOTKEY = " "; // Spacebar

export default function PushToTalkButton({
  target,
  language,
  onResult,
  onError,
  spaceHotkey = true,
  className = "",
}: PushToTalkButtonProps) {
  const ptt = usePushToTalk({ target, language, onResult, onError });

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

  return (
    <button
      type="button"
      onMouseDown={beginPress}
      onMouseUp={endPress}
      onMouseLeave={endPress}
      onTouchStart={beginPress}
      onTouchEnd={endPress}
      title="버튼을 누르고 있는 동안 말하세요. (Space 키도 가능)"
      className={`select-none rounded-full border px-4 py-2 font-mono text-xs font-semibold backdrop-blur transition ${tone} ${className}`}
    >
      {label}
    </button>
  );
}

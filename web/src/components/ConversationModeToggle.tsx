"use client";

/**
 * Wave A — Always-on conversation toggle.
 *
 * Sits beside (or instead of) the push-to-talk button. When the user
 * flips the toggle on, the mic stays open and the silero-vad model
 * decides when to send the audio. The button itself doubles as the
 * status indicator: colour and icon mirror the underlying VAD state
 * (loading / idle-listening / speaking / uploading / error).
 *
 * Props mirror PushToTalkButton's interrupt/result hooks so callers
 * can wire both controls into the same TTS-pause + transcript-text
 * sinks.
 */

import { useEffect } from "react";
import {
  useAlwaysOnVoice,
  type AlwaysOnResult,
  type AlwaysOnState,
} from "@/hooks/useAlwaysOnVoice";

export interface ConversationModeToggleProps {
  enabled: boolean;
  onEnabledChange: (next: boolean) => void;
  language?: string;
  target?: string;
  onResult?: (r: AlwaysOnResult) => void;
  onError?: (msg: string) => void;
  onInterrupt?: () => void;
  className?: string;
}

const STATE_LABEL: Record<AlwaysOnState, string> = {
  off: "🔇 대화 모드 꺼짐",
  loading: "⏳ 모델 로딩 중…",
  idle: "🎧 듣는 중",
  speaking: "🎤 인식 중",
  uploading: "📤 전송 중…",
  error: "⚠️ 오류",
};

const STATE_TONE: Record<AlwaysOnState, string> = {
  off: "border-white/15 bg-white/5 text-white/50",
  loading: "border-amber-400/40 bg-amber-500/15 text-amber-100",
  idle: "border-emerald-400/40 bg-emerald-500/15 text-emerald-100",
  speaking: "border-rose-400/60 bg-rose-500/30 text-rose-50 shadow-rose-500/30",
  uploading: "border-cyan-400/40 bg-cyan-500/20 text-cyan-100",
  error: "border-rose-400/60 bg-rose-500/20 text-rose-100",
};

export default function ConversationModeToggle({
  enabled,
  onEnabledChange,
  language,
  target,
  onResult,
  onError,
  onInterrupt,
  className = "",
}: ConversationModeToggleProps) {
  const ao = useAlwaysOnVoice({
    target,
    language,
    onResult,
    onError,
    onInterrupt,
  });

  // Drive the VAD start/stop from the parent-controlled ``enabled`` flag.
  // Using an effect (rather than calling start/stop in a click handler)
  // means the hook also tears down cleanly when the parent unmounts
  // mid-conversation — e.g. user navigates away with the toggle on.
  useEffect(() => {
    if (enabled) {
      void ao.start();
    } else {
      void ao.stop();
    }
    // ``ao`` identity changes per render; we intentionally key only
    // on ``enabled`` to avoid restart-loops. The hook is safe with
    // double-start / double-stop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  const label = enabled ? STATE_LABEL[ao.state] : STATE_LABEL.off;
  const tone = enabled ? STATE_TONE[ao.state] : STATE_TONE.off;

  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <button
        type="button"
        onClick={() => onEnabledChange(!enabled)}
        className={`select-none rounded-full border px-4 py-2 font-mono text-xs font-semibold backdrop-blur transition ${tone}`}
        title={
          enabled
            ? "대화 모드 켜짐 — 그냥 말하시면 됩니다. 다시 누르면 끕니다."
            : "대화 모드 꺼짐 — 누르면 마이크가 항상 듣기 시작합니다."
        }
        aria-pressed={enabled}
        aria-label={
          enabled
            ? `대화 모드 켜짐, 현재 상태: ${label}`
            : "대화 모드 끄기 토글"
        }
      >
        {label}
      </button>
      {enabled && ao.state === "speaking" && ao.lastText && (
        <span className="font-mono text-[10px] text-white/40 max-w-[20rem] truncate">
          마지막: {ao.lastText}
        </span>
      )}
      {ao.error && (
        <span className="font-mono text-[10px] text-rose-300/80 max-w-[20rem] break-words">
          {ao.error}
        </span>
      )}
    </div>
  );
}

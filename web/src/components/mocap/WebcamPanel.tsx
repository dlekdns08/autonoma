"use client";

/**
 * Side-by-side webcam preview + live solve status. The ``<video>``
 * element is the one ``useMocap`` attaches the MediaStream to — we just
 * give it a parent and label the current state.
 */

import type { RefObject } from "react";
import type { MocapStatus } from "@/hooks/mocap/useMocap";

interface Props {
  videoRef: RefObject<HTMLVideoElement | null>;
  status: MocapStatus;
  error: string | null;
  /** Selected VRM filename for the "recording target" label. */
  targetLabel?: string;
  /** ``true`` when the solved sample is landing bones — lets us show a
   *  green indicator in the preview without the caller having to pass
   *  the full ``ClipSample``. */
  tracking: boolean;
  mirror: boolean;
}

export default function WebcamPanel({
  videoRef,
  status,
  error,
  targetLabel,
  tracking,
  mirror,
}: Props) {
  return (
    <div className="relative overflow-hidden rounded-lg border border-white/10 bg-black/50">
      <video
        ref={videoRef}
        className="aspect-[4/3] w-full object-cover"
        style={{ transform: mirror ? "scaleX(-1)" : undefined }}
        playsInline
        muted
      />
      <div className="absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/70 to-transparent px-3 py-2 text-[11px] font-mono">
        <span className="text-white/70">
          {targetLabel ? `→ ${targetLabel}` : "대상 없음"}
        </span>
        <span
          className={
            "rounded-full px-2 py-0.5 " +
            (status === "running"
              ? tracking
                ? "bg-emerald-500/20 text-emerald-200"
                : "bg-amber-500/20 text-amber-200"
              : status === "loading"
                ? "bg-indigo-500/20 text-indigo-200"
                : status === "error"
                  ? "bg-rose-500/20 text-rose-200"
                  : "bg-slate-500/20 text-slate-200")
          }
        >
          {status === "running"
            ? tracking
              ? "추적 중"
              : "카메라 연결됨"
            : status === "loading"
              ? "모델 로드 중…"
              : status === "error"
                ? "오류"
                : "대기"}
        </span>
      </div>
      {error && (
        <div
          role="alert"
          className="absolute inset-x-0 bottom-0 bg-rose-900/80 px-3 py-2 text-[11px] font-mono text-rose-50"
        >
          {error}
        </div>
      )}
    </div>
  );
}

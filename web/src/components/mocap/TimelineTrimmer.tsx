"use client";

/**
 * Very small timeline scrubber + trim handles. Used to preview + clip
 * a ``MocapClip`` before uploading — the captured frames often include
 * a "reach for the stop button" tail the operator wants to discard.
 *
 * The trim state lives in the parent. This component is a pure UI
 * control over ``{ startS, endS, playheadS, durationS }``.
 */

import { useCallback } from "react";

interface Props {
  durationS: number;
  startS: number;
  endS: number;
  playheadS: number;
  playing: boolean;
  onChange: (next: { startS: number; endS: number }) => void;
  onSeek: (t: number) => void;
  onTogglePlay: () => void;
}

export default function TimelineTrimmer({
  durationS,
  startS,
  endS,
  playheadS,
  playing,
  onChange,
  onSeek,
  onTogglePlay,
}: Props) {
  const pct = (t: number) => (durationS > 0 ? (t / durationS) * 100 : 0);

  const onStart = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = Math.min(endS - 0.05, Math.max(0, parseFloat(e.target.value)));
      onChange({ startS: v, endS });
    },
    [endS, onChange],
  );
  const onEnd = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = Math.max(startS + 0.05, Math.min(durationS, parseFloat(e.target.value)));
      onChange({ startS, endS: v });
    },
    [startS, durationS, onChange],
  );
  const onPlayhead = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      onSeek(Math.max(0, Math.min(durationS, parseFloat(e.target.value))));
    },
    [durationS, onSeek],
  );

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-white/10 bg-slate-900/40 px-3 py-2">
      <div className="flex items-center justify-between text-[11px] font-mono text-white/60">
        <span>클립 길이 {durationS.toFixed(2)}초</span>
        <span>
          {startS.toFixed(2)} – {endS.toFixed(2)}초
        </span>
      </div>
      <div className="relative h-2 rounded-full bg-white/10">
        <div
          className="absolute inset-y-0 rounded-full bg-fuchsia-500/40"
          style={{
            left: `${pct(startS)}%`,
            width: `${Math.max(0, pct(endS) - pct(startS))}%`,
          }}
        />
        <div
          className="absolute inset-y-0 w-0.5 bg-white"
          style={{ left: `${pct(playheadS)}%` }}
        />
      </div>
      <div className="grid gap-1 text-[10px] font-mono text-white/50">
        <label className="flex items-center gap-2">
          <span className="w-12">시작</span>
          <input
            type="range"
            min={0}
            max={durationS}
            step={0.01}
            value={startS}
            onChange={onStart}
            className="flex-1"
          />
        </label>
        <label className="flex items-center gap-2">
          <span className="w-12">끝</span>
          <input
            type="range"
            min={0}
            max={durationS}
            step={0.01}
            value={endS}
            onChange={onEnd}
            className="flex-1"
          />
        </label>
        <label className="flex items-center gap-2">
          <span className="w-12">재생</span>
          <input
            type="range"
            min={0}
            max={durationS}
            step={0.01}
            value={playheadS}
            onChange={onPlayhead}
            className="flex-1"
          />
        </label>
      </div>
      <button
        type="button"
        onClick={onTogglePlay}
        className="self-start rounded border border-white/15 bg-slate-950/70 px-3 py-1 text-[11px] font-mono text-white hover:border-white/35"
      >
        {playing ? "일시 정지" : "재생"}
      </button>
    </div>
  );
}

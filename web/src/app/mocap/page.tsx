"use client";

/**
 * ``/mocap`` page — capture motion into reusable clips and bind them to
 * character triggers.
 *
 *   1. Pick a target character (VRM filename).
 *   2. Enable the webcam → MediaPipe FaceLandmarker + PoseLandmarker
 *      drives a live VRM preview through ``useMocap``.
 *   3. Record, trim, name, upload → appears in ``ClipLibrary``.
 *   4. Bind clips to mood / emote / state triggers in ``BindingEditor``;
 *      the dashboard picks them up via the ``mocap.bindings.updated``
 *      WebSocket event.
 *
 * Auth gating: pending / denied / disabled users see a 403 panel. Any
 * signed-in active user can record + bind (bindings are global, but
 * that's an accepted design choice for this PoC).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { useMocap } from "@/hooks/mocap/useMocap";
import { useMocapClips } from "@/hooks/mocap/useMocapClips";
import { useMocapBindings } from "@/hooks/mocap/useMocapBindings";
import CharacterPicker from "@/components/mocap/CharacterPicker";
import WebcamPanel from "@/components/mocap/WebcamPanel";
import MocapPreview from "@/components/mocap/MocapPreview";
import TimelineTrimmer from "@/components/mocap/TimelineTrimmer";
import ClipLibrary from "@/components/mocap/ClipLibrary";
import BindingEditor from "@/components/mocap/BindingEditor";
import { encodeClip } from "@/lib/mocap/gzipEncode";
import {
  ClipRuntime,
  createSampleBuffer,
  type ClipSample,
} from "@/lib/mocap/clipPlayer";
import type { MocapClip } from "@/lib/mocap/clipFormat";

type Stage = "idle" | "recorded" | "uploading" | "error";

export default function MocapPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  const isActive = user?.status === "active";

  // Recording + preview wiring.
  const mocap = useMocap({ mirror: true });
  const [targetVrm, setTargetVrm] = useState<string | null>(null);

  // Clip list + bindings. Bindings need a refresh token bumped by the
  // ``mocap.bindings.updated`` WS event; we mirror it as a local counter
  // here so two mocap pages opened side-by-side stay in sync through
  // manual refresh at minimum.
  const [refreshToken, setRefreshToken] = useState(0);
  const clipsApi = useMocapClips(refreshToken);
  const bindingsApi = useMocapBindings(refreshToken);

  // Captured-but-not-yet-uploaded clip + its trim state.
  const [draftClip, setDraftClip] = useState<MocapClip | null>(null);
  const [clipName, setClipName] = useState("");
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [stage, setStage] = useState<Stage>("idle");
  const [stageMessage, setStageMessage] = useState<string | null>(null);

  // Playback buffer for the trim preview. Fed by a ClipRuntime we
  // create on demand. Playhead lives in both a ref (fast path for the
  // rAF tick) and state (UI rendering); don't include the state in
  // effect deps or every tick will re-register the rAF loop.
  const trimSampleRef = useRef<ClipSample>(createSampleBuffer());
  const trimRuntimeRef = useRef<ClipRuntime | null>(null);
  const [playing, setPlaying] = useState(false);
  const [playhead, setPlayhead] = useState(0);
  // 녹화 경과 초. ``performance.now()`` 를 render 중에 부르면
  // react-hooks/refs 가 impure 함수 호출이라고 경고하므로, 10Hz 로 state
  // 를 갱신해 "녹화 중인 동안만 tick" 하고 정지하면 갱신도 멈춘다.
  const [recordElapsed, setRecordElapsed] = useState(0);
  const playheadRef = useRef(0);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!draftClip) {
      trimRuntimeRef.current = null;
      setTrimStart(0);
      setTrimEnd(0);
      setPlayhead(0);
      playheadRef.current = 0;
      return;
    }
    trimRuntimeRef.current = new ClipRuntime(draftClip, 0, { loop: false });
    setTrimStart(0);
    setTrimEnd(draftClip.durationS);
    setPlayhead(0);
    playheadRef.current = 0;
  }, [draftClip]);

  useEffect(() => {
    if (!mocap.recording) return;
    const id = window.setInterval(() => setRecordTick((n) => n + 1), 100);
    return () => window.clearInterval(id);
  }, [mocap.recording]);

  useEffect(() => {
    if (!playing || !draftClip || !trimRuntimeRef.current) return;
    const rt = trimRuntimeRef.current;
    // Snap a stale playhead back inside the trim window.
    if (playheadRef.current < trimStart || playheadRef.current >= trimEnd) {
      playheadRef.current = trimStart;
    }
    const startWall = performance.now() / 1000;
    const startHead = playheadRef.current;
    const tick = () => {
      const now = performance.now() / 1000;
      const t = Math.min(trimEnd, startHead + (now - startWall));
      playheadRef.current = t;
      setPlayhead(t);
      rt.sampleInto(t, trimSampleRef.current);
      if (t >= trimEnd) {
        setPlaying(false);
        rafRef.current = null;
        return;
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [playing, draftClip, trimStart, trimEnd]);

  const onSeek = (t: number) => {
    setPlaying(false);
    setPlayhead(t);
    playheadRef.current = t;
    if (trimRuntimeRef.current) {
      trimRuntimeRef.current.sampleInto(t, trimSampleRef.current);
    }
  };

  // Recording controls.
  const onStartCamera = async () => {
    if (!targetVrm) {
      alert("먼저 대상 캐릭터를 선택하세요.");
      return;
    }
    await mocap.start();
  };
  const onStopCamera = () => {
    mocap.stop();
  };
  const onRecord = () => {
    mocap.startRecording();
  };
  const onStopRecord = () => {
    const clip = mocap.stopRecording();
    if (clip) {
      const bound = { ...clip, sourceVrm: targetVrm ?? "" };
      setDraftClip(bound);
      setClipName(`${bound.sourceVrm || "clip"} ${new Date().toLocaleTimeString()}`);
      setStage("recorded");
      setStageMessage(null);
    } else {
      setStageMessage("녹화가 너무 짧습니다 (2프레임 미만).");
    }
  };

  const onDiscard = () => {
    setDraftClip(null);
    setStage("idle");
    setStageMessage(null);
    setPlaying(false);
  };

  const onUpload = async () => {
    if (!draftClip) return;
    // Apply the trim by slicing the uniform-sampled track arrays. We
    // reuse the clip's existing fps — no re-resample needed.
    const trimmed = sliceClip(draftClip, trimStart, trimEnd);
    trimmed.name = clipName.trim() || `clip-${Date.now()}`;
    trimmed.sourceVrm = targetVrm ?? trimmed.sourceVrm;
    setStage("uploading");
    setStageMessage(null);
    try {
      const { payloadGzB64, rawSizeBytes } = await encodeClip(trimmed);
      const res = await clipsApi.upload({
        name: trimmed.name,
        sourceVrm: trimmed.sourceVrm,
        payloadGzB64,
        expectedSizeBytes: rawSizeBytes,
      });
      if (!res.ok) {
        setStage("error");
        setStageMessage(`업로드 실패: ${res.reason}`);
        return;
      }
      setDraftClip(null);
      setStage("idle");
      setStageMessage(`"${trimmed.name}" 저장 완료`);
      setRefreshToken((n) => n + 1);
    } catch (err) {
      setStage("error");
      setStageMessage(err instanceof Error ? err.message : String(err));
    }
  };

  // Which preview to show: draft clip when recorded, else the live webcam solve.
  const liveSampleRef = useRef<ClipSample>(mocap.latestSample);
  liveSampleRef.current = mocap.latestSample;
  const previewSampleRef = stage === "recorded" ? trimSampleRef : liveSampleRef;

  const tracking = useMemo(() => {
    return mocap.frameSeq > 0 && Object.keys(mocap.latestSample.bones).length > 0;
  }, [mocap.frameSeq, mocap.latestSample]);

  // ── Auth gating ──────────────────────────────────────────────────────
  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/40">
        loading…
      </div>
    );
  }
  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12]">
        <div className="max-w-md rounded-2xl border border-white/10 bg-slate-950/95 p-8 text-center shadow-2xl">
          <h1 className="font-mono text-2xl font-bold text-white">로그인이 필요합니다</h1>
          <p className="mt-2 font-mono text-xs text-white/50">
            /mocap 페이지는 인증된 계정으로만 접근할 수 있습니다.
          </p>
          <button
            onClick={() => router.push("/")}
            className="mt-4 rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/70 hover:bg-white/10"
          >
            홈으로
          </button>
        </div>
      </div>
    );
  }
  if (!isActive) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12]">
        <div className="max-w-md rounded-2xl border border-rose-500/30 bg-slate-950/95 p-8 text-center shadow-2xl">
          <div className="mb-3 text-4xl">⛔</div>
          <h1 className="font-mono text-2xl font-bold text-rose-300">403</h1>
          <p className="mt-2 font-mono text-sm text-white/60">
            계정이 아직 활성 상태가 아닙니다. 관리자 승인 후 다시 시도하세요.
          </p>
        </div>
      </div>
    );
  }

  // ── Main layout ──────────────────────────────────────────────────────
  return (
    <div className="min-h-screen bg-[#0a0a12] p-4 text-white">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-2xl font-bold text-transparent">
              모션 캡처 스튜디오
            </h1>
            <p className="mt-1 font-mono text-xs text-white/40">
              웹캠 → VRM · 녹화 후 전역 바인딩에 등록하면 대시보드가 즉시 반영합니다.
            </p>
          </div>
          <button
            onClick={() => router.push("/")}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/60 hover:bg-white/10"
          >
            ← 대시보드
          </button>
        </header>

        <section className="flex flex-col gap-2">
          <h2 className="font-mono text-sm font-semibold text-white/80">캐릭터 선택</h2>
          <CharacterPicker
            selected={targetVrm}
            onSelect={(f) => {
              setTargetVrm(f);
              if (stage === "recorded") onDiscard();
            }}
            bindings={bindingsApi.bindings}
          />
        </section>

        {targetVrm && (
          <section className="grid gap-4 md:grid-cols-2">
            <div className="flex flex-col gap-3">
              <WebcamPanel
                videoRef={mocap.videoRef}
                status={mocap.status}
                error={mocap.error}
                targetLabel={targetVrm}
                tracking={tracking}
                mirror={true}
              />
              <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono">
                {mocap.status !== "running" ? (
                  <button
                    onClick={onStartCamera}
                    disabled={mocap.status === "loading"}
                    className="rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-40"
                  >
                    {mocap.status === "loading" ? "로드 중…" : "카메라 켜기"}
                  </button>
                ) : (
                  <button
                    onClick={onStopCamera}
                    className="rounded border border-white/15 bg-slate-950/70 px-3 py-1.5 text-white/70 hover:border-white/35"
                  >
                    카메라 끄기
                  </button>
                )}
                {mocap.status === "running" && !mocap.recording && stage !== "recorded" && (
                  <button
                    onClick={onRecord}
                    className="rounded border border-rose-500/40 bg-rose-500/10 px-3 py-1.5 text-rose-200 hover:bg-rose-500/20"
                  >
                    ● 녹화
                  </button>
                )}
                {mocap.recording && (
                  <button
                    onClick={onStopRecord}
                    className="rounded border border-rose-500/70 bg-rose-500/20 px-3 py-1.5 text-rose-100 hover:bg-rose-500/30"
                  >
                    ■ 정지 ({mocap.recordingMeta
                      ? (performance.now() / 1000 - mocap.recordingMeta.startedAt).toFixed(1)
                      : "0.0"}
                    초)
                  </button>
                )}
                {stage === "recorded" && (
                  <>
                    <input
                      value={clipName}
                      onChange={(e) => setClipName(e.target.value)}
                      placeholder="클립 이름"
                      className="w-56 rounded border border-white/15 bg-slate-950/80 px-2 py-1 font-mono text-xs text-white outline-none focus:border-fuchsia-500/60"
                    />
                    <button
                      onClick={onUpload}
                      disabled={!clipName.trim()}
                      className="rounded border border-fuchsia-500/50 bg-fuchsia-500/15 px-3 py-1.5 text-fuchsia-100 hover:bg-fuchsia-500/25 disabled:opacity-40"
                    >
                      업로드
                    </button>
                    <button
                      onClick={onDiscard}
                      className="rounded border border-white/15 bg-slate-950/70 px-3 py-1.5 text-white/70 hover:border-white/35"
                    >
                      버리기
                    </button>
                  </>
                )}
                {stageMessage && (
                  <span
                    className={
                      "font-mono " +
                      (stage === "error" ? "text-rose-300" : "text-emerald-300")
                    }
                  >
                    {stageMessage}
                  </span>
                )}
              </div>
              {stage === "recorded" && draftClip && (
                <TimelineTrimmer
                  durationS={draftClip.durationS}
                  startS={trimStart}
                  endS={trimEnd}
                  playheadS={playhead}
                  playing={playing}
                  onChange={({ startS, endS }) => {
                    setTrimStart(startS);
                    setTrimEnd(endS);
                  }}
                  onSeek={onSeek}
                  onTogglePlay={() => setPlaying((p) => !p)}
                />
              )}
            </div>

            <MocapPreview vrmFile={targetVrm} sampleRef={previewSampleRef} />
          </section>
        )}

        {targetVrm && (
          <section className="grid gap-4 md:grid-cols-2">
            <div className="flex flex-col gap-2">
              <h2 className="font-mono text-sm font-semibold text-white/80">클립 라이브러리</h2>
              <ClipLibrary
                clips={clipsApi.clips}
                loading={clipsApi.loading}
                selectedClipId={null}
                sourceVrmFilter={targetVrm}
                onSelect={() => {}}
                onRename={clipsApi.rename}
                onDelete={clipsApi.remove}
              />
            </div>
            <div className="flex flex-col gap-2">
              <h2 className="font-mono text-sm font-semibold text-white/80">
                {targetVrm} 바인딩
              </h2>
              <BindingEditor
                vrmFile={targetVrm}
                clips={clipsApi.clips}
                bindings={bindingsApi}
              />
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

/** Slice a uniform-sampled clip to ``[startS, endS]``. Assumes the clip
 *  was sampled at ``fps`` frames per second with ``frameCount = round
 *  (durationS * fps) + 1`` (the same invariant the recorder / server
 *  enforce). */
function sliceClip(clip: MocapClip, startS: number, endS: number): MocapClip {
  if (startS <= 0 && endS >= clip.durationS) return clip;
  const fps = clip.fps;
  const i0 = Math.max(0, Math.floor(startS * fps));
  const i1 = Math.min(clip.frameCount - 1, Math.ceil(endS * fps));
  const newCount = Math.max(2, i1 - i0 + 1);
  const newDuration = Number(((newCount - 1) / fps).toFixed(3));
  const bones: MocapClip["bones"] = {};
  for (const [name, track] of Object.entries(clip.bones) as [
    keyof MocapClip["bones"],
    { data: number[] },
  ][]) {
    const out = new Array(newCount * 4);
    for (let j = 0; j < newCount; j++) {
      const src = (i0 + j) * 4;
      out[j * 4] = track.data[src];
      out[j * 4 + 1] = track.data[src + 1];
      out[j * 4 + 2] = track.data[src + 2];
      out[j * 4 + 3] = track.data[src + 3];
    }
    bones[name] = { data: out };
  }
  const expressions: MocapClip["expressions"] = {};
  for (const [name, track] of Object.entries(clip.expressions) as [
    keyof MocapClip["expressions"],
    { data: number[] },
  ][]) {
    expressions[name] = { data: track.data.slice(i0, i0 + newCount) };
  }
  return {
    ...clip,
    durationS: newDuration,
    frameCount: newCount,
    bones,
    expressions,
  };
}

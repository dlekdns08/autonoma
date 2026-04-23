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
 * signed-in active user can record clips; only admins can mutate the
 * site-wide trigger → clip bindings.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { useMocap } from "@/hooks/mocap/useMocap";
import { useMocapClips } from "@/hooks/mocap/useMocapClips";
import { useMocapBindings } from "@/hooks/mocap/useMocapBindings";
import CharacterPicker from "@/components/mocap/CharacterPicker";
import WebcamPanel from "@/components/mocap/WebcamPanel";
import SkeletonOverlay from "@/components/mocap/SkeletonOverlay";
import MocapPreview, { type FingerTestAxis } from "@/components/mocap/MocapPreview";
import type { NormalizedLandmark } from "@mediapipe/tasks-vision";
import TimelineTrimmer from "@/components/mocap/TimelineTrimmer";
import ClipLibrary from "@/components/mocap/ClipLibrary";
import BindingEditor from "@/components/mocap/BindingEditor";
import { encodeClip } from "@/lib/mocap/gzipEncode";
import {
  ClipRuntime,
  clipCache,
  createSampleBuffer,
  type ClipSample,
} from "@/lib/mocap/clipPlayer";
import type { MocapClip } from "@/lib/mocap/clipFormat";
import {
  DEFAULT_TRIGGER_CATALOG,
  fetchTriggerCatalog,
} from "@/lib/mocap/triggers";
import { API_BASE_URL, useSwarm } from "@/hooks/useSwarm";
import {
  computeAlignment,
  type VrmBoneWorldPositions,
} from "@/lib/mocap/alignment";

type Stage = "idle" | "recorded" | "uploading" | "error";

/** One-line readout for the "손 캡처" status strip. Kept as a
 *  module-scoped helper so inline JSX doesn't embed the formatting. */
function handDiagLabel(d: {
  count: number;
  leftActive: boolean;
  rightActive: boolean;
  maxCurl: number;
}): string {
  const sides =
    d.leftActive && d.rightActive
      ? "L+R"
      : d.leftActive
        ? "L"
        : d.rightActive
          ? "R"
          : "–";
  const curlDeg = Math.round((d.maxCurl * 180) / Math.PI);
  return `손 ${d.count}개 (${sides}) · curl ${curlDeg}°`;
}

export default function MocapPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  const isActive = user?.status === "active";
  const isAdmin = user?.role === "admin";

  // ``?debug=1`` in the URL unhides developer scaffolding (axis-test
  // buttons and the hand-diagnostic badge). Lazy-init on the client so
  // SSR doesn't trip on ``window`` and so we don't trigger the
  // set-state-in-effect lint rule.
  const [debugMode] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return new URLSearchParams(window.location.search).get("debug") === "1";
  });

  // Fetch the server's max clip duration once so the recorder auto-stops
  // at exactly the ceiling ``validate_payload`` will enforce. Falls back
  // to the baked default on network error.
  const [maxDurationS, setMaxDurationS] = useState<number>(
    DEFAULT_TRIGGER_CATALOG.maxClipDurationS,
  );
  useEffect(() => {
    let cancelled = false;
    void fetchTriggerCatalog(API_BASE_URL).then((cat) => {
      if (!cancelled) setMaxDurationS(cat.maxClipDurationS);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Hand-capture toggle. Off by default — the extra HandLandmarker pass
  // costs a few fps on low-end laptops, and plenty of clips (talk, mood
  // idle) don't need fingers. Flipping the toggle takes effect on the
  // next ``start()``; we disable the checkbox while the camera is live.
  const [handsEnabled, setHandsEnabled] = useState<boolean>(false);

  // Skeleton overlay controls.
  //   * ``overlayEnabled`` — toggle the canvas layer on/off.
  //   * ``referencePose`` — frozen 33-landmark snapshot used as the
  //     "guide" the user tries to match. Null ⇒ no guide, overlay
  //     draws only the live skeleton in green.
  //   * ``matchThreshold`` — per-joint normalized-distance threshold
  //     past which the joint flips to red to show the user which body
  //     part is off-pose. 0.08 ≈ 8% of image width (≈ fist-width).
  const [overlayEnabled, setOverlayEnabled] = useState(true);
  const [referencePose, setReferencePose] = useState<
    NormalizedLandmark[] | null
  >(null);
  const [matchThreshold, setMatchThreshold] = useState(0.08);

  // Finger-axis diagnostic. When set, MocapPreview bypasses the solver
  // for finger proximals and forces a fixed 60° rotation around the
  // chosen axis. Whichever axis visibly bends the fingers inward is
  // the rig's actual curl axis — feed that back to the solver.
  const [testFingerAxis, setTestFingerAxis] = useState<FingerTestAxis>(null);
  const [testFingerUntil, setTestFingerUntil] = useState(0);
  const testTimerRef = useRef<number | null>(null);
  // Wrapped in useCallback so ``performance.now()`` inside only runs on
  // the click, not during render — keeps the react-hooks/purity lint
  // rule happy.
  const runAxisTest = useCallback((axis: "x" | "y" | "z") => {
    if (testTimerRef.current !== null) {
      window.clearTimeout(testTimerRef.current);
    }
    setTestFingerAxis(axis);
    setTestFingerUntil(performance.now() + 3000);
    testTimerRef.current = window.setTimeout(() => {
      setTestFingerAxis(null);
      testTimerRef.current = null;
    }, 3000);
  }, []);

  // Recording + preview wiring. ``onMaxDurationReached`` is a ref so
  // ``useMocap`` invokes the *current* ``onStopRecord`` (which reads the
  // latest ``targetVrm``) without re-initialising the whole hook each
  // time these deps change.
  const autoStopRef = useRef<() => void>(() => {});
  // Flipped by the auto-stop path so ``onStopRecord`` can distinguish a
  // user-driven stop from the server-limit cutoff and surface the right
  // toast. Reset inside ``onStopRecord`` after it's been consumed.
  const autoStoppedByMaxRef = useRef(false);
  const [targetVrm, setTargetVrm] = useState<string | null>(null);
  const mocap = useMocap({
    mirror: true,
    hands: handsEnabled,
    maxDurationS,
    vrmFile: targetVrm ?? undefined,
    onMaxDurationReached: () => {
      autoStoppedByMaxRef.current = true;
      autoStopRef.current();
    },
  });

  // Snapshot the user's current pose into ``referencePose`` so the
  // overlay's guide layer draws it. Deep-copy each landmark: the ref
  // holds the mutable buffer MediaPipe writes into, so retaining a
  // reference would clobber the guide next frame. Plain function —
  // the React compiler handles memoization automatically and useCallback
  // here trips the "existing memoization could not be preserved" rule.
  const captureReferencePose = () => {
    const pose = mocap.poseLandmarksRef.current;
    if (!pose || pose.length === 0) return;
    setReferencePose(pose.map((l) => ({ ...l })));
  };

  // Clip list + bindings. Bindings need a refresh token bumped by the
  // ``mocap.bindings.updated`` WS event; we mirror it as a local counter
  // here so two mocap pages opened side-by-side stay in sync through
  // manual refresh at minimum. ``mocapClipEvent`` routes peer
  // create/rename/delete mutations through the client clip cache so
  // other clients don't keep playing stale copies until the 5-minute
  // TTL.
  const { mocapClipEvent } = useSwarm();
  const [refreshToken, setRefreshToken] = useState(0);
  const clipsApi = useMocapClips(refreshToken, mocapClipEvent);
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

  // Selected-clip preview state. When a library clip is clicked (and
  // there's no active recording / draft), we load it into a dedicated
  // ClipRuntime + sample buffer and drive an rAF loop that keeps the
  // preview panel alive. Separate from ``trimSampleRef`` so clicking a
  // library clip doesn't disturb an in-progress trim.
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const selectedRuntimeRef = useRef<ClipRuntime | null>(null);
  const selectedSampleRef = useRef<ClipSample>(createSampleBuffer());
  const selectedRafRef = useRef<number | null>(null);

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
    const meta = mocap.recordingMeta;
    if (!mocap.recording || !meta) return;
    // Stale "last elapsed" gets overwritten by the first tick below;
    // no need to reset here, which would trigger the lint rule that
    // flags setState calls in an effect body.
    const update = () =>
      setRecordElapsed(performance.now() / 1000 - meta.startedAt);
    update();
    const id = window.setInterval(update, 100);
    return () => window.clearInterval(id);
  }, [mocap.recording, mocap.recordingMeta]);

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

  // Load + loop-play the currently selected library clip. Cleanup
  // cancels the rAF and drops the runtime so switching clips (or
  // deselecting) never leaves a zombie tick running.
  useEffect(() => {
    if (!selectedClipId) {
      selectedRuntimeRef.current = null;
      if (selectedRafRef.current !== null) {
        cancelAnimationFrame(selectedRafRef.current);
        selectedRafRef.current = null;
      }
      return;
    }
    const id = selectedClipId;
    let cancelled = false;
    clipCache
      .ensure(id)
      .then((clip) => {
        if (cancelled || selectedClipId !== id) return;
        const rt = new ClipRuntime(clip, performance.now() / 1000, {
          loop: true,
        });
        selectedRuntimeRef.current = rt;
        const tick = () => {
          const cur = selectedRuntimeRef.current;
          if (!cur) return;
          cur.sampleInto(performance.now() / 1000, selectedSampleRef.current);
          selectedRafRef.current = requestAnimationFrame(tick);
        };
        selectedRafRef.current = requestAnimationFrame(tick);
      })
      .catch((err) => {
        console.warn("[mocap] clip preview load failed:", err);
      });
    return () => {
      cancelled = true;
      if (selectedRafRef.current !== null) {
        cancelAnimationFrame(selectedRafRef.current);
        selectedRafRef.current = null;
      }
      selectedRuntimeRef.current = null;
    };
  }, [selectedClipId]);

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
    // Live camera needs the preview pane — drop any library clip
    // preview that's currently driving it.
    if (selectedClipId) setSelectedClipId(null);
    mocap.startRecording();
  };
  const onStopRecord = () => {
    const wasAutoStop = autoStoppedByMaxRef.current;
    autoStoppedByMaxRef.current = false;
    const clip = mocap.stopRecording();
    if (clip) {
      const bound = { ...clip, sourceVrm: targetVrm ?? "" };
      setDraftClip(bound);
      setClipName(`${bound.sourceVrm || "clip"} ${new Date().toLocaleTimeString()}`);
      setStage("recorded");
      if (wasAutoStop) {
        setStageMessage(
          `녹화가 ${maxDurationS}초 한도에 도달해 자동 정지됨 — 업로드하거나 버리세요`,
        );
      } else {
        setStageMessage(null);
      }
    } else {
      setStageMessage("녹화가 너무 짧습니다 (2프레임 미만).");
    }
  };
  // Keep the auto-stop handler pointed at the latest ``onStopRecord``
  // closure so ``useMocap`` sees the current ``targetVrm``.
  useEffect(() => {
    autoStopRef.current = onStopRecord;
  });

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

  // ``mocap.latestSample`` is the solver's internal ref, mutated in place
  // every frame — same object identity for the lifetime of the hook.
  // Boxing it once is enough; MocapPreview reads ``.current`` via
  // ``useFrame`` and always sees the freshest bones.
  const liveSampleRef = useRef<ClipSample>(mocap.latestSample);

  // VRM bone world positions published by MocapPreview each frame. We
  // compare these against the raw pose landmarks at ~3Hz to surface a
  // coarse alignment-score badge for IK tuning.
  const vrmBonesRef = useRef<VrmBoneWorldPositions>({});
  const [alignmentScore, setAlignmentScore] = useState<number | null>(null);
  useEffect(() => {
    // Only run the sampler while the camera is live. When it isn't,
    // we skip registering the interval at all — the cleanup from the
    // previous "running" effect has already stopped the tick, and the
    // stale score stays frozen until the badge is hidden by the same
    // ``mocap.status === "running"`` guard in JSX.
    if (mocap.status !== "running") return;
    const tick = () => {
      const score = computeAlignment(
        mocap.poseLandmarksRef.current,
        vrmBonesRef.current,
        true, // selfie mirror — matches WebcamPanel / SkeletonOverlay
      );
      setAlignmentScore(score);
    };
    tick();
    const id = window.setInterval(tick, 333); // ~3Hz
    return () => {
      window.clearInterval(id);
      // Blow away stale numbers so the next session starts blank —
      // done in cleanup, not the effect body, to satisfy
      // react-hooks/set-state-in-effect.
      setAlignmentScore(null);
    };
  }, [mocap.status, mocap.poseLandmarksRef]);

  // Priority: draft-trim beats library-clip preview beats live camera.
  const previewSampleRef =
    stage === "recorded"
      ? trimSampleRef
      : selectedClipId
        ? selectedSampleRef
        : liveSampleRef;

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
              setSelectedClipId(null);
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
                overlay={
                  overlayEnabled && mocap.status === "running" ? (
                    <SkeletonOverlay
                      poseLandmarksRef={mocap.poseLandmarksRef}
                      handLandmarksRef={mocap.handLandmarksRef}
                      referencePose={referencePose}
                      matchThreshold={matchThreshold}
                      mirror={true}
                    />
                  ) : null
                }
              />
              {mocap.handsError && (
                <div className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] font-mono text-amber-200">
                  손 모델 로드 실패 — body/face만 캡처됩니다: {mocap.handsError}
                </div>
              )}
              {selectedClipId && stage !== "recorded" && (
                <div className="flex items-center gap-2 rounded border border-white/15 bg-slate-950/70 px-2 py-1 text-[11px] font-mono text-white/70">
                  <span className="text-white/50">클립 미리보기:</span>
                  <span className="truncate text-white">
                    {clipsApi.clips.find((c) => c.id === selectedClipId)?.name ??
                      "(이름 없음)"}
                  </span>
                  <button
                    type="button"
                    onClick={() => setSelectedClipId(null)}
                    className="ml-auto rounded border border-white/15 bg-slate-900/60 px-2 py-0.5 text-white/70 hover:border-white/35"
                  >
                    선택 해제
                  </button>
                </div>
              )}
              {/* Skeleton overlay controls — separate row so the
                  recording controls below stay unambiguous. */}
              <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono">
                <label className="flex cursor-pointer items-center gap-1.5 rounded border border-white/15 bg-slate-950/70 px-2 py-1 text-white/70 hover:border-white/35">
                  <input
                    type="checkbox"
                    className="accent-emerald-500"
                    checked={overlayEnabled}
                    onChange={(e) => setOverlayEnabled(e.target.checked)}
                  />
                  스켈레톤
                </label>
                <button
                  type="button"
                  onClick={captureReferencePose}
                  disabled={mocap.status !== "running"}
                  title="지금의 자세를 가이드로 저장 — 카메라가 켜져 있어야 합니다"
                  className="rounded border border-white/15 bg-slate-950/70 px-2 py-1 text-white/80 hover:border-white/35 disabled:opacity-40"
                >
                  📌 가이드 저장
                </button>
                {referencePose && (
                  <>
                    <button
                      type="button"
                      onClick={() => setReferencePose(null)}
                      className="rounded border border-white/15 bg-slate-950/70 px-2 py-1 text-white/60 hover:border-white/35"
                    >
                      가이드 지우기
                    </button>
                    <label className="flex items-center gap-1.5 text-white/50">
                      허용오차
                      <input
                        type="range"
                        min={0.04}
                        max={0.2}
                        step={0.01}
                        value={matchThreshold}
                        onChange={(e) =>
                          setMatchThreshold(parseFloat(e.target.value))
                        }
                        className="w-20 accent-emerald-500"
                      />
                      <span className="w-8 tabular-nums text-white/70">
                        {Math.round(matchThreshold * 100)}
                      </span>
                    </label>
                  </>
                )}
              </div>

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
                <label
                  title="손 캡처를 켜면 카메라 성능이 떨어질 수 있어요. 카메라가 꺼진 상태에서만 바꿀 수 있습니다."
                  className={
                    "flex items-center gap-1.5 rounded border px-2 py-1 " +
                    (mocap.status === "running"
                      ? "cursor-not-allowed border-white/10 bg-slate-950/40 text-white/30"
                      : "cursor-pointer border-white/15 bg-slate-950/70 text-white/70 hover:border-white/35")
                  }
                >
                  <input
                    type="checkbox"
                    className="accent-fuchsia-500"
                    checked={handsEnabled}
                    disabled={mocap.status === "running"}
                    onChange={(e) => setHandsEnabled(e.target.checked)}
                  />
                  손 캡처
                </label>
                {debugMode && mocap.handDiagnostics && (
                  <span
                    className="rounded border border-cyan-500/30 bg-cyan-500/5 px-2 py-1 text-cyan-200/80"
                    title="손 감지 수 · 손가락 curl(라디안). curl이 0이면 본은 움직이지 않는 상태."
                  >
                    {handDiagLabel(mocap.handDiagnostics)}
                  </span>
                )}
                {mocap.status === "running" && alignmentScore !== null && (
                  <span
                    className={
                      "rounded border px-2 py-1 tabular-nums " +
                      (alignmentScore >= 0.8
                        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
                        : alignmentScore >= 0.5
                          ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
                          : "border-rose-500/40 bg-rose-500/10 text-rose-200")
                    }
                    title="VRM 본 방향이 MediaPipe 랜드마크 방향과 얼마나 일치하는지 (평균 코사인 유사도). 80% 이상이면 IK가 잘 맞는 상태."
                  >
                    정합도 {Math.round(alignmentScore * 100)}%
                  </span>
                )}
                {/* Finger-axis bisection. Each button forces a 3-second
                    60° rotation on every finger proximal around that
                    axis. Whichever axis visibly bends the fingers is
                    the rig's curl axis. See docstring in MocapPreview.
                    Developer-only — gated behind ?debug=1. */}
                {debugMode && (
                  <>
                    <span className="text-[10px] text-white/30">축 테스트:</span>
                    {(["x", "y", "z"] as const).map((axis) => (
                      <button
                        key={axis}
                        onClick={() => runAxisTest(axis)}
                        title={`${axis.toUpperCase()}축으로 60° 회전 (3초). 손가락이 안쪽으로 굽으면 그 축이 정답.`}
                        className={
                          "rounded border px-2 py-1 " +
                          (testFingerAxis === axis
                            ? "border-amber-400/60 bg-amber-500/15 text-amber-200"
                            : "border-white/15 bg-slate-950/70 text-white/70 hover:border-white/35")
                        }
                      >
                        {axis.toUpperCase()}축
                      </button>
                    ))}
                  </>
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
                    ■ 정지 ({recordElapsed.toFixed(1)}초)
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

            <MocapPreview
              vrmFile={targetVrm}
              sampleRef={previewSampleRef}
              testFingerAxis={testFingerAxis}
              testFingerUntil={testFingerUntil}
              bonePositionsRef={vrmBonesRef}
            />
          </section>
        )}

        {targetVrm && (
          <section className="grid gap-4 md:grid-cols-2">
            <div className="flex flex-col gap-2">
              <h2 className="font-mono text-sm font-semibold text-white/80">클립 라이브러리</h2>
              <ClipLibrary
                clips={clipsApi.clips}
                loading={clipsApi.loading}
                selectedClipId={selectedClipId}
                sourceVrmFilter={targetVrm}
                currentUserId={user?.id != null ? String(user.id) : ""}
                isAdmin={isAdmin}
                onSelect={(id) =>
                  setSelectedClipId((cur) => (cur === id ? null : id))
                }
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
                isAdmin={isAdmin}
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

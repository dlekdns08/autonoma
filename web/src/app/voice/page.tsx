"use client";

/**
 * ``/voice`` page — OmniVoice reference profile manager.
 *
 *   1. Upload a short WAV sample + its transcript → profile is stored
 *      in the DB as ``voice_profiles``.
 *   2. Bind a profile to a VRM character → ``voice_bindings``. The TTS
 *      worker resolves the binding at speech time, so edits take effect
 *      on the next utterance without a restart.
 *   3. Test bench: pick a profile, type any text, listen to the
 *      synthesized WAV before committing the binding.
 *
 * Auth gating mirrors ``/mocap``: pending/denied users see a 403. Any
 * active user can upload and bind (voice bindings are global in this
 * PoC, like mocap bindings).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { useSwarm } from "@/hooks/useSwarm";
import {
  useVoiceProfiles,
  voiceProfileAudioUrl,
  testVoiceProfile,
  type VoiceProfileSummary,
} from "@/hooks/voice/useVoiceProfiles";
import { useVoiceBindings } from "@/hooks/voice/useVoiceBindings";
import { VRM_FILES, VRM_CREDITS } from "@/components/vtuber/vrmCredits";
import { StatusBox } from "@/components/StatusBox";
import PushToTalkButton from "@/components/PushToTalkButton";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function formatDuration(s: number): string {
  if (!s || !isFinite(s)) return "—";
  return `${s.toFixed(1)}s`;
}

export default function VoicePage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const isActive = user?.status === "active";
  const isAdmin = user?.role === "admin";

  // WS events are needed so that bindings edited in a second tab
  // (or by another user) propagate into this page without a reload.
  const swarm = useSwarm();
  const profilesApi = useVoiceProfiles();
  const bindingsApi = useVoiceBindings(
    swarm.voiceBindingsRefreshToken,
    swarm.voiceBindingEvent,
  );

  // Upload form state
  const [upName, setUpName] = useState("");
  const [upRefText, setUpRefText] = useState("");
  const [upFile, setUpFile] = useState<File | null>(null);
  const [upBusy, setUpBusy] = useState(false);
  const [upError, setUpError] = useState<string | null>(null);
  // Blob URL for the currently-selected upload file so the admin can
  // verify they picked the right recording before committing it.
  const [upPreviewUrl, setUpPreviewUrl] = useState<string | null>(null);

  // Test bench state. ``selectedTestProfileId`` is the user's explicit
  // selection; the effective id falls back to the first profile so the
  // select always has a valid value without an effect-driven seed.
  const [selectedTestProfileId, setSelectedTestProfileId] = useState<string>("");
  const [testText, setTestText] = useState(
    "안녕하세요, 제 목소리 테스트입니다.",
  );
  const [testBusy, setTestBusy] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [testAudioUrl, setTestAudioUrl] = useState<string | null>(null);
  const [testElapsed, setTestElapsed] = useState(0);
  const testAbortRef = useRef<AbortController | null>(null);
  const testAudioRef = useRef<HTMLAudioElement | null>(null);
  // When on, releasing the mic immediately fires the synth with the
  // transcribed text — full mic→text→speech round-trip in one gesture.
  const [autoSynth, setAutoSynth] = useState(true);
  const [micError, setMicError] = useState<string | null>(null);

  const testProfileId = useMemo(() => {
    if (selectedTestProfileId) {
      const hit = profilesApi.profiles.find(
        (p) => p.id === selectedTestProfileId,
      );
      if (hit) return hit.id;
    }
    return profilesApi.profiles[0]?.id ?? "";
  }, [selectedTestProfileId, profilesApi.profiles]);

  // Revoke the previous blob URL when a new one is created or the
  // component unmounts — otherwise every test leaks a ~100KB blob.
  useEffect(() => {
    return () => {
      if (testAudioUrl) URL.revokeObjectURL(testAudioUrl);
    };
  }, [testAudioUrl]);

  // Same leak guard for the upload preview blob.
  useEffect(() => {
    return () => {
      if (upPreviewUrl) URL.revokeObjectURL(upPreviewUrl);
    };
  }, [upPreviewUrl]);

  // Tick a 1s elapsed counter while a synth request is in flight so the
  // admin knows the page isn't frozen during slow CPU inference.
  useEffect(() => {
    if (!testBusy) {
      setTestElapsed(0);
      return;
    }
    const start = Date.now();
    const tick = () => setTestElapsed(Math.round((Date.now() - start) / 1000));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [testBusy]);

  // Cancel any in-flight synth on unmount so the server isn't synthesizing
  // audio no one will listen to.
  useEffect(() => {
    return () => {
      testAbortRef.current?.abort();
    };
  }, []);

  const bindingsByVrm = useMemo(() => {
    const m = new Map<string, string>();
    for (const b of bindingsApi.bindings) m.set(b.vrm_file, b.profile_id);
    return m;
  }, [bindingsApi.bindings]);

  const profileById = useMemo(() => {
    const m = new Map<string, VoiceProfileSummary>();
    for (const p of profilesApi.profiles) m.set(p.id, p);
    return m;
  }, [profilesApi.profiles]);

  const onUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    setUpError(null);
    if (!upFile) {
      setUpError("오디오 파일을 선택하세요.");
      return;
    }
    if (!upName.trim()) {
      setUpError("프로파일 이름을 입력하세요.");
      return;
    }
    if (!upRefText.trim()) {
      setUpError("레퍼런스 오디오의 스크립트(전사)를 입력하세요.");
      return;
    }
    setUpBusy(true);
    const res = await profilesApi.create({
      name: upName.trim(),
      refText: upRefText.trim(),
      refAudio: upFile,
    });
    setUpBusy(false);
    if (!res.ok) {
      setUpError(res.reason);
      return;
    }
    setUpName("");
    setUpRefText("");
    setUpFile(null);
    setUpPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    // Reset file input value so the same file can be picked twice in a row
    const form = e.target as HTMLFormElement;
    form.reset();
  };

  const onUpFileChange = (file: File | null) => {
    setUpFile(file);
    setUpPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return file ? URL.createObjectURL(file) : null;
    });
  };

  const onCancelTest = () => {
    testAbortRef.current?.abort();
  };

  const onDeleteProfile = async (p: VoiceProfileSummary) => {
    if (!confirm(`"${p.name}" 프로파일을 삭제할까요?`)) return;
    const res = await profilesApi.remove(p.id);
    if (!res.ok) {
      alert(`삭제 실패: ${res.reason ?? "unknown"}`);
    }
  };

  const onBind = async (vrmFile: string, profileId: string) => {
    if (!profileId) {
      await bindingsApi.remove(vrmFile);
      return;
    }
    const res = await bindingsApi.upsert(vrmFile, profileId);
    if (!res.ok) {
      alert(`바인딩 실패: ${res.reason}`);
    }
  };

  // Synthesize an arbitrary string via the currently-selected profile.
  // ``onTest`` (the button handler) reads ``testText``; the mic result
  // handler bypasses state so it doesn't have to wait for the React
  // commit before kicking off synthesis.
  const synthesize = async (text: string) => {
    if (!testProfileId) return;
    const trimmed = text.trim();
    if (!trimmed) {
      setTestError("합성할 문장이 없습니다.");
      return;
    }
    testAbortRef.current?.abort();
    const ctrl = new AbortController();
    testAbortRef.current = ctrl;
    setTestBusy(true);
    setTestError(null);
    const res = await testVoiceProfile({
      id: testProfileId,
      text: trimmed,
      signal: ctrl.signal,
    });
    setTestBusy(false);
    testAbortRef.current = null;
    if (!res.ok) {
      if (!res.aborted) setTestError(res.reason);
      return;
    }
    const url = URL.createObjectURL(res.blob);
    setTestAudioUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return url;
    });
    requestAnimationFrame(() => {
      testAudioRef.current?.play().catch(() => {
        /* user-gesture policy — ignore */
      });
    });
  };

  const onTest = async () => {
    if (testBusy) return; // defense-in-depth; button is also disabled
    if (!testText.trim()) {
      setTestError("테스트 문장을 입력하세요.");
      return;
    }
    await synthesize(testText);
  };

  // Mic round-trip: transcript lands in the textarea (so the user can
  // see + edit it), and if autoSynth is on we also kick off synthesis
  // with the fresh transcript directly — without waiting for the
  // setTestText commit to settle.
  const onMicResult = (r: { text: string; route: { action: string; detail: string } }) => {
    setMicError(null);
    if (!r.text || !r.text.trim()) {
      setMicError("전사된 텍스트가 없습니다. 다시 시도하세요.");
      return;
    }
    setTestText(r.text);
    if (autoSynth) {
      if (!testProfileId) {
        setMicError("프로파일을 먼저 선택하세요.");
        return;
      }
      void synthesize(r.text);
    }
  };

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
          <h1 className="font-mono text-2xl font-bold text-white">
            로그인이 필요합니다
          </h1>
          <p className="mt-2 font-mono text-xs text-white/50">
            /voice 페이지는 인증된 계정으로만 접근할 수 있습니다.
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

  return (
    <div className="min-h-screen bg-[#0a0a12] p-4 text-white">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-2xl font-bold text-transparent">
              음성 프로파일 스튜디오
            </h1>
            <p className="mt-1 font-mono text-xs text-white/40">
              OmniVoice 제로샷 · 레퍼런스 오디오 업로드 → 캐릭터별 음성 바인딩
            </p>
          </div>
          <button
            onClick={() => router.push("/")}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/60 hover:bg-white/10"
          >
            ← 대시보드
          </button>
        </header>

        {/* ── Upload ────────────────────────────────────────────────── */}
        <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
          <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">
            레퍼런스 업로드
          </h2>
          <form onSubmit={onUpload} className="flex flex-col gap-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-xs text-white/60">
                <span>이름</span>
                <input
                  type="text"
                  value={upName}
                  onChange={(e) => setUpName(e.target.value)}
                  maxLength={128}
                  placeholder="e.g. 차분한 여성"
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60"
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-white/60">
                <span>레퍼런스 오디오 (WAV 권장, 1–30초, ≤ 4MB)</span>
                <input
                  type="file"
                  accept="audio/wav,audio/x-wav,audio/wave,audio/mpeg,audio/ogg,audio/webm"
                  onChange={(e) => onUpFileChange(e.target.files?.[0] ?? null)}
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-xs text-white/70 file:mr-3 file:rounded-md file:border-0 file:bg-fuchsia-500/20 file:px-3 file:py-1 file:text-fuchsia-200"
                />
                {upPreviewUrl && (
                  <audio
                    controls
                    src={upPreviewUrl}
                    className="mt-1 h-8 w-full"
                  />
                )}
              </label>
            </div>
            <label className="flex flex-col gap-1 text-xs text-white/60">
              <span>레퍼런스 스크립트 (오디오에서 말하는 그대로)</span>
              <textarea
                value={upRefText}
                onChange={(e) => setUpRefText(e.target.value)}
                maxLength={2048}
                rows={3}
                placeholder="레퍼런스 오디오가 말하는 문장을 정확히 입력하세요."
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60"
              />
            </label>
            {upError && (
              <StatusBox tone="error" title="업로드 실패">
                {upError}
              </StatusBox>
            )}
            <div className="flex items-center justify-end">
              <button
                type="submit"
                disabled={upBusy}
                className="rounded-xl border border-fuchsia-400/40 bg-fuchsia-500/20 px-4 py-2 font-mono text-xs text-fuchsia-100 hover:bg-fuchsia-500/30 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {upBusy ? "업로드 중…" : "프로파일 추가"}
              </button>
            </div>
          </form>
        </section>

        {/* ── Profile list ─────────────────────────────────────────── */}
        <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
          <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">
            프로파일 목록
          </h2>
          {profilesApi.loading ? (
            <p className="font-mono text-xs text-white/40">loading…</p>
          ) : profilesApi.profiles.length === 0 ? (
            <p className="font-mono text-xs text-white/40">
              업로드된 프로파일이 아직 없습니다.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {profilesApi.profiles.map((p) => {
                const canDelete = p.owner_user_id === user.id || isAdmin;
                return (
                  <div
                    key={p.id}
                    className="flex flex-col gap-2 rounded-lg border border-white/10 bg-slate-900/40 p-3"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-sm font-semibold text-white">
                          {p.name}
                        </div>
                        <div className="mt-1 font-mono text-[10px] text-white/40">
                          {formatDuration(p.duration_s)} · {formatBytes(p.size_bytes)} ·{" "}
                          {p.ref_audio_mime}
                        </div>
                      </div>
                      {canDelete && (
                        <button
                          type="button"
                          onClick={() => onDeleteProfile(p)}
                          className="rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-1 font-mono text-[10px] text-rose-200 hover:bg-rose-500/20"
                        >
                          삭제
                        </button>
                      )}
                    </div>
                    <div className="truncate font-mono text-[10px] text-white/50">
                      “{p.ref_text}”
                    </div>
                    <audio
                      controls
                      preload="none"
                      src={voiceProfileAudioUrl(p.id)}
                      className="h-8 w-full"
                    />
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* ── Test bench ───────────────────────────────────────────── */}
        <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
          <h2 className="mb-1 font-mono text-sm font-semibold text-white/80">
            테스트 벤치
          </h2>
          <p className="mb-3 font-mono text-[10px] text-white/40">
            마이크로 말하면 한국어로 전사 → 선택한 프로파일 목소리로 합성 →
            자동 재생됩니다. 텍스트를 직접 입력해서 시험해도 됩니다.
          </p>
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-xs text-white/60">
                <span>프로파일</span>
                <select
                  value={testProfileId}
                  onChange={(e) => setSelectedTestProfileId(e.target.value)}
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60"
                >
                  {profilesApi.profiles.length === 0 && (
                    <option value="">— 프로파일 없음 —</option>
                  )}
                  {profilesApi.profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex flex-col justify-end gap-1">
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={onTest}
                    disabled={testBusy || !testProfileId}
                    className="flex-1 rounded-xl border border-cyan-400/40 bg-cyan-500/20 px-4 py-2 font-mono text-xs text-cyan-100 hover:bg-cyan-500/30 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {testBusy ? `합성 중… (${testElapsed}s)` : "합성 후 재생"}
                  </button>
                  {testBusy && (
                    <button
                      type="button"
                      onClick={onCancelTest}
                      className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 font-mono text-xs text-white/60 hover:bg-white/10"
                    >
                      취소
                    </button>
                  )}
                </div>
                {testBusy && testElapsed > 20 && (
                  <div className="font-mono text-[10px] text-white/40">
                    CPU 합성은 문장 길이에 따라 30–60초까지 걸릴 수 있습니다.
                  </div>
                )}
              </div>
            </div>

            {/* ── Mic round-trip: speech → text → speech ─────────────── */}
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-fuchsia-400/20 bg-fuchsia-500/5 px-3 py-2">
              <PushToTalkButton
                mode="stream"
                language="ko"
                route={false}
                onResult={onMicResult}
                onError={(m) => setMicError(m)}
              />
              <label className="flex items-center gap-1.5 font-mono text-[11px] text-white/60">
                <input
                  type="checkbox"
                  checked={autoSynth}
                  onChange={(e) => setAutoSynth(e.target.checked)}
                  className="h-3.5 w-3.5 accent-fuchsia-500"
                />
                녹음 끝나면 자동으로 합성·재생
              </label>
              <span className="font-mono text-[10px] text-white/35">
                Space 키도 길게 누르면 됩니다.
              </span>
            </div>

            <label className="flex flex-col gap-1 text-xs text-white/60">
              <span>테스트 문장 (1-500자)</span>
              <textarea
                value={testText}
                onChange={(e) => setTestText(e.target.value)}
                maxLength={500}
                rows={2}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-cyan-500/60"
              />
            </label>
            {micError && (
              <StatusBox tone="error" title="음성 입력 오류">
                {micError}
              </StatusBox>
            )}
            {testError && (
              <StatusBox tone="error" title="합성 실패">
                {testError}
              </StatusBox>
            )}
            {testAudioUrl && (
              <audio
                ref={testAudioRef}
                controls
                src={testAudioUrl}
                className="w-full"
              />
            )}
          </div>
        </section>

        {/* ── Binding matrix ───────────────────────────────────────── */}
        <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
          <h2 className="mb-1 font-mono text-sm font-semibold text-white/80">
            캐릭터 바인딩
          </h2>
          <p className="mb-3 font-mono text-[10px] text-white/40">
            각 VRM 파일에 사용할 음성 프로파일을 지정하세요. 변경 사항은 다음
            발화부터 즉시 반영됩니다.
          </p>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {VRM_FILES.map((vrm) => {
              const bound = bindingsByVrm.get(vrm) ?? "";
              const credit = VRM_CREDITS[vrm];
              const boundProfile = bound ? profileById.get(bound) : undefined;
              return (
                <div
                  key={vrm}
                  className="flex flex-col gap-2 rounded-lg border border-white/10 bg-slate-900/40 p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate font-mono text-sm font-semibold text-white">
                        {credit?.character ?? vrm}
                      </div>
                      <div className="truncate font-mono text-[10px] text-white/40">
                        {vrm}
                      </div>
                    </div>
                    {bound && (
                      <button
                        type="button"
                        onClick={() => onBind(vrm, "")}
                        className="rounded-md border border-white/10 bg-white/5 px-2 py-1 font-mono text-[10px] text-white/60 hover:bg-white/10"
                      >
                        해제
                      </button>
                    )}
                  </div>
                  <select
                    value={bound}
                    onChange={(e) => onBind(vrm, e.target.value)}
                    className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-xs text-white outline-none focus:border-fuchsia-500/60"
                  >
                    <option value="">— 음성 없음 (TTS 비활성) —</option>
                    {profilesApi.profiles.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                  {boundProfile && (
                    <div className="truncate font-mono text-[10px] text-white/35">
                      현재: {boundProfile.name}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}

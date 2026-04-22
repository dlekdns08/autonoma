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
  const testAudioRef = useRef<HTMLAudioElement | null>(null);

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
      setUpError(`업로드 실패: ${res.reason}`);
      return;
    }
    setUpName("");
    setUpRefText("");
    setUpFile(null);
    // Reset file input value so the same file can be picked twice in a row
    const form = e.target as HTMLFormElement;
    form.reset();
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

  const onTest = async () => {
    if (!testProfileId) return;
    if (!testText.trim()) {
      setTestError("테스트 문장을 입력하세요.");
      return;
    }
    setTestBusy(true);
    setTestError(null);
    const res = await testVoiceProfile({
      id: testProfileId,
      text: testText.trim(),
    });
    setTestBusy(false);
    if (!res.ok) {
      setTestError(`합성 실패: ${res.reason}`);
      return;
    }
    const url = URL.createObjectURL(res.blob);
    setTestAudioUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return url;
    });
    // Autoplay next frame so the <audio> src update has landed.
    requestAnimationFrame(() => {
      testAudioRef.current?.play().catch(() => {
        /* user-gesture policy — ignore */
      });
    });
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
                <span>레퍼런스 오디오 (WAV 권장, ≤ 4MB)</span>
                <input
                  type="file"
                  accept="audio/wav,audio/x-wav,audio/wave,audio/mpeg,audio/ogg,audio/webm"
                  onChange={(e) => setUpFile(e.target.files?.[0] ?? null)}
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-xs text-white/70 file:mr-3 file:rounded-md file:border-0 file:bg-fuchsia-500/20 file:px-3 file:py-1 file:text-fuchsia-200"
                />
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
              <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 font-mono text-xs text-rose-200">
                {upError}
              </div>
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
          <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">
            테스트 벤치
          </h2>
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
              <div className="flex flex-col justify-end">
                <button
                  type="button"
                  onClick={onTest}
                  disabled={testBusy || !testProfileId}
                  className="rounded-xl border border-cyan-400/40 bg-cyan-500/20 px-4 py-2 font-mono text-xs text-cyan-100 hover:bg-cyan-500/30 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {testBusy ? "합성 중…" : "합성 후 재생"}
                </button>
              </div>
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
            {testError && (
              <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 font-mono text-xs text-rose-200">
                {testError}
              </div>
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

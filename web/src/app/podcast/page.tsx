"use client";

/**
 * /podcast — multi-character dialogue stage.
 *
 * Lets the operator pick two voice profiles + a topic, then the
 * server-side orchestrator scripts a podcast-style conversation
 * between them. Live audio chunks arrive over the existing WS in
 * the ``podcast.line_audio_*`` shape; we accumulate per-line and
 * play through a single <audio> element. Listeners can interject
 * via the chat box at the bottom — those messages break the
 * current line and feed into the next LLM chunk.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { useSwarm, API_BASE_URL } from "@/hooks/useSwarm";
import { useVoiceProfiles } from "@/hooks/voice/useVoiceProfiles";
import { StatusBox } from "@/components/StatusBox";

// ── Types mirroring the server's _public_view shape ─────────────────

interface PodcastSessionDTO {
  id: string;
  status: "idle" | "running" | "paused" | "ended" | "error";
  turns_played: number;
  max_total_turns: number;
  host_name: string;
  guest_name: string;
  topic: string;
  language: string;
  history: Array<{ speaker: string; text: string }>;
  error: string | null;
}

// Per-line accumulator for the audio stream.
interface LineState {
  seq: number;
  speaker: "host" | "guest";
  speakerName: string;
  text: string;
  chunks: Uint8Array[];
  audio?: HTMLAudioElement;
  url?: string;
  finished: boolean;
}

function decodeBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export default function PodcastPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const swarm = useSwarm();
  const profilesApi = useVoiceProfiles();

  // ── Form state for a new session ─────────────────────────────────
  const [hostName, setHostName] = useState("Alice");
  const [guestName, setGuestName] = useState("Bob");
  const [hostVoiceId, setHostVoiceId] = useState("");
  const [guestVoiceId, setGuestVoiceId] = useState("");
  const [hostPersona, setHostPersona] = useState("호기심 많고 따뜻한 진행자.");
  const [guestPersona, setGuestPersona] = useState("강한 의견과 풍부한 일화를 가진 게스트.");
  const [topic, setTopic] = useState("최근 AI 음성 기술의 발전과 한계");
  const [chunkSize, setChunkSize] = useState(4);
  const [maxTurns, setMaxTurns] = useState(20);
  const [language, setLanguage] = useState("ko");

  // ── Live session state ────────────────────────────────────────────
  const [session, setSession] = useState<PodcastSessionDTO | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [interruptText, setInterruptText] = useState("");

  // ── Audio playback state machine ─────────────────────────────────
  // Map line seq → accumulator. We hold every in-flight line in case
  // chunks arrive out of order on the WS (rare but possible).
  const linesRef = useRef<Map<number, LineState>>(new Map());
  const [currentSpeaker, setCurrentSpeaker] = useState<"host" | "guest" | null>(null);
  const [currentText, setCurrentText] = useState<string>("");

  // Pre-populate voice ids when profiles load — pick the first two
  // distinct profiles so the user can click Start without configuring
  // anything more than the topic.
  useEffect(() => {
    if (profilesApi.profiles.length === 0) return;
    if (!hostVoiceId) setHostVoiceId(profilesApi.profiles[0].id);
    if (!guestVoiceId && profilesApi.profiles.length > 1) {
      setGuestVoiceId(profilesApi.profiles[1].id);
    } else if (!guestVoiceId) {
      // Only one profile available — reuse it (the operator can override).
      setGuestVoiceId(profilesApi.profiles[0].id);
    }
  }, [profilesApi.profiles, hostVoiceId, guestVoiceId]);

  // ── Subscribe to podcast.* events from the WS ────────────────────
  const lastSeenSeqRef = useRef(0);
  useEffect(() => {
    const evt = swarm.podcastEvent;
    if (!evt) return;
    if (evt.seq <= lastSeenSeqRef.current) return;
    lastSeenSeqRef.current = evt.seq;

    const data = evt.data as Record<string, unknown>;
    switch (evt.kind) {
      case "podcast.started": {
        // Server signalled the orchestrator started — refresh status.
        // Most state arrives via line events; this is mostly a hook
        // for "session started" UI affordances.
        break;
      }
      case "podcast.line_started": {
        const seq = data.seq as number;
        const speaker = data.speaker as "host" | "guest";
        const speakerName = (data.speaker_name as string) || speaker;
        const text = (data.text as string) || "";
        linesRef.current.set(seq, {
          seq,
          speaker,
          speakerName,
          text,
          chunks: [],
          finished: false,
        });
        setCurrentSpeaker(speaker);
        setCurrentText(text);
        break;
      }
      case "podcast.line_audio_chunk": {
        const seq = data.seq as number;
        const b64 = data.b64 as string | undefined;
        if (!b64) break;
        const line = linesRef.current.get(seq);
        if (!line) break;
        line.chunks.push(decodeBase64(b64));
        break;
      }
      case "podcast.line_audio_end": {
        const seq = data.seq as number;
        const line = linesRef.current.get(seq);
        if (!line) break;
        line.finished = true;
        if (line.chunks.length > 0) {
          let total = 0;
          for (const c of line.chunks) total += c.byteLength;
          const merged = new Uint8Array(total);
          let off = 0;
          for (const c of line.chunks) {
            merged.set(c, off);
            off += c.byteLength;
          }
          // OmniVoice emits WAV bytes for the whole utterance.
          const blob = new Blob([merged.buffer], { type: "audio/wav" });
          const url = URL.createObjectURL(blob);
          line.url = url;
          // Use one shared audio element so a fresh line can replace
          // the previous src cleanly. We stash on the line so the
          // GC doesn't drop the URL while it's playing.
          const audio = new Audio(url);
          line.audio = audio;
          audio.onended = () => URL.revokeObjectURL(url);
          audio.onerror = () => URL.revokeObjectURL(url);
          // Fire-and-forget — autoplay may be denied without a prior
          // user gesture, but the Start button counts as one for the
          // first line and subsequent lines inherit the gesture
          // policy on most browsers.
          audio.play().catch(() => {
            URL.revokeObjectURL(url);
          });
        }
        // Drop the chunks now that we've merged — keeps memory
        // bounded across long sessions.
        line.chunks = [];
        break;
      }
      case "podcast.line_failed": {
        const reason = (data.reason as string) || "unknown";
        setCreateError(`라인 합성 실패: ${reason}`);
        break;
      }
      case "podcast.user_input": {
        // Listener interjection acknowledged — clear the local input
        // box so the user can type the next one.
        setInterruptText("");
        break;
      }
      case "podcast.ended": {
        setCurrentSpeaker(null);
        setCurrentText("");
        // Refresh from server to get the final history.
        if (session) {
          void refreshSession(session.id);
        }
        break;
      }
    }
    // We intentionally don't list ``session`` in deps — it changes on
    // every event and would re-run this effect uselessly. The
    // refresh-on-end branch reads it through the closure, which is
    // safe because the refresh is idempotent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [swarm.podcastEvent]);

  // ── API helpers ──────────────────────────────────────────────────
  const refreshSession = useCallback(async (sid: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/podcast/sessions/${sid}`, {
        credentials: "include",
      });
      if (!res.ok) return;
      const data = (await res.json()) as PodcastSessionDTO;
      setSession(data);
    } catch {
      /* silent */
    }
  }, []);

  const onStart = useCallback(async () => {
    setCreateError(null);
    if (!hostVoiceId || !guestVoiceId) {
      setCreateError("진행자/게스트 음성 프로파일을 모두 선택하세요.");
      return;
    }
    if (!topic.trim()) {
      setCreateError("주제를 입력하세요.");
      return;
    }
    try {
      // 1. create
      const createRes = await fetch(`${API_BASE_URL}/api/podcast/sessions`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host_name: hostName,
          guest_name: guestName,
          host_voice_profile_id: hostVoiceId,
          guest_voice_profile_id: guestVoiceId,
          host_persona: hostPersona,
          guest_persona: guestPersona,
          topic,
          chunk_size: chunkSize,
          max_total_turns: maxTurns,
          language,
        }),
      });
      if (!createRes.ok) {
        const detail = await createRes.json().catch(() => ({}));
        throw new Error(detail?.detail?.message || `HTTP ${createRes.status}`);
      }
      const created = (await createRes.json()) as PodcastSessionDTO;
      setSession(created);

      // 2. start
      const startRes = await fetch(
        `${API_BASE_URL}/api/podcast/sessions/${created.id}/start`,
        { method: "POST", credentials: "include" },
      );
      if (!startRes.ok) {
        const detail = await startRes.json().catch(() => ({}));
        throw new Error(detail?.detail?.message || `start HTTP ${startRes.status}`);
      }
      const started = (await startRes.json()) as PodcastSessionDTO;
      setSession(started);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : String(err));
    }
  }, [
    hostName, guestName, hostVoiceId, guestVoiceId, hostPersona,
    guestPersona, topic, chunkSize, maxTurns, language,
  ]);

  const onStop = useCallback(async () => {
    if (!session) return;
    try {
      const res = await fetch(
        `${API_BASE_URL}/api/podcast/sessions/${session.id}/stop`,
        { method: "POST", credentials: "include" },
      );
      if (res.ok) {
        const data = (await res.json()) as PodcastSessionDTO;
        setSession(data);
      }
    } catch {
      /* silent */
    }
  }, [session]);

  const onInterrupt = useCallback(async () => {
    if (!session) return;
    const text = interruptText.trim();
    if (!text) return;
    try {
      await fetch(`${API_BASE_URL}/api/podcast/sessions/${session.id}/interrupt`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      // Local clear happens when podcast.user_input echoes back.
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : String(err));
    }
  }, [session, interruptText]);

  const isRunning = useMemo(
    () => session?.status === "running",
    [session?.status],
  );

  // ── Render ───────────────────────────────────────────────────────
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

  return (
    <div className="min-h-screen bg-[#0a0a12] p-4 text-white">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-2xl font-bold text-transparent">
              팟캐스트 스튜디오
            </h1>
            <p className="mt-1 font-mono text-xs text-white/40">
              두 캐릭터 · OmniVoice · 시청자 끼어들기 가능
            </p>
          </div>
          <button
            onClick={() => router.push("/")}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/60 hover:bg-white/10"
          >
            ← 대시보드
          </button>
        </header>

        {/* ── Setup ──────────────────────────────────────────────── */}
        <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
          <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">
            세션 설정
          </h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <label className="font-mono text-xs text-white/60">진행자 이름</label>
              <input
                type="text"
                value={hostName}
                onChange={(e) => setHostName(e.target.value)}
                disabled={isRunning}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="font-mono text-xs text-white/60">게스트 이름</label>
              <input
                type="text"
                value={guestName}
                onChange={(e) => setGuestName(e.target.value)}
                disabled={isRunning}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="font-mono text-xs text-white/60">진행자 음성</label>
              <select
                value={hostVoiceId}
                onChange={(e) => setHostVoiceId(e.target.value)}
                disabled={isRunning}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              >
                <option value="">— 선택 —</option>
                {profilesApi.profiles.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="font-mono text-xs text-white/60">게스트 음성</label>
              <select
                value={guestVoiceId}
                onChange={(e) => setGuestVoiceId(e.target.value)}
                disabled={isRunning}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              >
                <option value="">— 선택 —</option>
                {profilesApi.profiles.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1 sm:col-span-2">
              <label className="font-mono text-xs text-white/60">진행자 페르소나</label>
              <input
                type="text"
                value={hostPersona}
                onChange={(e) => setHostPersona(e.target.value)}
                disabled={isRunning}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              />
            </div>
            <div className="flex flex-col gap-1 sm:col-span-2">
              <label className="font-mono text-xs text-white/60">게스트 페르소나</label>
              <input
                type="text"
                value={guestPersona}
                onChange={(e) => setGuestPersona(e.target.value)}
                disabled={isRunning}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              />
            </div>
            <div className="flex flex-col gap-1 sm:col-span-2">
              <label className="font-mono text-xs text-white/60">주제</label>
              <textarea
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                disabled={isRunning}
                rows={2}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              />
            </div>
            <div className="flex gap-3">
              <label className="flex flex-col gap-1 flex-1">
                <span className="font-mono text-xs text-white/60">청크 크기</span>
                <input
                  type="number"
                  min={2}
                  max={8}
                  value={chunkSize}
                  onChange={(e) => setChunkSize(Number(e.target.value))}
                  disabled={isRunning}
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
                />
              </label>
              <label className="flex flex-col gap-1 flex-1">
                <span className="font-mono text-xs text-white/60">최대 턴</span>
                <input
                  type="number"
                  min={2}
                  max={80}
                  value={maxTurns}
                  onChange={(e) => setMaxTurns(Number(e.target.value))}
                  disabled={isRunning}
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
                />
              </label>
              <label className="flex flex-col gap-1 flex-1">
                <span className="font-mono text-xs text-white/60">언어</span>
                <select
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                  disabled={isRunning}
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
                >
                  <option value="ko">한국어</option>
                  <option value="en">English</option>
                  <option value="ja">日本語</option>
                </select>
              </label>
            </div>
          </div>
          {createError && (
            <div className="mt-3">
              <StatusBox tone="error" title="오류">{createError}</StatusBox>
            </div>
          )}
          <div className="mt-4 flex items-center gap-3">
            {!isRunning ? (
              <button
                type="button"
                onClick={onStart}
                disabled={profilesApi.profiles.length === 0}
                className="rounded-xl border border-fuchsia-400/40 bg-fuchsia-500/20 px-5 py-2 font-mono text-sm text-fuchsia-100 hover:bg-fuchsia-500/30 disabled:cursor-not-allowed disabled:opacity-50"
              >
                ▶ 시작
              </button>
            ) : (
              <button
                type="button"
                onClick={onStop}
                className="rounded-xl border border-rose-400/40 bg-rose-500/20 px-5 py-2 font-mono text-sm text-rose-100 hover:bg-rose-500/30"
              >
                ■ 정지
              </button>
            )}
            {session && (
              <span className="font-mono text-xs text-white/50">
                상태: {session.status} · 턴: {session.turns_played}/{session.max_total_turns}
              </span>
            )}
          </div>
        </section>

        {/* ── Live stage ─────────────────────────────────────────── */}
        {session && (
          <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
            <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">
              라이브 무대
            </h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {/* Host card */}
              <div
                className={`rounded-xl border p-4 transition-all ${
                  currentSpeaker === "host"
                    ? "border-fuchsia-400/60 bg-fuchsia-500/10 shadow-lg shadow-fuchsia-500/20"
                    : "border-white/10 bg-slate-900/40"
                }`}
              >
                <div className="font-mono text-xs text-white/40">진행자</div>
                <div className="mt-1 font-mono text-lg font-bold text-fuchsia-200">
                  {session.host_name}
                </div>
                {currentSpeaker === "host" && (
                  <div className="mt-2 font-mono text-sm text-white/80">
                    {currentText}
                  </div>
                )}
              </div>
              {/* Guest card */}
              <div
                className={`rounded-xl border p-4 transition-all ${
                  currentSpeaker === "guest"
                    ? "border-cyan-400/60 bg-cyan-500/10 shadow-lg shadow-cyan-500/20"
                    : "border-white/10 bg-slate-900/40"
                }`}
              >
                <div className="font-mono text-xs text-white/40">게스트</div>
                <div className="mt-1 font-mono text-lg font-bold text-cyan-200">
                  {session.guest_name}
                </div>
                {currentSpeaker === "guest" && (
                  <div className="mt-2 font-mono text-sm text-white/80">
                    {currentText}
                  </div>
                )}
              </div>
            </div>

            {/* Listener interrupt */}
            <div className="mt-4 flex flex-col gap-2 rounded-xl border border-amber-400/20 bg-amber-500/5 p-3">
              <div className="font-mono text-[11px] text-amber-200/70">
                시청자 끼어들기 — 메시지를 보내면 현재 발화를 멈추고 다음 턴이 반응합니다
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={interruptText}
                  onChange={(e) => setInterruptText(e.target.value)}
                  placeholder="끼어들 메시지를 입력하세요…"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void onInterrupt();
                  }}
                  className="flex-1 rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-amber-500/60"
                />
                <button
                  type="button"
                  onClick={onInterrupt}
                  disabled={!interruptText.trim() || !isRunning}
                  className="rounded-lg border border-amber-400/40 bg-amber-500/20 px-4 py-2 font-mono text-xs text-amber-100 hover:bg-amber-500/30 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  보내기
                </button>
              </div>
            </div>

            {/* History */}
            {session.history.length > 0 && (
              <div className="mt-4 flex flex-col gap-1.5 max-h-72 overflow-y-auto rounded-lg border border-white/10 bg-slate-900/40 p-3">
                {session.history.map((h, i) => (
                  <div key={i} className="font-mono text-[11px]">
                    <span
                      className={
                        h.speaker === "host"
                          ? "text-fuchsia-300"
                          : h.speaker === "guest"
                            ? "text-cyan-300"
                            : "text-amber-300"
                      }
                    >
                      {h.speaker === "host"
                        ? session.host_name
                        : h.speaker === "guest"
                          ? session.guest_name
                          : "👤 시청자"}
                      :
                    </span>{" "}
                    <span className="text-white/70">{h.text}</span>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

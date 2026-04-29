"use client";

/**
 * /podcast — N-character dialogue stage with VRM avatars.
 *
 * Setup form lets the operator define 2–6 participants (name + voice
 * profile + persona + VRM file). The server orchestrator scripts a
 * podcast-style conversation; live audio chunks arrive over the WS
 * and we play them through a per-line <audio> element fed through
 * an AnalyserNode so each speaker's VRM can lip-sync to its own
 * mouth-amplitude.
 *
 * Listeners can interject by typing or pushing the mic button —
 * either way the text flows into the next LLM chunk so participants
 * can react. Pause/resume parks the orchestrator at the next
 * inter-turn boundary without dropping queued context.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { useSwarm, API_BASE_URL } from "@/hooks/useSwarm";
import { useVoiceProfiles } from "@/hooks/voice/useVoiceProfiles";
import { StatusBox } from "@/components/StatusBox";
import VRMCharacter from "@/components/vtuber/VRMCharacter";
import { VRM_FILES, VRM_CREDITS } from "@/components/vtuber/vrmCredits";
import type { AgentData } from "@/lib/types";

// ── Local types mirroring the server's _public_view shape ───────────

interface ParticipantDTO {
  name: string;
  voice_profile_id: string;
  persona: string;
  vrm_file: string;
}

interface PodcastSessionDTO {
  id: string;
  status: "idle" | "running" | "paused" | "ended" | "error";
  turns_played: number;
  max_total_turns: number;
  topic: string;
  language: string;
  participants: ParticipantDTO[];
  history: Array<{ speaker: string; text: string }>;
  error: string | null;
}

// Per-participant audio + lip-sync state. We hold a persistent
// <audio> element + AnalyserNode per participant so the VRM tile can
// poll mouth amplitude every render frame without rebuilding the
// graph on every line.
interface VoiceSlot {
  audio: HTMLAudioElement;
  analyser: AnalyserNode | null;
  source: MediaElementAudioSourceNode | null;
  // ``Uint8Array<ArrayBuffer>`` (not the looser ArrayBufferLike) so
  // ``getByteTimeDomainData`` accepts the buffer under TS 5.7+'s
  // tighter typed-array generics.
  buf: Uint8Array<ArrayBuffer> | null;
  amp: number;
  // ``seq`` of the line currently feeding this slot (so out-of-order
  // chunks for a superseded line are dropped).
  currentSeq: number;
  pendingChunks: Uint8Array[];
}

function decodeBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// Build a minimal AgentData stub that satisfies the VRMCharacter
// prop contract — only ``name`` and ``state``/``mood`` strings drive
// behaviour; everything else has visual defaults.
function makeStubAgent(name: string, isSpeaking: boolean): AgentData {
  return {
    name,
    emoji: "🎙️",
    role: "podcast",
    color: "#a78bfa",
    position: { x: 0, y: 0 },
    state: isSpeaking ? "talking" : "idle",
    mood: isSpeaking ? "excited" : "relaxed",
    level: 1,
    xp: 0,
    xp_to_next: 100,
  };
}

// ── Page ───────────────────────────────────────────────────────────

export default function PodcastPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();
  const swarm = useSwarm();
  const profilesApi = useVoiceProfiles();

  // ── Setup form ─────────────────────────────────────────────────
  type FormParticipant = {
    name: string;
    voice_profile_id: string;
    persona: string;
    vrm_file: string;
  };
  const [participants, setParticipants] = useState<FormParticipant[]>([
    { name: "Alice", voice_profile_id: "", persona: "호기심 많은 진행자.", vrm_file: "" },
    { name: "Bob", voice_profile_id: "", persona: "강한 의견의 게스트.", vrm_file: "" },
  ]);
  const [topic, setTopic] = useState("최근 AI 음성 기술의 발전과 한계");
  const [chunkSize, setChunkSize] = useState(4);
  const [maxTurns, setMaxTurns] = useState(20);
  const [language, setLanguage] = useState("ko");

  // Auto-fill the first two voice + VRM defaults once the lists load.
  useEffect(() => {
    if (profilesApi.profiles.length === 0) return;
    setParticipants((prev) => {
      const next = [...prev];
      let mutated = false;
      for (let i = 0; i < next.length; i++) {
        if (!next[i].voice_profile_id) {
          next[i] = {
            ...next[i],
            voice_profile_id:
              profilesApi.profiles[i % profilesApi.profiles.length]?.id ?? "",
          };
          mutated = true;
        }
        if (!next[i].vrm_file) {
          next[i] = {
            ...next[i],
            vrm_file: VRM_FILES[i % VRM_FILES.length] ?? "",
          };
          mutated = true;
        }
      }
      return mutated ? next : prev;
    });
  }, [profilesApi.profiles]);

  const addParticipant = useCallback(() => {
    setParticipants((prev) => {
      if (prev.length >= 6) return prev;
      const idx = prev.length;
      return [
        ...prev,
        {
          name: `Speaker${idx + 1}`,
          voice_profile_id:
            profilesApi.profiles[idx % Math.max(1, profilesApi.profiles.length)]?.id ?? "",
          persona: "",
          vrm_file: VRM_FILES[idx % VRM_FILES.length] ?? "",
        },
      ];
    });
  }, [profilesApi.profiles]);

  const removeParticipant = useCallback((idx: number) => {
    setParticipants((prev) => {
      if (prev.length <= 2) return prev; // keep min cast
      return prev.filter((_, i) => i !== idx);
    });
  }, []);

  const updateParticipant = useCallback(
    (idx: number, patch: Partial<FormParticipant>) => {
      setParticipants((prev) =>
        prev.map((p, i) => (i === idx ? { ...p, ...patch } : p)),
      );
    },
    [],
  );

  // ── Session state ─────────────────────────────────────────────
  const [session, setSession] = useState<PodcastSessionDTO | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [interruptText, setInterruptText] = useState("");
  const [currentSpeaker, setCurrentSpeaker] = useState<string | null>(null);
  const [currentText, setCurrentText] = useState("");

  // ── Audio playback per participant ────────────────────────────
  // Slots keyed by speaker name. AudioContext is shared.
  const slotsRef = useRef<Map<string, VoiceSlot>>(new Map());
  const audioCtxRef = useRef<AudioContext | null>(null);
  // Per-line accumulator so chunks landing across multiple ws frames
  // are merged before playback. Keyed by ``${speaker}:${seq}``.
  const lineBufRef = useRef<Map<string, Uint8Array[]>>(new Map());

  const ensureCtx = useCallback((): AudioContext | null => {
    if (typeof window === "undefined") return null;
    if (audioCtxRef.current) return audioCtxRef.current;
    type W = Window & { webkitAudioContext?: typeof AudioContext };
    const Ctor = window.AudioContext ?? (window as W).webkitAudioContext;
    if (!Ctor) return null;
    audioCtxRef.current = new Ctor();
    return audioCtxRef.current;
  }, []);

  const ensureSlot = useCallback(
    (speaker: string): VoiceSlot => {
      const existing = slotsRef.current.get(speaker);
      if (existing) return existing;
      const audio = new Audio();
      audio.preload = "auto";
      audio.loop = false;
      const slot: VoiceSlot = {
        audio,
        analyser: null,
        source: null,
        buf: null,
        amp: 0,
        currentSeq: -1,
        pendingChunks: [],
      };
      slotsRef.current.set(speaker, slot);
      return slot;
    },
    [],
  );

  const connectAnalyser = useCallback(
    (slot: VoiceSlot) => {
      if (slot.source && slot.analyser) return;
      const ctx = ensureCtx();
      if (!ctx) return;
      if (ctx.state === "suspended") void ctx.resume().catch(() => {});
      try {
        const src = ctx.createMediaElementSource(slot.audio);
        const an = ctx.createAnalyser();
        an.fftSize = 256;
        an.smoothingTimeConstant = 0.6;
        src.connect(an);
        an.connect(ctx.destination);
        slot.source = src;
        slot.analyser = an;
        slot.buf = new Uint8Array(an.fftSize);
      } catch {
        /* createMediaElementSource only works once; subsequent calls
           land here harmlessly. */
      }
    },
    [ensureCtx],
  );

  const getMouthAmplitude = useCallback((agentName: string) => {
    const slot = slotsRef.current.get(agentName);
    if (!slot) return 0;
    if (!slot.analyser || !slot.buf) {
      slot.amp *= 0.85;
      return slot.amp;
    }
    if (slot.audio.paused || slot.audio.ended) {
      slot.amp *= 0.85;
      return slot.amp;
    }
    slot.analyser.getByteTimeDomainData(slot.buf);
    let sum = 0;
    for (let i = 0; i < slot.buf.length; i++) {
      const v = (slot.buf[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / slot.buf.length);
    const target = Math.min(1, rms * 4);
    slot.amp = slot.amp * 0.5 + target * 0.5;
    return slot.amp;
  }, []);

  // ── WS event handler ─────────────────────────────────────────
  // Drain the ref-backed queue every time useSwarm bumps the tick.
  // Single-slot state would coalesce rapid-fire line_started /
  // audio_chunk frames under React 18 batching — losing the
  // line_started would leave ``slot.currentSeq`` stuck at -1 and
  // every subsequent audio_end's ``currentSeq !== seq`` check would
  // skip playback, exactly the "첫 말 이후 다음으로 안 넘어가"
  // symptom.
  const lastSeenSeqRef = useRef(0);
  useEffect(() => {
    const queue = swarm.podcastEventQueue.current;
    while (queue.length > 0) {
      const evt = queue.shift()!;
      if (evt.seq <= lastSeenSeqRef.current) continue;
      lastSeenSeqRef.current = evt.seq;

      const data = evt.data as Record<string, unknown>;
      switch (evt.kind) {
        case "podcast.line_started": {
          const speaker = data.speaker as string;
          const text = (data.text as string) || "";
          setCurrentSpeaker(speaker);
          setCurrentText(text);
          const seq = data.seq as number;
          const slot = ensureSlot(speaker);
          slot.currentSeq = seq;
          lineBufRef.current.set(`${speaker}:${seq}`, []);
          break;
        }
        case "podcast.line_audio_chunk": {
          const speaker = data.speaker as string | undefined;
          const seq = data.seq as number;
          const b64 = data.b64 as string | undefined;
          if (!b64) break;
          // Speaker isn't repeated on chunk frames in the original
          // schema, but we put it on for clarity. Fall back to looking
          // up the line buffer purely by seq across every speaker.
          if (speaker) {
            const buf = lineBufRef.current.get(`${speaker}:${seq}`);
            if (buf) buf.push(decodeBase64(b64));
          } else {
            // Fall back: scan all open lines for this seq.
            for (const [key, buf] of lineBufRef.current) {
              if (key.endsWith(`:${seq}`)) {
                buf.push(decodeBase64(b64));
                break;
              }
            }
          }
          break;
        }
        case "podcast.line_audio_end": {
          const speaker = data.speaker as string;
          const seq = data.seq as number;
          const key = `${speaker}:${seq}`;
          const chunks = lineBufRef.current.get(key);
          lineBufRef.current.delete(key);
          if (!chunks || chunks.length === 0) break;
          const slot = ensureSlot(speaker);
          if (slot.currentSeq !== seq) break; // superseded
          let total = 0;
          for (const c of chunks) total += c.byteLength;
          const merged = new Uint8Array(total);
          let off = 0;
          for (const c of chunks) {
            merged.set(c, off);
            off += c.byteLength;
          }
          // Pass the Uint8Array view directly — TS18 narrows
          // ``ArrayBuffer`` more strictly than ``ArrayBufferLike``,
          // and typed-array views are accepted by ``Blob`` either way.
          const blob = new Blob([merged], { type: "audio/wav" });
          const url = URL.createObjectURL(blob);
          const prev = slot.audio.src;
          slot.audio.src = url;
          slot.audio.onended = () => URL.revokeObjectURL(url);
          slot.audio.onerror = () => URL.revokeObjectURL(url);
          connectAnalyser(slot);
          slot.audio.play().catch(() => {
            URL.revokeObjectURL(url);
          });
          if (prev && prev.startsWith("blob:")) {
            window.setTimeout(() => URL.revokeObjectURL(prev), 1000);
          }
          break;
        }
        case "podcast.line_failed": {
          const reason = (data.reason as string) || "unknown";
          setCreateError(`라인 합성 실패: ${reason}`);
          break;
        }
        case "podcast.user_input": {
          setInterruptText("");
          break;
        }
        case "podcast.paused":
        case "podcast.resumed":
        case "podcast.ended": {
          // Refresh from server for status truth.
          if (session) void refreshSession(session.id);
          if (evt.kind === "podcast.ended") {
            setCurrentSpeaker(null);
            setCurrentText("");
          }
          break;
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [swarm.podcastEventTick]);

  // ── API helpers ──────────────────────────────────────────────
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
    if (participants.some((p) => !p.voice_profile_id)) {
      setCreateError("모든 참가자에 음성 프로파일을 지정하세요.");
      return;
    }
    if (!topic.trim()) {
      setCreateError("주제를 입력하세요.");
      return;
    }
    try {
      const createRes = await fetch(`${API_BASE_URL}/api/podcast/sessions`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          participants,
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
  }, [participants, topic, chunkSize, maxTurns, language]);

  const sessionAction = useCallback(
    async (action: "stop" | "pause" | "resume") => {
      if (!session) return;
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/podcast/sessions/${session.id}/${action}`,
          { method: "POST", credentials: "include" },
        );
        if (res.ok) {
          const data = (await res.json()) as PodcastSessionDTO;
          setSession(data);
        }
      } catch {
        /* silent */
      }
    },
    [session],
  );

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
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : String(err));
    }
  }, [session, interruptText]);

  // Mic interrupt — uses the existing PushToTalkButton with route=false
  // (Cohere transcribes but doesn't fan out through ExternalInputRouter).
  // The transcript is then fed straight into /interrupt so the next
  // dialogue chunk reacts to it.
  const onMicTranscript = useCallback(
    async (r: { text: string }) => {
      if (!session) return;
      const text = (r.text || "").trim();
      if (!text) return;
      setInterruptText(text);
      try {
        await fetch(`${API_BASE_URL}/api/podcast/sessions/${session.id}/interrupt`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
      } catch {
        /* silent */
      }
    },
    [session],
  );

  const isRunning = useMemo(() => session?.status === "running", [session?.status]);
  const isPaused = useMemo(() => session?.status === "paused", [session?.status]);

  // ── Cleanup ──────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      for (const slot of slotsRef.current.values()) {
        try {
          slot.audio.pause();
          slot.audio.removeAttribute("src");
        } catch {
          /* ignore */
        }
      }
      slotsRef.current.clear();
      lineBufRef.current.clear();
      const ctx = audioCtxRef.current;
      if (ctx) {
        void ctx.close().catch(() => {});
        audioCtxRef.current = null;
      }
    };
  }, []);

  // ── Render guards ────────────────────────────────────────────
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
      <div className="mx-auto flex max-w-7xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-2xl font-bold text-transparent">
              팟캐스트 스튜디오
            </h1>
            <p className="mt-1 font-mono text-xs text-white/40">
              2~6명 참가자 · OmniVoice · VRM 캐릭터 · 시청자 끼어들기
            </p>
          </div>
          <button
            onClick={() => router.push("/")}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/60 hover:bg-white/10"
          >
            ← 대시보드
          </button>
        </header>

        {/* ── Setup ──────────────────────────────────────────── */}
        <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-mono text-sm font-semibold text-white/80">
              참가자 ({participants.length}/6)
            </h2>
            <button
              type="button"
              onClick={addParticipant}
              disabled={isRunning || isPaused || participants.length >= 6}
              className="rounded-md border border-fuchsia-400/40 bg-fuchsia-500/10 px-3 py-1 font-mono text-xs text-fuchsia-200 hover:bg-fuchsia-500/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              + 참가자 추가
            </button>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {participants.map((p, i) => (
              <div
                key={i}
                className="flex flex-col gap-2 rounded-xl border border-white/10 bg-slate-900/40 p-3"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] text-white/40">
                    참가자 #{i + 1}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeParticipant(i)}
                    disabled={isRunning || isPaused || participants.length <= 2}
                    className="rounded px-2 py-0.5 font-mono text-[10px] text-rose-300/70 hover:text-rose-200 disabled:cursor-not-allowed disabled:opacity-30"
                    title={participants.length <= 2 ? "최소 2명 필요" : "삭제"}
                  >
                    삭제
                  </button>
                </div>
                <input
                  type="text"
                  value={p.name}
                  onChange={(e) => updateParticipant(i, { name: e.target.value })}
                  disabled={isRunning || isPaused}
                  placeholder="이름"
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
                />
                <select
                  value={p.voice_profile_id}
                  onChange={(e) =>
                    updateParticipant(i, { voice_profile_id: e.target.value })
                  }
                  disabled={isRunning || isPaused}
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
                >
                  <option value="">— 음성 프로파일 선택 —</option>
                  {profilesApi.profiles.map((prof) => (
                    <option key={prof.id} value={prof.id}>
                      {prof.name}
                    </option>
                  ))}
                </select>
                <select
                  value={p.vrm_file}
                  onChange={(e) => updateParticipant(i, { vrm_file: e.target.value })}
                  disabled={isRunning || isPaused}
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
                >
                  <option value="">— VRM 캐릭터 선택 —</option>
                  {VRM_FILES.map((f) => (
                    <option key={f} value={f}>
                      {VRM_CREDITS[f]?.character ?? f}
                    </option>
                  ))}
                </select>
                <input
                  type="text"
                  value={p.persona}
                  onChange={(e) => updateParticipant(i, { persona: e.target.value })}
                  disabled={isRunning || isPaused}
                  placeholder="페르소나 (선택)"
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-xs text-white/80 outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
                />
              </div>
            ))}
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 sm:col-span-2">
              <span className="font-mono text-xs text-white/60">주제</span>
              <textarea
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                disabled={isRunning || isPaused}
                rows={2}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-xs text-white/60">청크 크기</span>
              <input
                type="number"
                min={2}
                max={8}
                value={chunkSize}
                onChange={(e) => setChunkSize(Number(e.target.value))}
                disabled={isRunning || isPaused}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-xs text-white/60">최대 턴</span>
              <input
                type="number"
                min={2}
                max={80}
                value={maxTurns}
                onChange={(e) => setMaxTurns(Number(e.target.value))}
                disabled={isRunning || isPaused}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-xs text-white/60">언어</span>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                disabled={isRunning || isPaused}
                className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60 disabled:opacity-50"
              >
                <option value="ko">한국어</option>
                <option value="en">English</option>
                <option value="ja">日本語</option>
              </select>
            </label>
          </div>

          {createError && (
            <div className="mt-3">
              <StatusBox tone="error" title="오류">{createError}</StatusBox>
            </div>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            {!isRunning && !isPaused ? (
              <button
                type="button"
                onClick={onStart}
                disabled={profilesApi.profiles.length === 0}
                className="rounded-xl border border-fuchsia-400/40 bg-fuchsia-500/20 px-5 py-2 font-mono text-sm text-fuchsia-100 hover:bg-fuchsia-500/30 disabled:cursor-not-allowed disabled:opacity-50"
              >
                ▶ 시작
              </button>
            ) : (
              <>
                {isRunning && (
                  <button
                    type="button"
                    onClick={() => sessionAction("pause")}
                    className="rounded-xl border border-amber-400/40 bg-amber-500/20 px-4 py-2 font-mono text-sm text-amber-100 hover:bg-amber-500/30"
                  >
                    ⏸ 일시정지
                  </button>
                )}
                {isPaused && (
                  <button
                    type="button"
                    onClick={() => sessionAction("resume")}
                    className="rounded-xl border border-emerald-400/40 bg-emerald-500/20 px-4 py-2 font-mono text-sm text-emerald-100 hover:bg-emerald-500/30"
                  >
                    ▶ 재개
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => sessionAction("stop")}
                  className="rounded-xl border border-rose-400/40 bg-rose-500/20 px-4 py-2 font-mono text-sm text-rose-100 hover:bg-rose-500/30"
                >
                  ■ 정지
                </button>
              </>
            )}
            {session && (
              <span className="font-mono text-xs text-white/50">
                상태: {session.status} · 턴: {session.turns_played}/
                {session.max_total_turns}
              </span>
            )}
          </div>
        </section>

        {/* ── VRM Stage ──────────────────────────────────────── */}
        {session && (
          <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
            <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">
              라이브 무대
            </h2>
            <div
              className="grid gap-3"
              style={{
                gridTemplateColumns: `repeat(${Math.min(session.participants.length, 3)}, minmax(0, 1fr))`,
              }}
            >
              {session.participants.map((p) => {
                const isSpeaking = currentSpeaker === p.name;
                return (
                  <div
                    key={p.name}
                    className={`flex flex-col items-stretch overflow-hidden rounded-xl border transition-all ${
                      isSpeaking
                        ? "border-fuchsia-400/60 bg-fuchsia-500/5 shadow-lg shadow-fuchsia-500/20"
                        : "border-white/10 bg-slate-900/40"
                    }`}
                    style={{ minHeight: 320 }}
                  >
                    <div
                      className="relative flex-1"
                      style={{ minHeight: 220 }}
                    >
                      <VRMCharacter
                        agent={makeStubAgent(p.name, isSpeaking)}
                        getMouthAmplitude={getMouthAmplitude}
                        spotlight={false}
                        state={isSpeaking ? "talking" : "idle"}
                        vrmFileOverride={p.vrm_file || undefined}
                      />
                    </div>
                    <div className="border-t border-white/10 bg-slate-950/60 px-3 py-2">
                      <div className="flex items-baseline justify-between gap-2">
                        <span
                          className={`font-mono text-sm font-bold ${
                            isSpeaking ? "text-fuchsia-200" : "text-white/70"
                          }`}
                        >
                          {p.name}
                        </span>
                        {isSpeaking && (
                          <span className="font-mono text-[9px] uppercase tracking-wider text-fuchsia-400/80">
                            ● speaking
                          </span>
                        )}
                      </div>
                      {isSpeaking && (
                        <div className="mt-1 max-h-12 overflow-hidden font-mono text-[11px] leading-snug text-white/70">
                          {currentText}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Listener interrupt */}
            <div className="mt-4 flex flex-col gap-2 rounded-xl border border-amber-400/20 bg-amber-500/5 p-3">
              <div className="font-mono text-[11px] text-amber-200/70">
                시청자 끼어들기 — 텍스트 또는 마이크. 다음 턴이 자동 반응합니다
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  value={interruptText}
                  onChange={(e) => setInterruptText(e.target.value)}
                  placeholder="끼어들 메시지를 입력…"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void onInterrupt();
                  }}
                  className="flex-1 min-w-[220px] rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-amber-500/60"
                />
                <button
                  type="button"
                  onClick={onInterrupt}
                  disabled={!interruptText.trim() || (!isRunning && !isPaused)}
                  className="rounded-lg border border-amber-400/40 bg-amber-500/20 px-4 py-2 font-mono text-xs text-amber-100 hover:bg-amber-500/30 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  보내기
                </button>
                {/* Mic interrupt hidden while vibevoice TTS is in use
                    — Cohere ASR (which transcribes the mic) is on a
                    different transformers major version than vibevoice.
                    Text interrupt above stays. */}
              </div>
            </div>

            {/* History */}
            {session.history.length > 0 && (
              <div className="mt-4 flex flex-col gap-1.5 max-h-72 overflow-y-auto rounded-lg border border-white/10 bg-slate-900/40 p-3">
                {session.history.map((h, i) => (
                  <div key={i} className="font-mono text-[11px]">
                    <span
                      className={
                        h.speaker === "listener" ? "text-amber-300" : "text-cyan-300"
                      }
                    >
                      {h.speaker === "listener" ? "👤 시청자" : h.speaker}:
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

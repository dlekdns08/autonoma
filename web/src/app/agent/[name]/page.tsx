"use client";

/**
 * ``/agent/<name>`` — public-ish profile page for a persisted character.
 *
 * Reads from ``/api/agents/{name}/profile`` and renders:
 *   - header: species / emoji / rarity / level / catchphrase
 *   - lifetime counters (runs, XP, tasks, files)
 *   - traits + stat bars
 *   - recent journal entries (diary / memory / note / lore)
 *   - outbound relationships sorted by trust
 *
 * Auth: mirrors the rest of the admin UI — any active user may view.
 * Pin-note action is enabled but the server scopes the actor by cookie;
 * the UI simply lets you type and send.
 */

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { useAgentProfile, type AgentJournalEntry } from "@/hooks/useAgentProfile";
import { StatusBox } from "@/components/StatusBox";
import TradingCard from "@/components/TradingCard";

const RARITY_COLOR: Record<string, string> = {
  legendary: "from-amber-400 to-rose-500",
  rare: "from-cyan-400 to-violet-500",
  uncommon: "from-emerald-400 to-teal-500",
  common: "from-slate-400 to-slate-600",
};

function Stat({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = Math.max(0, Math.min(100, (value / Math.max(1, max)) * 100));
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 font-mono text-[11px] uppercase text-white/50">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded bg-white/10">
        <div className="h-full bg-gradient-to-r from-fuchsia-400 to-cyan-400" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-6 text-right font-mono text-xs text-white/70">{value}</span>
    </div>
  );
}

const KIND_ICON: Record<string, string> = {
  diary: "📔",
  memory: "💭",
  lore: "✨",
  note: "📌",
};

function JournalLine({ entry }: { entry: AgentJournalEntry }) {
  const icon = KIND_ICON[entry.kind] ?? "•";
  return (
    <li className="rounded border border-white/10 bg-slate-900/40 px-3 py-2">
      <div className="flex items-center gap-2 font-mono text-[10px] uppercase text-white/40">
        <span>{icon}</span>
        <span>{entry.kind}</span>
        {entry.round ? <span>R{entry.round}</span> : null}
        {entry.mood ? <span>· {entry.mood}</span> : null}
        <span className="ml-auto">{entry.at.replace("T", " ").slice(0, 16)}</span>
      </div>
      <div className="mt-1 whitespace-pre-wrap font-mono text-sm text-white/85">{entry.text}</div>
    </li>
  );
}

export default function AgentProfilePage() {
  const router = useRouter();
  const params = useParams<{ name: string }>();
  const name = params?.name ? decodeURIComponent(params.name) : "";
  const { user, loading: authLoading } = useAuth();
  const { profile, loading, error, pinNote } = useAgentProfile(name);
  const [noteText, setNoteText] = useState("");
  const [noteBusy, setNoteBusy] = useState(false);
  const [noteStatus, setNoteStatus] = useState<string | null>(null);

  const onPin = async () => {
    const trimmed = noteText.trim();
    if (!trimmed) return;
    setNoteBusy(true);
    setNoteStatus(null);
    const res = await pinNote(trimmed);
    setNoteBusy(false);
    if (res.ok) {
      setNoteText("");
      setNoteStatus("노트가 추가되었습니다.");
    } else {
      setNoteStatus(`실패: ${res.reason ?? "unknown"}`);
    }
  };

  if (authLoading || loading) {
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
  if (error || !profile) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0a0a12] p-4">
        <div className="max-w-md">
          <StatusBox tone="error" title="캐릭터를 찾을 수 없습니다">
            {error ?? "알 수 없는 오류"}
          </StatusBox>
          <button
            onClick={() => router.push("/")}
            className="mt-3 rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/70 hover:bg-white/10"
          >
            ← 대시보드
          </button>
        </div>
      </div>
    );
  }

  const { character, journal, relationships, runs } = profile;
  const rarityGradient = RARITY_COLOR[character.rarity] ?? RARITY_COLOR.common;

  return (
    <div className="min-h-screen bg-[#0a0a12] p-4 text-white">
      <div className="mx-auto flex max-w-5xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <Link href="/" className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/60 hover:bg-white/10">
            ← 대시보드
          </Link>
          <div className="font-mono text-xs text-white/30">agent profile</div>
        </header>

        {/* ── Identity ─────────────────────────────────────────── */}
        <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-6">
          <div className="flex flex-wrap items-center gap-4">
            <div
              className={`flex h-20 w-20 items-center justify-center rounded-2xl bg-gradient-to-br ${rarityGradient} text-5xl shadow-lg`}
            >
              {character.species_emoji || "❔"}
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="truncate bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-3xl font-bold text-transparent">
                {character.name}
              </h1>
              <div className="mt-1 font-mono text-xs text-white/60">
                {character.role} · {character.species} · <span className="uppercase">{character.rarity}</span> ·
                Lv. {character.level}
              </div>
              {character.catchphrase ? (
                <div className="mt-2 font-mono text-sm text-white/75 italic">“{character.catchphrase}”</div>
              ) : null}
              <div className="mt-2 flex gap-2 font-mono text-[10px] text-white/40">
                {character.is_alive ? (
                  <span className="rounded border border-emerald-500/40 bg-emerald-500/10 px-2 py-0.5 text-emerald-200">
                    ALIVE
                  </span>
                ) : (
                  <span className="rounded border border-rose-500/40 bg-rose-500/10 px-2 py-0.5 text-rose-200">
                    RETIRED
                  </span>
                )}
                <span>first seen {character.first_seen.slice(0, 10)}</span>
                <span>last seen {character.last_seen.slice(0, 10)}</span>
              </div>
            </div>
            <div className="flex gap-6 font-mono text-sm">
              <Counter label="런" value={runs} />
              <Counter label="생존" value={character.runs_survived} />
              <Counter label="사망" value={character.runs_died} />
              <Counter label="XP" value={character.total_xp} />
            </div>
          </div>
        </section>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {/* ── Stats + traits ─────────────────────────────────── */}
          <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
            <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">스탯</h2>
            <div className="flex flex-col gap-2">
              {Object.entries(character.stats).map(([k, v]) => (
                <Stat key={k} label={k} value={v} max={10} />
              ))}
            </div>
            <h2 className="mb-2 mt-4 font-mono text-sm font-semibold text-white/80">특성</h2>
            <div className="flex flex-wrap gap-1.5">
              {character.traits.length === 0 ? (
                <span className="font-mono text-xs text-white/40">—</span>
              ) : (
                character.traits.map((t) => (
                  <span
                    key={t}
                    className="rounded-full border border-fuchsia-400/40 bg-fuchsia-500/10 px-2 py-0.5 font-mono text-[11px] text-fuchsia-200"
                  >
                    {t}
                  </span>
                ))
              )}
            </div>
            <div className="mt-4 rounded border border-white/10 bg-slate-900/40 p-2 font-mono text-[10px] text-white/40">
              <div>uuid: <span className="text-white/60">{character.uuid}</span></div>
              <div>tasks: {character.tasks_completed} · files: {character.files_created}</div>
              {character.last_mood ? <div>last mood: {character.last_mood}</div> : null}
            </div>
          </section>

          {/* ── Journal ────────────────────────────────────────── */}
          <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4 lg:col-span-2">
            <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">
              일지 {journal.length > 0 ? `(${journal.length})` : ""}
            </h2>
            {journal.length === 0 ? (
              <p className="font-mono text-xs text-white/40">아직 기록이 없습니다.</p>
            ) : (
              <ul className="flex max-h-[480px] flex-col gap-2 overflow-y-auto pr-1">
                {journal.map((entry, idx) => (
                  <JournalLine key={`${entry.at}-${idx}`} entry={entry} />
                ))}
              </ul>
            )}

            {/* Pin-note composer */}
            <div className="mt-4 flex flex-col gap-2 border-t border-white/10 pt-3">
              <label className="flex flex-col gap-1 text-xs text-white/60">
                <span>📌 노트 추가</span>
                <textarea
                  value={noteText}
                  onChange={(e) => setNoteText(e.target.value)}
                  maxLength={2000}
                  rows={2}
                  placeholder="이 캐릭터에 대한 메모를 적어주세요."
                  className="rounded-lg border border-white/10 bg-slate-900/60 px-3 py-2 font-mono text-sm text-white outline-none focus:border-fuchsia-500/60"
                />
              </label>
              <div className="flex items-center justify-end gap-2">
                {noteStatus ? (
                  <span className="font-mono text-[11px] text-white/50">{noteStatus}</span>
                ) : null}
                <button
                  onClick={onPin}
                  disabled={noteBusy || !noteText.trim()}
                  className="rounded-xl border border-fuchsia-400/40 bg-fuchsia-500/20 px-4 py-2 font-mono text-xs text-fuchsia-100 hover:bg-fuchsia-500/30 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {noteBusy ? "추가 중…" : "노트 핀"}
                </button>
              </div>
            </div>
          </section>
        </div>

        {/* ── Trading card export ─────────────────────────────── */}
        <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
          <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">
            트레이딩 카드
          </h2>
          <TradingCard profile={profile} />
        </section>

        {/* ── Relationships ───────────────────────────────────── */}
        <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
          <h2 className="mb-3 font-mono text-sm font-semibold text-white/80">
            관계 {relationships.length > 0 ? `(${relationships.length})` : ""}
          </h2>
          {relationships.length === 0 ? (
            <p className="font-mono text-xs text-white/40">기록된 관계가 없습니다.</p>
          ) : (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {relationships.map((r) => (
                <Link
                  key={r.to_uuid}
                  href={`/agent/${encodeURIComponent(r.to_uuid)}`}
                  className="flex flex-col gap-1 rounded border border-white/10 bg-slate-900/40 px-3 py-2 transition hover:border-fuchsia-400/40"
                >
                  <div className="flex items-center justify-between">
                    <span className="truncate font-mono text-xs text-white/80">{r.to_uuid.slice(0, 18)}…</span>
                    <span className="rounded px-1.5 py-0.5 font-mono text-[10px] uppercase text-white/50">
                      {r.sentiment}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded bg-white/10">
                      <div
                        className="h-full bg-gradient-to-r from-rose-400 via-yellow-400 to-emerald-400"
                        style={{ width: `${Math.max(0, Math.min(100, r.trust * 100))}%` }}
                      />
                    </div>
                    <span className="w-10 text-right font-mono text-[10px] text-white/50">
                      {r.trust.toFixed(2)}
                    </span>
                  </div>
                  {r.last_interaction ? (
                    <div className="truncate font-mono text-[10px] text-white/40">“{r.last_interaction}”</div>
                  ) : null}
                </Link>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex flex-col items-center">
      <span className="font-mono text-lg font-bold text-white">{value}</span>
      <span className="font-mono text-[10px] uppercase text-white/40">{label}</span>
    </div>
  );
}

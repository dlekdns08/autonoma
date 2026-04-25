"use client";

/**
 * Phase 3-#3 — cutscene composer scaffold.
 *
 *   /cutscenes
 *
 * Lists the user's saved cutscenes, lets them pick one to edit (or
 * create a new one), and exposes a minimal step editor: kind, ``at_ms``
 * offset, label, and a free-form JSON payload textarea. The full
 * timeline-with-tracks UI is a follow-up — this page is structured so
 * the editor surface can be replaced without changing the persistence
 * or playback code.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/hooks/useAuth";
import {
  useCutscenes,
  type Cutscene,
  type CutsceneStep,
  type CutsceneStepKind,
} from "@/hooks/useCutscenes";

const STEP_KINDS: CutsceneStepKind[] = ["clip", "speech", "sfx", "delay"];

const PAYLOAD_PLACEHOLDER: Record<CutsceneStepKind, string> = {
  clip: '{"clip_id": "...", "vrm_file": "alice.vrm"}',
  speech: '{"agent": "Director", "text": "We did it!"}',
  sfx: '{"sfx_name": "complete"}',
  delay: '{"duration_ms": 1500}',
};

function blankStep(at_ms: number): CutsceneStep {
  return { at_ms, kind: "sfx", label: "", payload: { sfx_name: "blip" } };
}

function ensureCutscene(c: Partial<Cutscene>, ownerHint: string): Cutscene {
  return {
    id: c.id ?? "draft",
    owner_user_id: c.owner_user_id ?? ownerHint,
    name: c.name ?? "새 컷씬",
    description: c.description ?? "",
    steps: c.steps ?? [],
    trigger: c.trigger ?? { kind: "manual", value: "" },
    created_at: c.created_at ?? "",
    updated_at: c.updated_at ?? "",
  };
}

export default function CutsceneComposerPage() {
  const { user, loading: authLoading } = useAuth();
  const { cutscenes, loading, error, create, update, remove, play, refresh } =
    useCutscenes();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Cutscene | null>(null);
  const [saving, setSaving] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const selected = useMemo(
    () => cutscenes.find((c) => c.id === selectedId) ?? null,
    [cutscenes, selectedId],
  );

  // Bring up draft state whenever the selection changes.
  useEffect(() => {
    if (!selected) {
      setDraft(null);
      return;
    }
    setDraft({ ...selected, steps: selected.steps.map((s) => ({ ...s })) });
  }, [selected]);

  const onNew = useCallback(async () => {
    if (!user) return;
    const created = await create({
      name: "새 컷씬",
      description: "",
      steps: [
        { at_ms: 0, kind: "sfx", label: "intro", payload: { sfx_name: "spawn" } },
      ],
      trigger: { kind: "manual", value: "" },
    });
    if (created) setSelectedId(created.id);
  }, [create, user]);

  const onSave = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    setStatusMsg(null);
    try {
      const saved = await update(draft.id, draft);
      if (saved) {
        setStatusMsg("저장되었습니다.");
        setDraft({ ...saved, steps: saved.steps.map((s) => ({ ...s })) });
      } else {
        setStatusMsg("저장 실패.");
      }
    } finally {
      setSaving(false);
    }
  }, [draft, update]);

  const onDelete = useCallback(async () => {
    if (!draft) return;
    if (!window.confirm(`"${draft.name}" 컷씬을 삭제할까요?`)) return;
    if (await remove(draft.id)) {
      setSelectedId(null);
      setStatusMsg("삭제되었습니다.");
    }
  }, [draft, remove]);

  const onPlay = useCallback(async () => {
    if (!draft) return;
    setStatusMsg(null);
    if (await play(draft.id)) {
      setStatusMsg("재생을 시작했습니다 — 버스 이벤트를 확인하세요.");
    }
  }, [draft, play]);

  const updateStep = useCallback(
    (index: number, patch: Partial<CutsceneStep>) => {
      setDraft((prev) => {
        if (!prev) return prev;
        const steps = prev.steps.map((s, i) =>
          i === index ? { ...s, ...patch } : s,
        );
        return { ...prev, steps };
      });
    },
    [],
  );

  const addStep = useCallback(() => {
    setDraft((prev) => {
      if (!prev) return prev;
      const last = prev.steps[prev.steps.length - 1];
      const at_ms = last ? last.at_ms + 500 : 0;
      return { ...prev, steps: [...prev.steps, blankStep(at_ms)] };
    });
  }, []);

  const removeStep = useCallback((index: number) => {
    setDraft((prev) => {
      if (!prev) return prev;
      return { ...prev, steps: prev.steps.filter((_, i) => i !== index) };
    });
  }, []);

  const setPayloadJson = useCallback(
    (index: number, raw: string) => {
      try {
        const parsed = raw.trim() ? JSON.parse(raw) : {};
        if (typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("payload must be a JSON object");
        }
        updateStep(index, { payload: parsed as Record<string, unknown> });
      } catch (err) {
        setStatusMsg(
          `payload JSON 오류: ${
            err instanceof Error ? err.message : String(err)
          }`,
        );
      }
    },
    [updateStep],
  );

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/40">
        loading…
      </div>
    );
  }
  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/60">
        로그인이 필요합니다.
      </div>
    );
  }

  // Best-effort owner hint for ``ensureCutscene`` defaults.
  const ownerHint = (user as unknown as { id?: string; uuid?: string }).id
    ?? (user as unknown as { id?: string; uuid?: string }).uuid
    ?? "me";

  return (
    <div className="min-h-screen bg-[#0a0a12] p-4 text-white">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <Link
            href="/"
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/60 hover:bg-white/10"
          >
            ← 대시보드
          </Link>
          <h1 className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-lg font-bold text-transparent">
            🎬 컷씬 컴포저
          </h1>
          <button
            type="button"
            onClick={onNew}
            className="rounded-xl border border-fuchsia-400/40 bg-fuchsia-500/15 px-4 py-2 font-mono text-xs text-fuchsia-100 hover:bg-fuchsia-500/30"
          >
            + 새 컷씬
          </button>
        </header>

        {error ? (
          <div className="rounded-xl border border-rose-500/40 bg-rose-950/40 p-3 font-mono text-xs text-rose-200">
            {error}
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_1fr]">
          {/* ── Catalog ────────────────────────────────────────── */}
          <aside className="rounded-2xl border border-white/10 bg-slate-950/60 p-3">
            <div className="flex items-center justify-between">
              <h2 className="font-mono text-xs font-semibold uppercase tracking-wider text-white/60">
                내 컷씬 ({cutscenes.length})
              </h2>
              <button
                type="button"
                onClick={() => void refresh()}
                className="font-mono text-[10px] text-white/40 hover:text-white/70"
              >
                ↻
              </button>
            </div>
            <ul className="mt-2 flex flex-col gap-1.5">
              {loading ? (
                <li className="font-mono text-xs text-white/40">불러오는 중…</li>
              ) : cutscenes.length === 0 ? (
                <li className="font-mono text-xs text-white/40">
                  아직 컷씬이 없습니다.
                </li>
              ) : (
                cutscenes.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(c.id)}
                      className={`w-full rounded border px-2 py-1.5 text-left font-mono text-xs transition ${
                        c.id === selectedId
                          ? "border-fuchsia-400/60 bg-fuchsia-500/15 text-fuchsia-100"
                          : "border-white/10 bg-slate-900/40 text-white/75 hover:bg-white/10"
                      }`}
                    >
                      <div className="truncate">{c.name}</div>
                      <div className="font-mono text-[10px] text-white/40">
                        {c.steps.length} step · {c.trigger.kind}
                      </div>
                    </button>
                  </li>
                ))
              )}
            </ul>
          </aside>

          {/* ── Editor ─────────────────────────────────────────── */}
          <section className="rounded-2xl border border-white/10 bg-slate-950/60 p-4">
            {!draft ? (
              <p className="font-mono text-sm text-white/40">
                왼쪽에서 컷씬을 선택하거나 새로 만드세요.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <label className="flex flex-col gap-1 font-mono text-[11px] text-white/50">
                    이름
                    <input
                      value={draft.name}
                      onChange={(e) =>
                        setDraft((p) => p && { ...p, name: e.target.value })
                      }
                      className="rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-sm text-white"
                    />
                  </label>
                  <label className="flex flex-col gap-1 font-mono text-[11px] text-white/50">
                    트리거
                    <select
                      value={draft.trigger.kind}
                      onChange={(e) =>
                        setDraft(
                          (p) =>
                            p && {
                              ...p,
                              trigger: {
                                kind: e.target.value as Cutscene["trigger"]["kind"],
                                value: p.trigger.value,
                              },
                            },
                        )
                      }
                      className="rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-sm text-white"
                    >
                      <option value="manual">manual</option>
                      <option value="project_complete">project_complete</option>
                      <option value="achievement">achievement</option>
                      <option value="boss_defeated">boss_defeated</option>
                    </select>
                  </label>
                </div>

                {draft.trigger.kind === "achievement" ? (
                  <label className="flex flex-col gap-1 font-mono text-[11px] text-white/50">
                    achievement_id (빈 값 = 모든 achievement)
                    <input
                      value={draft.trigger.value}
                      onChange={(e) =>
                        setDraft(
                          (p) =>
                            p && {
                              ...p,
                              trigger: { ...p.trigger, value: e.target.value },
                            },
                        )
                      }
                      className="rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-sm text-white"
                    />
                  </label>
                ) : null}

                <label className="flex flex-col gap-1 font-mono text-[11px] text-white/50">
                  설명
                  <textarea
                    value={draft.description}
                    onChange={(e) =>
                      setDraft(
                        (p) => p && { ...p, description: e.target.value },
                      )
                    }
                    rows={2}
                    className="rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-sm text-white"
                  />
                </label>

                <div className="flex items-center justify-between">
                  <h3 className="font-mono text-xs font-semibold uppercase tracking-wider text-white/60">
                    스텝 ({draft.steps.length})
                  </h3>
                  <button
                    type="button"
                    onClick={addStep}
                    className="rounded border border-white/10 bg-white/5 px-2 py-1 font-mono text-[11px] text-white/70 hover:bg-white/10"
                  >
                    + 스텝
                  </button>
                </div>

                <ul className="flex flex-col gap-2">
                  {draft.steps.map((step, idx) => (
                    <li
                      key={idx}
                      className="rounded border border-white/10 bg-slate-900/40 p-2"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <input
                          type="number"
                          min={0}
                          step={50}
                          value={step.at_ms}
                          onChange={(e) =>
                            updateStep(idx, {
                              at_ms: Math.max(0, Number(e.target.value)),
                            })
                          }
                          className="w-24 rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-xs text-white"
                          title="ms offset"
                        />
                        <select
                          value={step.kind}
                          onChange={(e) =>
                            updateStep(idx, {
                              kind: e.target.value as CutsceneStepKind,
                            })
                          }
                          className="rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-xs text-white"
                        >
                          {STEP_KINDS.map((k) => (
                            <option key={k} value={k}>
                              {k}
                            </option>
                          ))}
                        </select>
                        <input
                          value={step.label}
                          onChange={(e) =>
                            updateStep(idx, { label: e.target.value })
                          }
                          placeholder="라벨"
                          className="flex-1 rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-xs text-white"
                        />
                        <button
                          type="button"
                          onClick={() => removeStep(idx)}
                          className="rounded border border-rose-400/30 bg-rose-500/10 px-2 py-1 font-mono text-[11px] text-rose-200 hover:bg-rose-500/20"
                        >
                          ✕
                        </button>
                      </div>
                      <textarea
                        defaultValue={JSON.stringify(step.payload, null, 2)}
                        onBlur={(e) => setPayloadJson(idx, e.target.value)}
                        rows={3}
                        placeholder={PAYLOAD_PLACEHOLDER[step.kind]}
                        spellCheck={false}
                        className="mt-2 w-full rounded border border-white/10 bg-slate-900/60 px-2 py-1 font-mono text-[11px] text-white"
                      />
                    </li>
                  ))}
                </ul>

                <div className="flex flex-wrap items-center justify-end gap-2 border-t border-white/10 pt-3">
                  {statusMsg ? (
                    <span className="mr-auto font-mono text-[10px] text-white/50">
                      {statusMsg}
                    </span>
                  ) : null}
                  <button
                    type="button"
                    onClick={onPlay}
                    className="rounded border border-cyan-400/40 bg-cyan-500/10 px-3 py-1 font-mono text-xs text-cyan-100 hover:bg-cyan-500/20"
                  >
                    ▶ 재생
                  </button>
                  <button
                    type="button"
                    onClick={onDelete}
                    className="rounded border border-rose-400/30 bg-rose-500/10 px-3 py-1 font-mono text-xs text-rose-200 hover:bg-rose-500/20"
                  >
                    삭제
                  </button>
                  <button
                    type="button"
                    onClick={onSave}
                    disabled={saving}
                    className="rounded border border-fuchsia-400/40 bg-fuchsia-500/15 px-3 py-1 font-mono text-xs text-fuchsia-100 hover:bg-fuchsia-500/30 disabled:opacity-50"
                  >
                    {saving ? "저장 중…" : "저장"}
                  </button>
                </div>
                {/* tiny owner hint stamp so the page doesn't drop unused */}
                <span className="hidden">{ownerHint}</span>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

"use client";

/**
 * Binding matrix for one VRM model:
 *
 *   rows = triggers (mood / emote / state)
 *   col  = which clip plays for that trigger, if any
 *
 * The user picks a clip from a dropdown per row; saving bumps the
 * global binding table so every viewer on the dashboard starts playing
 * the new clip within one ``mocap.bindings.updated`` event.
 *
 * Below the main table an admin-only "수동 트리거" panel surfaces
 * ``manual`` bindings — free-form slugs admins can create and fire on
 * demand via ``POST /api/mocap-triggers/fire``. Non-admins never see
 * this section (they can't create or fire).
 */

import { useMemo, useState } from "react";
import type { ClipSummary } from "@/lib/mocap/clipFormat";
import type { UseMocapBindings } from "@/hooks/mocap/useMocapBindings";
import { API_BASE_URL } from "@/hooks/useSwarm";
import {
  EMOTE_LABELS,
  EMOTE_TRIGGERS,
  MANUAL_SLUG_RE,
  MOOD_LABELS,
  MOOD_TRIGGERS,
  STATE_LABELS,
  STATE_TRIGGERS,
  type EmoteTrigger,
  type MoodTrigger,
  type StateTrigger,
  type TriggerKind,
} from "@/lib/mocap/triggers";

interface Props {
  vrmFile: string;
  clips: ClipSummary[];
  bindings: UseMocapBindings;
  isAdmin: boolean;
}

type Row = {
  kind: TriggerKind;
  value: string;
  label: string;
};

function buildRows(): Row[] {
  const rows: Row[] = [];
  for (const v of MOOD_TRIGGERS) {
    rows.push({ kind: "mood", value: v, label: MOOD_LABELS[v as MoodTrigger] });
  }
  for (const v of STATE_TRIGGERS) {
    rows.push({ kind: "state", value: v, label: STATE_LABELS[v as StateTrigger] });
  }
  for (const v of EMOTE_TRIGGERS) {
    rows.push({
      kind: "emote",
      value: v,
      label: `${v} — ${EMOTE_LABELS[v as EmoteTrigger]}`,
    });
  }
  return rows;
}

export default function BindingEditor({ vrmFile, clips, bindings, isAdmin }: Props) {
  const rows = buildRows();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Manual-panel form state. Only rendered for admins, but keep the
  // hooks unconditional so the hook call order stays stable across
  // ``isAdmin`` flips (non-admins still hold this state; they just
  // never see or mutate it).
  const [manualSlug, setManualSlug] = useState("");
  const [manualClipId, setManualClipId] = useState("");
  const [manualBusy, setManualBusy] = useState(false);

  // Index clips by id for the existing-manual-bindings list so we can
  // show a human name instead of the raw UUID.
  const clipNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of clips) m.set(c.id, c.name);
    return m;
  }, [clips]);

  const manualBindings = useMemo(
    () =>
      bindings.bindings.filter(
        (b) => b.vrm_file === vrmFile && b.trigger_kind === "manual",
      ),
    [bindings.bindings, vrmFile],
  );

  const onPick = async (row: Row, clipId: string) => {
    setError(null);
    setBusyKey(`${row.kind}|${row.value}`);
    try {
      if (clipId === "") {
        await bindings.remove({ vrmFile, kind: row.kind, value: row.value });
      } else {
        const res = await bindings.upsert(
          { vrmFile, kind: row.kind, value: row.value },
          clipId,
        );
        if (!res.ok) setError(`저장 실패: ${res.reason}`);
      }
    } finally {
      setBusyKey(null);
    }
  };

  const onAddManual = async () => {
    setError(null);
    const slug = manualSlug.trim();
    if (!slug) {
      setError("슬러그를 입력해 주세요");
      return;
    }
    if (!MANUAL_SLUG_RE.test(slug)) {
      setError("슬러그 형식이 올바르지 않습니다 (소문자, 숫자, - _ 만 허용)");
      return;
    }
    if (!manualClipId) {
      setError("클립을 선택해 주세요");
      return;
    }
    setManualBusy(true);
    try {
      const res = await bindings.upsert(
        { vrmFile, kind: "manual", value: slug },
        manualClipId,
      );
      if (!res.ok) {
        setError(`저장 실패: ${res.reason}`);
        return;
      }
      setManualSlug("");
      setManualClipId("");
    } finally {
      setManualBusy(false);
    }
  };

  const onRemoveManual = async (slug: string) => {
    setError(null);
    const ok = await bindings.remove({ vrmFile, kind: "manual", value: slug });
    if (!ok) setError("삭제 실패");
  };

  const onFire = async (slug: string) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/mocap-triggers/fire`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vrm_file: vrmFile, value: slug }),
      });
      if (!res.ok) {
        const body = (await res.json().catch((err) => {
          console.warn("Failed to parse mocap-trigger error response", err);
          return {};
        })) as {
          detail?: string;
        };
        setError(`발사 실패: ${body?.detail ?? res.status}`);
      }
    } catch {
      setError("발사 실패: 네트워크");
    }
  };

  return (
    <div className="flex flex-col gap-2">
      {error && (
        <div className="rounded border border-rose-500/30 bg-rose-950/40 px-2 py-1 text-[11px] font-mono text-rose-200">
          {error}
        </div>
      )}
      {!isAdmin && (
        <div className="rounded border border-rose-500/30 bg-slate-950/60 px-2 py-1 text-[11px] font-mono text-rose-200">
          바인딩 수정은 admin만 가능합니다
        </div>
      )}
      <div className="overflow-hidden rounded-lg border border-white/10">
        <table className="w-full text-[11px] font-mono">
          <thead>
            <tr className="bg-slate-900/70 text-white/50">
              <th className="px-3 py-1 text-left font-medium">종류</th>
              <th className="px-3 py-1 text-left font-medium">트리거</th>
              <th className="px-3 py-1 text-left font-medium">클립</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const match = bindings.lookup({
                vrmFile,
                kind: row.kind,
                value: row.value,
              });
              const key = `${row.kind}|${row.value}`;
              const busy = busyKey === key;
              return (
                <tr
                  key={key}
                  className="border-t border-white/5 odd:bg-slate-950/30"
                >
                  <td className="px-3 py-1 uppercase text-white/40">{row.kind}</td>
                  <td className="px-3 py-1 text-white/80">{row.label}</td>
                  <td className="px-3 py-1">
                    <select
                      disabled={busy || !isAdmin}
                      value={match?.clip_id ?? ""}
                      onChange={(e) => onPick(row, e.target.value)}
                      className="w-full rounded border border-white/10 bg-slate-950/80 px-2 py-1 text-white outline-none focus:border-fuchsia-500/60"
                    >
                      <option value="">— 없음 —</option>
                      {clips.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                          {c.source_vrm !== vrmFile ? ` (${c.source_vrm})` : ""}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-white/35">
        바인딩은 전역입니다 — 저장 즉시 대시보드의 모든 뷰어가 해당 트리거에서 이 클립을 재생합니다.
      </p>
      <p className="text-[10px] text-white/35">
        재생 우선순위: 상태 &gt; 이모트 &gt; 무드 (여러 트리거가 동시에 맞을 때 상위 항목이 이깁니다).
      </p>
      {isAdmin && (
        <div className="mt-3 flex flex-col gap-2 rounded-lg border border-fuchsia-500/20 bg-slate-950/40 p-3">
          <div className="flex items-center gap-2 text-[11px] font-mono">
            <span className="text-fuchsia-300/80 uppercase tracking-wider">
              수동 트리거
            </span>
            <span className="text-white/30">— admin only</span>
          </div>
          <div className="flex flex-col gap-1.5 sm:flex-row">
            <input
              value={manualSlug}
              onChange={(e) => setManualSlug(e.target.value)}
              placeholder="소문자, 숫자, `-`, `_` — 최대 32자"
              maxLength={32}
              disabled={manualBusy}
              className="flex-1 rounded border border-white/10 bg-slate-950/80 px-2 py-1 font-mono text-[11px] text-white outline-none focus:border-fuchsia-500/60"
            />
            <select
              value={manualClipId}
              onChange={(e) => setManualClipId(e.target.value)}
              disabled={manualBusy}
              className="rounded border border-white/10 bg-slate-950/80 px-2 py-1 font-mono text-[11px] text-white outline-none focus:border-fuchsia-500/60"
            >
              <option value="">— 클립 선택 —</option>
              {clips.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.source_vrm !== vrmFile ? ` (${c.source_vrm})` : ""}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onAddManual}
              disabled={manualBusy || !manualSlug || !manualClipId}
              className="rounded border border-fuchsia-500/40 bg-fuchsia-600/20 px-3 py-1 font-mono text-[11px] text-fuchsia-100 transition-colors hover:bg-fuchsia-600/30 disabled:cursor-not-allowed disabled:opacity-40"
            >
              추가
            </button>
          </div>
          {manualBindings.length === 0 ? (
            <p className="text-[10px] text-white/30">
              등록된 수동 트리거가 없습니다.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {manualBindings.map((b) => {
                const clipName = clipNameById.get(b.clip_id) ?? b.clip_id;
                return (
                  <li
                    key={b.trigger_value}
                    className="flex items-center gap-2 rounded border border-white/5 bg-slate-950/60 px-2 py-1 font-mono text-[11px]"
                  >
                    <span className="text-fuchsia-200/90">
                      {b.trigger_value}
                    </span>
                    <span className="text-white/30">→</span>
                    <span className="truncate text-white/80">{clipName}</span>
                    <div className="ml-auto flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => void onFire(b.trigger_value)}
                        className="rounded border border-fuchsia-500/40 bg-fuchsia-600/20 px-2 py-0.5 text-[10px] text-fuchsia-100 transition-colors hover:bg-fuchsia-600/30"
                        title="지금 재생"
                      >
                        ▶ 발사
                      </button>
                      <button
                        type="button"
                        onClick={() => void onRemoveManual(b.trigger_value)}
                        className="rounded border border-rose-500/30 bg-rose-950/40 px-2 py-0.5 text-[10px] text-rose-200 transition-colors hover:bg-rose-900/50"
                      >
                        삭제
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="text-[10px] text-white/30">
            발사 시 해당 VRM을 쓰는 모든 에이전트가 짧게 이 클립을 재생한 뒤 원래 동작으로 돌아갑니다.
          </p>
        </div>
      )}
    </div>
  );
}

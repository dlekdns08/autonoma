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
 * We deliberately only surface ``mood``, ``emote``, ``state`` here —
 * ``manual`` triggers are admin-only and go through a different panel.
 */

import { useState } from "react";
import type { ClipSummary } from "@/lib/mocap/clipFormat";
import type { UseMocapBindings } from "@/hooks/mocap/useMocapBindings";
import {
  EMOTE_LABELS,
  EMOTE_TRIGGERS,
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
    </div>
  );
}

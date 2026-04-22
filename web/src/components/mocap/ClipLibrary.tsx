"use client";

/**
 * List of captured clips with rename + delete + "preview" actions. The
 * parent passes the selected vrm filter so the library only shows
 * clips recorded on that model (clips are cross-compatible at playback
 * time, but the binding editor filters by rig to keep the list sane).
 */

import { useState } from "react";
import type { ClipSummary } from "@/lib/mocap/clipFormat";

interface Props {
  clips: ClipSummary[];
  loading: boolean;
  selectedClipId: string | null;
  sourceVrmFilter?: string;
  currentUserId: string;
  isAdmin: boolean;
  onSelect: (clipId: string) => void;
  onRename: (clipId: string, name: string) => Promise<boolean>;
  onDelete: (clipId: string) => Promise<boolean>;
}

export default function ClipLibrary({
  clips,
  loading,
  selectedClipId,
  sourceVrmFilter,
  currentUserId,
  isAdmin,
  onSelect,
  onRename,
  onDelete,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const visible = sourceVrmFilter
    ? clips.filter((c) => c.source_vrm === sourceVrmFilter)
    : clips;

  if (loading) {
    return (
      <div className="rounded-lg border border-white/10 bg-slate-900/40 px-3 py-4 text-center text-[11px] font-mono text-white/40">
        불러오는 중…
      </div>
    );
  }
  if (visible.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-[11px] font-mono text-white/40">
        {sourceVrmFilter
          ? `${sourceVrmFilter}로 녹화된 클립이 없습니다.`
          : "클립이 아직 없습니다."}
      </div>
    );
  }

  return (
    <ul className="flex flex-col gap-1">
      {visible.map((clip) => {
        const isSel = clip.id === selectedClipId;
        const isEditing = editingId === clip.id;
        const canEdit = clip.owner_user_id === currentUserId || isAdmin;
        return (
          <li
            key={clip.id}
            className={
              "flex items-center gap-2 rounded border px-2 py-1.5 text-[11px] font-mono " +
              (isSel
                ? "border-fuchsia-500/70 bg-fuchsia-500/10"
                : "border-white/10 bg-slate-900/40 hover:border-white/25")
            }
          >
            {isEditing ? (
              <input
                autoFocus
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onBlur={async () => {
                  const name = draftName.trim();
                  if (name && name !== clip.name) {
                    setBusyId(clip.id);
                    await onRename(clip.id, name);
                    setBusyId(null);
                  }
                  setEditingId(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                  if (e.key === "Escape") setEditingId(null);
                }}
                className="flex-1 rounded border border-fuchsia-500/50 bg-slate-950/80 px-1.5 py-0.5 text-white outline-none"
              />
            ) : (
              <button
                type="button"
                onClick={() => onSelect(clip.id)}
                className="flex-1 truncate text-left text-white"
              >
                {clip.name}
              </button>
            )}
            <span className="text-white/40">
              {clip.duration_s.toFixed(1)}s · {clip.source_vrm}
            </span>
            {canEdit && (
              <button
                type="button"
                onClick={() => {
                  setEditingId(clip.id);
                  setDraftName(clip.name);
                }}
                disabled={busyId === clip.id}
                className="rounded border border-white/10 px-1.5 py-0.5 text-white/50 hover:border-white/25 hover:text-white"
              >
                이름
              </button>
            )}
            {canEdit && (
              <button
                type="button"
                onClick={async () => {
                  if (!confirm(`"${clip.name}" 클립을 삭제할까요?`)) return;
                  setBusyId(clip.id);
                  const ok = await onDelete(clip.id);
                  setBusyId(null);
                  if (!ok) alert("삭제 실패 — 바인딩에서 사용 중일 수 있습니다.");
                }}
                disabled={busyId === clip.id}
                className="rounded border border-rose-500/30 px-1.5 py-0.5 text-rose-300 hover:border-rose-500/60"
              >
                삭제
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

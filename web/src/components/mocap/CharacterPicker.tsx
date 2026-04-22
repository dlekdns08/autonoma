"use client";

/**
 * Grid of VRM characters. Clicking a tile selects that character as the
 * recording target; the picker also shows which characters currently
 * have bindings so the operator can see at a glance which need work.
 */

import { VRM_FILES, VRM_CREDITS } from "@/components/vtuber/vrmCredits";
import type { BindingRow } from "@/lib/mocap/clipFormat";

interface Props {
  selected: string | null;
  onSelect: (vrmFile: string) => void;
  bindings: BindingRow[];
}

export default function CharacterPicker({ selected, onSelect, bindings }: Props) {
  const bindingCounts = new Map<string, number>();
  for (const b of bindings) {
    bindingCounts.set(b.vrm_file, (bindingCounts.get(b.vrm_file) ?? 0) + 1);
  }
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
      {VRM_FILES.map((file) => {
        const credit = VRM_CREDITS[file];
        const count = bindingCounts.get(file) ?? 0;
        const isSel = selected === file;
        return (
          <button
            key={file}
            type="button"
            onClick={() => onSelect(file)}
            className={
              "flex flex-col gap-1 rounded-lg border px-3 py-2 text-left transition-colors " +
              (isSel
                ? "border-fuchsia-500/70 bg-fuchsia-500/10"
                : "border-white/10 bg-slate-900/40 hover:border-white/25 hover:bg-slate-900/60")
            }
          >
            <span className="text-[13px] font-semibold text-white">
              {credit?.character ?? file}
            </span>
            <span className="truncate text-[10px] font-mono text-white/40">
              {file}
            </span>
            <span className="text-[10px] text-white/45">
              {count} 바인딩
            </span>
          </button>
        );
      })}
    </div>
  );
}

"use client";

import { SHORTCUTS } from "@/hooks/useKeyNav";
import { useModalA11y } from "@/hooks/useModalA11y";

interface Props {
  onClose: () => void;
}

export default function KeyboardHelpModal({ onClose }: Props) {
  const dialogRef = useModalA11y<HTMLDivElement>({ onEscape: onClose });
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="kbd-help-title"
        className="w-full max-w-sm rounded-2xl border border-white/10 bg-slate-950/95 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        style={{ boxShadow: "0 0 40px rgba(139,92,246,0.15)" }}
      >
        <div className="flex items-center justify-between mb-4">
          <h2
            id="kbd-help-title"
            className="font-mono text-sm font-bold text-violet-300 tracking-widest uppercase"
          >
            ⌨ Keyboard Shortcuts
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close keyboard shortcuts"
            className="text-white/40 hover:text-white/70 font-mono text-sm"
          >
            ✕
          </button>
        </div>

        <div className="flex flex-col gap-2">
          {SHORTCUTS.map(({ key, description }) => (
            <div key={key} className="flex items-center justify-between gap-4">
              <span className="font-mono text-xs text-white/60">{description}</span>
              <kbd
                className="shrink-0 rounded border border-white/20 bg-white/8 px-2 py-0.5 font-mono text-[11px] text-white/80"
              >
                {key}
              </kbd>
            </div>
          ))}
        </div>

        <p className="mt-4 text-center font-mono text-[10px] text-white/25">
          Press ? or Esc to close
        </p>
      </div>
    </div>
  );
}

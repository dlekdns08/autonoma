"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

interface TemplateInfo {
  id: string;
  name: string;
  description: string;
  file_count: number;
}

interface Props {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  disabled?: boolean;
}

export default function TemplateSelector({ selectedId, onSelect, disabled }: Props) {
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(`${API_BASE_URL}/api/templates`)
      .then((r) => r.json())
      .then((data: { templates: TemplateInfo[] }) => {
        if (!cancelled) {
          setTemplates(data.templates ?? []);
        }
      })
      .catch(() => {
        // silently ignore — templates are optional
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = templates.find((t) => t.id === selectedId) ?? null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled || loading}
        className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-mono transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
          selectedId
            ? "border-cyan-500/60 bg-cyan-500/10 text-cyan-200 hover:bg-cyan-500/20"
            : "border-white/20 bg-slate-900/80 text-white/60 hover:border-white/40"
        }`}
      >
        <span>{loading ? "..." : selected ? `Template: ${selected.name}` : "Choose template"}</span>
        <span className="text-white/40">{open ? "▲" : "▼"}</span>
      </button>

      {open && !loading && (
        <div className="absolute left-0 top-full z-50 mt-1 w-72 rounded-xl border border-white/10 bg-slate-900 shadow-xl">
          <button
            type="button"
            onClick={() => { onSelect(null); setOpen(false); }}
            className="w-full px-3 py-2 text-left text-xs font-mono text-white/40 hover:bg-white/5 rounded-t-xl transition-colors"
          >
            No template (blank project)
          </button>
          <div className="h-px bg-white/10" />
          {templates.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => { onSelect(t.id); setOpen(false); }}
              className={`w-full px-3 py-2 text-left transition-colors hover:bg-white/5 last:rounded-b-xl ${
                t.id === selectedId ? "bg-cyan-500/10" : ""
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono font-semibold text-cyan-200">{t.name}</span>
                <span className="text-[10px] font-mono text-white/40">{t.file_count} files</span>
              </div>
              <p className="mt-0.5 text-[10px] text-white/50 leading-snug">{t.description}</p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

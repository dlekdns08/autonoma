"use client";

import type { ReactNode } from "react";

type Tone = "info" | "error" | "warn" | "ok";

interface StatusBoxProps {
  tone?: Tone;
  title?: string;
  children: ReactNode;
  /** Optional dismiss / retry button. */
  action?: ReactNode;
}

const TONE_CLASSES: Record<Tone, string> = {
  info: "border-slate-700 bg-slate-900/60 text-slate-200",
  error: "border-red-700 bg-red-900/30 text-red-200",
  warn: "border-amber-700 bg-amber-900/30 text-amber-200",
  ok: "border-emerald-700 bg-emerald-900/30 text-emerald-200",
};

/** Shared inline status panel used by /mocap, /voice and similar admin
 *  pages. Keeps tone/title/body styling consistent across the app so
 *  error banners don't drift per feature. */
export function StatusBox({ tone = "info", title, children, action }: StatusBoxProps) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`rounded border px-3 py-2 text-sm ${TONE_CLASSES[tone]}`}
    >
      {title ? <div className="mb-1 font-semibold">{title}</div> : null}
      <div>{children}</div>
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";

export interface ToastItem {
  id: number;
  type: "level_up" | "boss" | "quest" | "achievement" | "ghost" | "fortune" | "guild" | "info";
  title: string;
  message: string;
  icon: string;
  timestamp: number;
}

const TOAST_STYLES: Record<ToastItem["type"], { border: string; bg: string; glow: string; titleColor: string }> = {
  level_up: {
    border: "border-yellow-500/60",
    bg: "from-yellow-950/90 to-amber-950/90",
    glow: "shadow-yellow-500/30",
    titleColor: "text-yellow-300",
  },
  boss: {
    border: "border-red-500/60",
    bg: "from-red-950/90 to-rose-950/90",
    glow: "shadow-red-500/30",
    titleColor: "text-red-300",
  },
  quest: {
    border: "border-emerald-500/60",
    bg: "from-emerald-950/90 to-green-950/90",
    glow: "shadow-emerald-500/30",
    titleColor: "text-emerald-300",
  },
  achievement: {
    border: "border-fuchsia-500/60",
    bg: "from-fuchsia-950/90 to-purple-950/90",
    glow: "shadow-fuchsia-500/30",
    titleColor: "text-fuchsia-300",
  },
  ghost: {
    border: "border-white/30",
    bg: "from-slate-900/90 to-slate-950/90",
    glow: "shadow-white/10",
    titleColor: "text-white/70",
  },
  fortune: {
    border: "border-amber-500/60",
    bg: "from-amber-950/90 to-orange-950/90",
    glow: "shadow-amber-500/30",
    titleColor: "text-amber-300",
  },
  guild: {
    border: "border-cyan-500/60",
    bg: "from-cyan-950/90 to-teal-950/90",
    glow: "shadow-cyan-500/30",
    titleColor: "text-cyan-300",
  },
  info: {
    border: "border-blue-500/40",
    bg: "from-blue-950/90 to-slate-950/90",
    glow: "shadow-blue-500/20",
    titleColor: "text-blue-300",
  },
};

let toastIdCounter = 0;
export function createToastId(): number {
  return toastIdCounter++;
}

export default function ToastContainer({ toasts, onDismiss }: { toasts: ToastItem[]; onDismiss: (id: number) => void }) {
  return (
    <div className="fixed top-20 right-4 z-50 flex flex-col gap-2 pointer-events-none max-w-sm">
      {toasts.slice(-5).map((toast) => (
        <ToastCard key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastCard({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: number) => void }) {
  const [visible, setVisible] = useState(false);
  const [exiting, setExiting] = useState(false);
  const style = TOAST_STYLES[toast.type];

  useEffect(() => {
    // Enter animation
    requestAnimationFrame(() => setVisible(true));

    // Auto-dismiss after 4s
    const t = setTimeout(() => {
      setExiting(true);
      setTimeout(() => onDismiss(toast.id), 300);
    }, 4000);

    return () => clearTimeout(t);
  }, [toast.id, onDismiss]);

  return (
    <div
      className={`pointer-events-auto rounded-xl border ${style.border} bg-gradient-to-r ${style.bg} px-4 py-3 backdrop-blur-md shadow-lg ${style.glow} transition-all duration-300 cursor-pointer ${
        visible && !exiting ? "translate-x-0 opacity-100" : "translate-x-8 opacity-0"
      }`}
      onClick={() => {
        setExiting(true);
        setTimeout(() => onDismiss(toast.id), 300);
      }}
    >
      <div className="flex items-start gap-3">
        <span className="text-2xl shrink-0 mt-0.5">{toast.icon}</span>
        <div className="min-w-0">
          <div className={`text-sm font-bold font-mono ${style.titleColor}`}>{toast.title}</div>
          <div className="text-xs text-white/60 mt-0.5 line-clamp-2">{toast.message}</div>
        </div>
      </div>

      {/* Progress bar auto-dismiss indicator */}
      <div className="mt-2 h-0.5 w-full rounded-full bg-white/10 overflow-hidden">
        <div
          className={`h-full rounded-full bg-white/30 transition-all ease-linear`}
          style={{
            width: visible && !exiting ? "0%" : "100%",
            transitionDuration: visible && !exiting ? "4000ms" : "0ms",
          }}
        />
      </div>
    </div>
  );
}

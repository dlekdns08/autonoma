"use client";

import { useTheme } from "@/hooks/useTheme";

export default function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();

  return (
    <button
      type="button"
      onClick={toggleTheme}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      className="fixed top-2 right-2 z-50 flex h-7 w-7 items-center justify-center rounded-full border border-white/10 bg-slate-950/80 text-sm backdrop-blur-sm transition-colors hover:border-violet-500/40 hover:bg-slate-900/90"
      style={{ boxShadow: "0 0 8px rgba(139,92,246,0.08)" }}
    >
      {theme === "dark" ? "☀️" : "🌙"}
    </button>
  );
}

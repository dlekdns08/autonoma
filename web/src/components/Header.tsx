"use client";

import Link from "next/link";

interface Props {
  projectName: string;
  round: number;
  maxRounds: number;
  sky: string;
  connected: boolean;
  /** Caller passes ``user?.role === "admin"`` so we can conditionally
   *  surface admin-only links without each header consumer having to
   *  inline the check. Optional so non-auth pages (the OBS feed, the
   *  share landing) can mount the header without an auth context. */
  isAdmin?: boolean;
}

const SKY_COLOR: Record<string, string> = {
  dawn: "bg-amber-400",
  morning: "bg-sky-400",
  afternoon: "bg-blue-400",
  evening: "bg-orange-400",
  dusk: "bg-rose-400",
  night: "bg-indigo-500",
};

function skyDotClass(sky: string): string {
  const key = sky.toLowerCase().split(" ")[0];
  return SKY_COLOR[key] ?? "bg-violet-500";
}

export default function Header({
  projectName,
  round,
  maxRounds,
  sky,
  connected,
  isAdmin = false,
}: Props) {
  const progressPct = maxRounds > 0 ? Math.min(100, (round / maxRounds) * 100) : 0;

  return (
    <header className="border-b border-[rgba(255,255,255,0.06)] bg-transparent px-4 py-2">
      <div className="flex items-center justify-between gap-4">

        {/* Left — logo mark + nav */}
        <div className="flex items-center gap-3 min-w-0">
          <Link href="/" className="flex items-center gap-2 hover:opacity-80">
            <span className="font-mono text-violet-400 text-base leading-none select-none">⬡</span>
            <span className="font-mono font-bold text-sm text-violet-200">autonoma</span>
          </Link>
          {/* Public nav: discoverable from every page. */}
          <Link
            href="/live"
            className="font-mono text-[10px] uppercase tracking-wider text-violet-300/60 hover:text-violet-100"
          >
            🔴 live
          </Link>
          {isAdmin && (
            <Link
              href="/admin/ab-compare"
              className="font-mono text-[10px] uppercase tracking-wider text-fuchsia-300/60 hover:text-fuchsia-100"
            >
              a/b compare
            </Link>
          )}
          {projectName && (
            <>
              <span className="font-mono text-violet-800 text-sm select-none">|</span>
              <span className="font-mono text-xs text-violet-300/70 truncate max-w-[200px]">
                {projectName}
              </span>
            </>
          )}
        </div>

        {/* Center — progress (only when round > 0) */}
        {round > 0 && (
          <div className="flex items-center gap-3 flex-shrink-0">
            <span className="font-mono text-xs text-violet-300">
              R{round}/{maxRounds}
            </span>
            <div className="h-0.5 w-24 rounded-full bg-violet-900/50 overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-violet-600 to-cyan-500 transition-all duration-500"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            {sky && (
              <div className="flex items-center gap-1.5">
                <span
                  className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${skyDotClass(sky)}`}
                />
                <span className="font-mono text-[10px] text-violet-400/60 lowercase">
                  {sky}
                </span>
              </div>
            )}
          </div>
        )}

        {/* Right — WS status */}
        <div
          role="status"
          aria-live="polite"
          aria-label={connected ? "WebSocket connected" : "WebSocket disconnected"}
          className="flex items-center gap-1.5 flex-shrink-0"
        >
          <span
            aria-hidden="true"
            className={`inline-block w-1.5 h-1.5 rounded-full ${
              connected
                ? "bg-violet-500"
                : "bg-rose-500 animate-pulse"
            }`}
          />
          <span
            aria-hidden="true"
            className={`font-mono text-[9px] ${
              connected ? "text-violet-500/50" : "text-rose-400"
            }`}
          >
            WS
          </span>
        </div>

      </div>
    </header>
  );
}

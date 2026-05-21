"use client";

/**
 * Channel-points spend UI — drop a fortune cookie on an agent.
 *
 * Tiny popover button that lets a viewer pay 50 pts to land a cookie
 * on a named agent's tile. The agent list comes from the parent
 * (typically the swarm state) so we don't have to re-fetch.
 *
 * On 402 (``insufficient_balance``) the inline message updates instead
 * of throwing — the parent never needs to know about the failure mode.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { PointsApiError, type UsePointsResult } from "@/hooks/usePoints";

const COOKIE_COST = 50;

export interface DropCookieButtonProps {
  /** Live agents in the session. Names are used as the spend target. */
  agents: Array<{ name: string }>;
  /** Shared points hook so the chip + button see the same balance. */
  points: UsePointsResult;
  className?: string;
}

export default function DropCookieButton({
  agents,
  points,
  className,
}: DropCookieButtonProps) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const el = popoverRef.current;
      if (el && e.target instanceof Node && !el.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const balance = points.balance;
  const canAfford = balance !== null && balance >= COOKIE_COST;
  // Filter out Director (the meta agent) — handing a cookie to the
  // Director is technically allowed by the API but the visible-on-tile
  // gameplay value is essentially zero, so we hide it from the picker.
  const pickList = useMemo(
    () => agents.filter((a) => a.name && a.name !== "Director"),
    [agents],
  );

  const onPick = async (name: string) => {
    if (pending) return;
    setMessage(null);
    setPending(name);
    try {
      await points.spendCookie(name);
      setMessage(`🥠 dropped on ${name}`);
      // Auto-close after a brief success message so the viewer sees
      // confirmation before the popover vanishes.
      window.setTimeout(() => {
        setOpen(false);
        setMessage(null);
      }, 1200);
    } catch (err) {
      if (err instanceof PointsApiError) {
        if (err.code === "insufficient_balance") {
          setMessage(`Need ${COOKIE_COST} pts — keep watching!`);
        } else if (err.code === "agent_not_found") {
          setMessage(`${name} isn't on stage right now.`);
        } else if (err.code === "swarm_not_running") {
          setMessage("Swarm isn't running yet.");
        } else if (err.code === "agent_busy") {
          setMessage(`${name} already has a cookie.`);
        } else {
          setMessage(err.message);
        }
      } else {
        setMessage(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setPending(null);
    }
  };

  return (
    <div ref={popoverRef} className={"relative " + (className ?? "")}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={
          "pointer-events-auto inline-flex items-center gap-1 rounded-full " +
          "border border-amber-300/40 bg-amber-500/10 px-2.5 py-1 " +
          "font-mono text-[11px] tracking-wider text-amber-200 " +
          "shadow-sm transition hover:bg-amber-500/20 " +
          (canAfford ? "" : "opacity-60 ")
        }
        disabled={pickList.length === 0}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={
          canAfford
            ? `Drop a cookie on an agent (${COOKIE_COST} pts)`
            : `Need ${COOKIE_COST} pts to drop a cookie`
        }
      >
        <span aria-hidden>🍪</span>
        <span>Drop ({COOKIE_COST})</span>
      </button>

      {open ? (
        <div
          role="dialog"
          aria-label="Pick an agent to drop a cookie on"
          className={
            "pointer-events-auto absolute bottom-full right-0 mb-2 w-56 " +
            "rounded-lg border border-white/15 bg-black/85 p-2 " +
            "font-mono text-[11px] text-white shadow-xl backdrop-blur"
          }
        >
          <p className="mb-1 px-1 text-white/60">
            Cost: {COOKIE_COST} pts · You: {balance ?? "—"}
          </p>
          {pickList.length === 0 ? (
            <p className="px-1 py-2 text-white/50">No agents on stage.</p>
          ) : (
            <ul className="max-h-48 overflow-y-auto">
              {pickList.map((a) => (
                <li key={a.name}>
                  <button
                    type="button"
                    onClick={() => void onPick(a.name)}
                    disabled={!canAfford || pending !== null}
                    className={
                      "w-full rounded px-2 py-1 text-left transition " +
                      "hover:bg-white/10 disabled:cursor-not-allowed " +
                      "disabled:opacity-40"
                    }
                  >
                    {pending === a.name ? "…" : "🥠"} {a.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {message ? (
            <p
              className={
                "mt-1 px-1 text-[10px] " +
                (message.startsWith("🥠")
                  ? "text-emerald-300"
                  : "text-rose-300")
              }
              role="status"
            >
              {message}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

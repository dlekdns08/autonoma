"use client";

/**
 * Spectator → Director advisory hint composer for the multi-viewer
 * watch room.
 *
 *   <DirectorHintComposer sendHint={sendDirectorHint} />
 *
 * One textarea + one submit button. The text travels through
 * ``sanitize → cap → buffer → next situation report`` on the server, and
 * arrives as a ``director.hint`` broadcast which ChatOverlay renders
 * with a 💡 marker.
 *
 * Throttling is enforced by the server (10s per viewer). We mirror it
 * here as an optimistic UI lockout so the button visibly disables for
 * the same window — the server's ``director.hint_rejected`` event clears
 * the lockout early if the server thinks the cooldown is shorter, and
 * extends it when the server pushes back with a ``retry_in_ms``.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";

const MAX_CHARS = 140;
const CLIENT_COOLDOWN_MS = 10_000;

interface Props {
  /** Send the hint text to the server. Wired to ``useSwarm().sendDirectorHint``. */
  sendHint: (text: string) => void;
  /** Live WebSocket ref so the composer can subscribe to
   *  ``director.hint_rejected`` events and adjust the cooldown timer.
   *  Pass the ref OBJECT (not ``.current``) so ``react-hooks/refs``
   *  doesn't flag a render-time deref at the call site. */
  wsRef?: RefObject<WebSocket | null>;
  /** Connection-up flag — flipped by the parent when ``wsRef.current``
   *  is OPEN. The composer re-subscribes whenever this toggles so a
   *  reconnect picks up the new socket without needing the ref to be
   *  observable. */
  connected?: boolean;
  /** Hide the composer entirely (e.g. before a swarm has started). */
  disabled?: boolean;
}

export default function DirectorHintComposer({
  sendHint,
  wsRef,
  connected = false,
  disabled = false,
}: Props) {
  const [text, setText] = useState("");
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [serverReason, setServerReason] = useState<string | null>(null);

  // 250ms tick — fine enough that the countdown feels alive without
  // being expensive. We only schedule the interval while a cooldown is
  // active so the idle-state has zero timers.
  useEffect(() => {
    if (cooldownUntil <= now) return;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [cooldownUntil, now]);

  // Subscribe to server-side rejection events. The same WS feeds
  // useSwarm, so this listener is purely additive — we don't preventDefault
  // or steal the event, just read it and react. We re-subscribe whenever
  // ``connected`` flips because that's the only signal we get when the
  // ref's underlying socket has been swapped (a reconnect).
  useEffect(() => {
    const ws = wsRef?.current;
    if (!ws || !connected) return;
    const handler = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(ev.data) as {
          event?: string;
          data?: { reason?: string; retry_in_ms?: number };
        };
        if (msg.event !== "director.hint_rejected") return;
        const reason = msg.data?.reason ?? "rejected";
        const retry = Math.max(0, msg.data?.retry_in_ms ?? 0);
        setServerReason(reason);
        if (retry > 0) {
          setCooldownUntil(Date.now() + retry);
        } else {
          // Non-throttle rejection (e.g. no_active_swarm) — release the
          // optimistic lockout immediately so the viewer can retry once
          // the swarm comes back up.
          setCooldownUntil(0);
        }
      } catch {
        // Non-JSON or unrelated frame — silently ignore.
      }
    };
    ws.addEventListener("message", handler);
    return () => ws.removeEventListener("message", handler);
  }, [wsRef, connected]);

  const cooldownRemainingMs = Math.max(0, cooldownUntil - now);
  const isThrottled = cooldownRemainingMs > 0;
  const trimmedLen = text.trim().length;
  const sendDisabled = disabled || isThrottled || trimmedLen === 0;

  const submit = useCallback(() => {
    if (sendDisabled) return;
    sendHint(text);
    setText("");
    setServerReason(null);
    setCooldownUntil(Date.now() + CLIENT_COOLDOWN_MS);
  }, [sendDisabled, sendHint, text]);

  const onKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      // Cmd/Ctrl-Enter submits — matches the convention in most chat UIs
      // and avoids accidental sends while the viewer is editing.
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        submit();
      }
    },
    [submit],
  );

  const onChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      // Hard-cap input client-side. The server re-truncates after
      // sanitization so even if a paste sneaks past this, nothing
      // catastrophic happens — it just gets clipped.
      setText(e.target.value.slice(0, MAX_CHARS));
    },
    [],
  );

  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  if (disabled) return null;

  return (
    <div className="pointer-events-auto flex w-full flex-col gap-1 rounded-md border border-amber-400/40 bg-black/70 p-2 backdrop-blur-sm">
      <label
        htmlFor="director-hint-composer"
        className="font-mono text-[10px] uppercase tracking-wider text-amber-300/80"
      >
        💡 Hint to Director
      </label>
      <textarea
        id="director-hint-composer"
        ref={composerRef}
        value={text}
        onChange={onChange}
        onKeyDown={onKeyDown}
        placeholder="Suggest a focus, edge case, priority…"
        maxLength={MAX_CHARS}
        rows={2}
        className="w-full resize-none rounded bg-black/40 px-2 py-1 font-mono text-xs text-white placeholder:text-white/30 focus:outline-none focus:ring-1 focus:ring-amber-400/50"
      />
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] text-white/40">
          {trimmedLen}/{MAX_CHARS}
          {isThrottled
            ? ` · wait ${Math.ceil(cooldownRemainingMs / 1000)}s`
            : serverReason === "no_active_swarm"
              ? " · no swarm running"
              : ""}
        </span>
        <button
          type="button"
          onClick={submit}
          disabled={sendDisabled}
          className="rounded bg-amber-500/80 px-2.5 py-1 font-mono text-[11px] font-bold text-black transition disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-white/40"
        >
          Send hint
        </button>
      </div>
    </div>
  );
}

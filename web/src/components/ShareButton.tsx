"use client";

/**
 * Live-share — host-facing visibility toggle + share modal.
 *
 * Mounts a single button on the host dashboard. Clicking it opens a
 * modal that:
 *
 *  - Toggles the caller's currently-hosted room between public and
 *    private (POSTs ``/api/live-share/visibility``).
 *  - When public: lets the host edit a title (max 120) + description
 *    (max 400) and re-save.
 *  - Surfaces a copy-link section (``/share/<code>``), a "Open share
 *    page" out-link, a Tweet/X intent link, and an embed iframe
 *    snippet — each with its own copy-to-clipboard affordance.
 *
 * The button renders nothing if ``roomCode`` is null/empty (no live
 * room means there's nothing to share). ``sessionId`` is accepted for
 * forward-compat with future per-session metadata fetches but is not
 * required by the visibility endpoint (which resolves the room from
 * the cookie session).
 *
 * Styling matches ``LiveQuestPanel`` / ``BGMToggle`` — rounded-2xl
 * borders, slate-950 surfaces, mono labels.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  LiveShareApiError,
  setVisibility,
  type LiveSession,
} from "@/lib/liveShare";
import { useModalA11y } from "@/hooks/useModalA11y";

const TITLE_MAX = 120;
const DESCRIPTION_MAX = 400;

export interface ShareButtonProps {
  roomCode: string | null;
  // ``sessionId`` is accepted for forward-compat (e.g. future analytics
  // on the share modal) but the visibility endpoint doesn't use it —
  // the backend resolves "the caller's current room" from the cookie.
  sessionId: number | null;
}

export default function ShareButton({ roomCode, sessionId }: ShareButtonProps) {
  // The button itself is suppressed when there's no room to share.
  // ``sessionId`` is intentionally not part of the suppression check
  // because a host may have a room code before the session has been
  // assigned an id (rare, but the visibility endpoint will still work
  // — it doesn't read the id either). Reference it once so the prop
  // doesn't go unused under TS strict.
  void sessionId;

  const [open, setOpen] = useState(false);

  if (!roomCode) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-2xl border border-fuchsia-400/40 bg-fuchsia-500/15 px-3 py-1.5 font-mono text-[11px] text-fuchsia-100 transition-colors hover:bg-fuchsia-500/30"
        aria-label="Share this live show"
      >
        🔗 Share live show
      </button>
      {open ? (
        <ShareModal roomCode={roomCode} onClose={() => setOpen(false)} />
      ) : null}
    </>
  );
}

interface ShareModalProps {
  roomCode: string;
  onClose: () => void;
}

function ShareModal({ roomCode, onClose }: ShareModalProps) {
  // Resolve the public origin once the modal opens so the snippets
  // and copy-link textboxes show real URLs (window is only available
  // in the browser — the parent button is also "use client", so this
  // is safe inside a useEffect/useState pairing).
  const [origin, setOrigin] = useState("");
  useEffect(() => {
    if (typeof window !== "undefined") {
      setOrigin(window.location.origin);
    }
  }, []);

  const containerRef = useModalA11y<HTMLDivElement>({ onEscape: onClose });

  // ----- Visibility state ---------------------------------------------------
  // ``isPublic`` mirrors the most recent server response. The first
  // toggle blindly POSTs {public: true} with empty title/desc; the
  // server returns the resulting LiveSession which we then echo back
  // into local state for editing.
  const [isPublic, setIsPublic] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const applySession = useCallback((session: LiveSession) => {
    setIsPublic(session.is_public);
    setTitle(session.title ?? "");
    setDescription(session.description ?? "");
  }, []);

  const handleToggle = useCallback(
    async (next: boolean) => {
      setBusy(true);
      setError(null);
      try {
        const session = await setVisibility(
          next
            ? { public: true, title, description }
            : { public: false },
        );
        applySession(session);
        setSavedAt(Date.now());
      } catch (err) {
        if (err instanceof LiveShareApiError && err.status === 404) {
          setError("이 계정으로 진행 중인 라이브 방을 찾지 못했어요.");
        } else {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        setBusy(false);
      }
    },
    [applySession, description, title],
  );

  const handleSaveMeta = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const session = await setVisibility({
        public: true,
        title,
        description,
      });
      applySession(session);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [applySession, description, title]);

  // ----- Derived URLs --------------------------------------------------------
  const shareUrl = useMemo(
    () => (origin ? `${origin}/share/${roomCode}` : ""),
    [origin, roomCode],
  );
  const watchUrl = useMemo(
    () => (origin ? `${origin}/watch/${roomCode}` : ""),
    [origin, roomCode],
  );
  const tweetUrl = useMemo(() => {
    const base = "https://twitter.com/intent/tweet";
    const text = (title || "Watch a self-organizing agent swarm live").slice(
      0,
      200,
    );
    const params = new URLSearchParams({ text, url: shareUrl });
    return `${base}?${params.toString()}`;
  }, [shareUrl, title]);
  const embedSnippet = useMemo(
    () =>
      origin
        ? `<iframe src="${origin}/watch/${roomCode}?embed=1" width="640" height="360" allow="autoplay; clipboard-write" frameborder="0"></iframe>`
        : "",
    [origin, roomCode],
  );

  return (
    <div
      role="presentation"
      onClick={(e) => {
        // Click on backdrop closes the modal; clicks inside the
        // dialog container are stopped from bubbling here.
        if (e.target === e.currentTarget) onClose();
      }}
      className="fixed inset-0 z-[120] flex items-center justify-center bg-black/70 backdrop-blur-sm"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-modal-title"
        className="flex max-h-[90vh] w-[min(560px,92vw)] flex-col gap-4 overflow-y-auto rounded-2xl border border-white/10 bg-slate-950/95 p-5 text-white shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3">
          <h2
            id="share-modal-title"
            className="font-mono text-sm font-semibold uppercase tracking-wider text-white/80"
          >
            🔗 Share live show
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[10px] text-white/60 hover:bg-white/10"
            aria-label="Close share modal"
          >
            ✕
          </button>
        </div>

        {error ? (
          <div
            role="alert"
            className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 font-mono text-[11px] text-rose-200"
          >
            {error}
          </div>
        ) : null}

        {/* ── Public toggle ─────────────────────────────────────────────── */}
        <section className="flex flex-col gap-2 rounded-xl border border-white/10 bg-slate-900/60 p-3">
          <label className="flex items-start gap-3 font-mono text-xs text-white/80">
            <input
              type="checkbox"
              checked={isPublic}
              disabled={busy}
              onChange={(e) => void handleToggle(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-fuchsia-400"
              aria-label="Make this room public"
            />
            <span className="flex flex-col gap-0.5">
              <span className="font-semibold text-white/90">
                Make this room public
              </span>
              <span className="text-[10px] text-white/50">
                When enabled, your room appears on{" "}
                <span className="text-fuchsia-300">/live</span> and anyone
                with the link can watch.
              </span>
            </span>
          </label>
          {savedAt ? (
            <p className="font-mono text-[10px] text-emerald-300/80">
              ✓ Saved
            </p>
          ) : null}
        </section>

        {/* ── Title + description (only when public) ───────────────────── */}
        {isPublic ? (
          <section className="flex flex-col gap-3 rounded-xl border border-white/10 bg-slate-900/60 p-3">
            <label className="flex flex-col gap-1 font-mono text-[10px] uppercase tracking-wider text-white/50">
              <span>Title</span>
              <input
                type="text"
                value={title}
                maxLength={TITLE_MAX}
                onChange={(e) =>
                  setTitle(e.target.value.slice(0, TITLE_MAX))
                }
                placeholder="e.g. Coding bots vs. impossible quest"
                className="rounded border border-white/10 bg-slate-950/60 px-2 py-1.5 font-mono text-xs normal-case text-white placeholder:text-white/30 focus:border-fuchsia-400/50 focus:outline-none"
              />
              <span className="self-end font-mono text-[10px] tabular-nums text-white/40">
                {title.length}/{TITLE_MAX}
              </span>
            </label>

            <label className="flex flex-col gap-1 font-mono text-[10px] uppercase tracking-wider text-white/50">
              <span>Description</span>
              <textarea
                value={description}
                maxLength={DESCRIPTION_MAX}
                rows={3}
                onChange={(e) =>
                  setDescription(e.target.value.slice(0, DESCRIPTION_MAX))
                }
                placeholder="Tell viewers what's happening on stream."
                className="rounded border border-white/10 bg-slate-950/60 px-2 py-1.5 font-mono text-xs normal-case text-white placeholder:text-white/30 focus:border-fuchsia-400/50 focus:outline-none"
              />
              <span className="self-end font-mono text-[10px] tabular-nums text-white/40">
                {description.length}/{DESCRIPTION_MAX}
              </span>
            </label>

            <button
              type="button"
              onClick={() => void handleSaveMeta()}
              disabled={busy}
              className="self-end rounded-lg border border-fuchsia-400/40 bg-fuchsia-500/15 px-3 py-1 font-mono text-xs text-fuchsia-100 hover:bg-fuchsia-500/30 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy ? "Saving…" : "Save"}
            </button>
          </section>
        ) : null}

        {/* ── Share links (always visible — even private rooms can use a
              direct link, though /share/<code> will 404 in that case). */}
        <section className="flex flex-col gap-3 rounded-xl border border-white/10 bg-slate-900/60 p-3">
          <CopyRow
            label="Share link"
            value={shareUrl}
            id="share-url"
          />

          <div className="flex flex-wrap gap-2">
            <a
              href={shareUrl || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded border border-fuchsia-400/40 bg-fuchsia-500/15 px-2.5 py-1 font-mono text-[11px] text-fuchsia-100 hover:bg-fuchsia-500/30"
            >
              ↗ Open share page
            </a>
            <a
              href={tweetUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded border border-cyan-400/40 bg-cyan-500/15 px-2.5 py-1 font-mono text-[11px] text-cyan-100 hover:bg-cyan-500/30"
            >
              𝕏 Tweet
            </a>
            <a
              href={watchUrl || "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded border border-white/15 bg-white/5 px-2.5 py-1 font-mono text-[11px] text-white/70 hover:bg-white/10"
            >
              ▶ Watch page
            </a>
          </div>

          <CopyRow
            label="Embed snippet"
            value={embedSnippet}
            id="embed-snippet"
            multiline
          />
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CopyRow — labelled read-only input + clipboard button. Lives inside
// this file because it isn't reused elsewhere.
// ---------------------------------------------------------------------------

function CopyRow({
  label,
  value,
  id,
  multiline,
}: {
  label: string;
  value: string;
  id: string;
  multiline?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  const onCopy = useCallback(async () => {
    if (!value) return;
    try {
      // ``navigator.clipboard`` may be undefined in insecure contexts
      // (e.g. http://192.168.x dev preview) — fall back to selecting
      // the input so the user can hit Cmd/Ctrl+C manually.
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        inputRef.current?.focus();
        inputRef.current?.select();
        document.execCommand?.("copy");
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      // Silently ignore — the read-only field still lets the user
      // copy manually.
    }
  }, [value]);

  return (
    <label
      htmlFor={id}
      className="flex flex-col gap-1 font-mono text-[10px] uppercase tracking-wider text-white/50"
    >
      <span>{label}</span>
      <div className="flex items-stretch gap-2">
        {multiline ? (
          <textarea
            ref={(el) => {
              inputRef.current = el;
            }}
            id={id}
            readOnly
            value={value}
            rows={2}
            onFocus={(e) => e.currentTarget.select()}
            className="flex-1 rounded border border-white/10 bg-slate-950/60 px-2 py-1.5 font-mono text-[11px] normal-case text-white/85 focus:border-fuchsia-400/50 focus:outline-none"
          />
        ) : (
          <input
            ref={(el) => {
              inputRef.current = el;
            }}
            id={id}
            readOnly
            value={value}
            onFocus={(e) => e.currentTarget.select()}
            className="flex-1 rounded border border-white/10 bg-slate-950/60 px-2 py-1.5 font-mono text-[11px] normal-case text-white/85 focus:border-fuchsia-400/50 focus:outline-none"
          />
        )}
        <button
          type="button"
          onClick={() => void onCopy()}
          disabled={!value}
          aria-label={`Copy ${label}`}
          className="rounded border border-white/15 bg-white/5 px-2 font-mono text-[11px] text-white/80 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {copied ? "✓" : "Copy"}
        </button>
      </div>
    </label>
  );
}

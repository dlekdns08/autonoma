"use client";

/**
 * Highlight Clip Generator — viewer-side button.
 *
 * Sits in the watch page's top-right area. Internally it owns:
 *
 *   - a ``useRollingRecorder`` instance bound to the largest visible
 *     canvas (the VTuber spotlight + 2D map are siblings; the spotlight
 *     usually wins on area). The recorder auto-starts on mount so the
 *     viewer always has a 30s buffer ready at click time.
 *   - a transient inline status pill that surfaces "uploading…" then
 *     the share URL after a successful upload, with a copy-link
 *     affordance.
 *   - graceful degradation when MediaRecorder is unavailable (older
 *     browsers / iOS quirks): the button renders disabled with a
 *     tooltip explaining the lack of support, matching the DoD's
 *     "no crash if MediaRecorder unavailable" requirement.
 *
 * Why is the upload path here and not in a lib helper? It's a single
 * fetch and the surrounding state lives entirely inside this widget —
 * splitting it would add indirection for no reuse benefit. If a second
 * caller (e.g. an auto-clip overlay) shows up later we can lift it
 * into ``web/src/lib/clipsApi.ts``.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE_URL } from "@/hooks/useSwarm";
import { useRollingRecorder } from "@/hooks/useRollingRecorder";

interface ClipButtonProps {
  /** Server session id this clip belongs to. The button stays disabled
   *  until a session is bound — without it the upload would 404 in
   *  the session ownership check. */
  sessionId: number | null;
  /** Optional room code used as a fallback title (no DB lookup, just
   *  flavour) when the user doesn't provide one. */
  title?: string;
}

type Status =
  | { kind: "idle" }
  | { kind: "uploading" }
  | { kind: "ok"; clipId: string; url: string }
  | { kind: "error"; message: string };

/** Lifetime of the success/error pill before it auto-dismisses. Long
 *  enough to read + copy, short enough to not block the stage. */
const STATUS_TIMEOUT_MS = 12_000;

export default function ClipButton({ sessionId, title }: ClipButtonProps) {
  const recorder = useRollingRecorder({ durationSec: 30 });
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const dismissTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Start the rolling capture as soon as the canvas is in the DOM.
  // ``recorder.start()`` is idempotent; the dependency on
  // ``recorder.supported`` makes sure we wait until the feature
  // detection has resolved.
  useEffect(() => {
    if (!recorder.supported) return;
    // Delay one tick so the watch page's canvases have mounted; without
    // this the largest-canvas finder picks nothing on first paint.
    const t = setTimeout(() => recorder.start(), 250);
    return () => {
      clearTimeout(t);
      // ``recorder.stop()`` is run by useRollingRecorder's own unmount
      // cleanup — no need to call it here.
    };
  }, [recorder.supported, recorder]);

  // Cancel any pending dismiss timer on unmount so an unmounted setState
  // doesn't fire after the page navigates.
  useEffect(() => {
    return () => {
      if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
    };
  }, []);

  const scheduleDismiss = useCallback(() => {
    if (dismissTimerRef.current) clearTimeout(dismissTimerRef.current);
    dismissTimerRef.current = setTimeout(() => {
      setStatus({ kind: "idle" });
    }, STATUS_TIMEOUT_MS);
  }, []);

  const onClip = useCallback(async () => {
    if (!recorder.supported) return;
    if (sessionId === null || sessionId <= 0) {
      setStatus({
        kind: "error",
        message: "세션이 아직 연결되지 않았어요.",
      });
      scheduleDismiss();
      return;
    }
    setStatus({ kind: "uploading" });
    const snap = await recorder.flush();
    if (!snap) {
      setStatus({
        kind: "error",
        message: "녹화 버퍼가 비어있어요. 잠시 후 다시 시도해주세요.",
      });
      scheduleDismiss();
      return;
    }

    // ``snap.blob`` carries the mime in its own ``type`` field but we
    // pass it explicitly on the multipart filename + content-type for
    // belt-and-suspenders so the backend's content_type lookup never
    // ends up as ``application/octet-stream``.
    const ext = snap.mime.includes("mp4") ? "mp4" : "webm";
    const form = new FormData();
    form.append("file", snap.blob, `clip.${ext}`);
    form.append("session_id", String(sessionId));
    form.append("duration_ms", String(snap.durationMs));
    if (title) form.append("title", title);

    try {
      const res = await fetch(`${API_BASE_URL}/api/clips`, {
        method: "POST",
        credentials: "include",
        body: form,
      });
      if (!res.ok) {
        let message = `HTTP ${res.status}`;
        try {
          const detail = (await res.json()) as { detail?: { message?: string } };
          if (detail?.detail?.message) message = detail.detail.message;
        } catch {
          /* ignore non-JSON body */
        }
        setStatus({ kind: "error", message });
        scheduleDismiss();
        return;
      }
      const json = (await res.json()) as { id: string; url: string };
      // ``url`` from the backend is API-side; the share page lives on
      // the same origin as the frontend, so we prefer the in-app route
      // for the toast link.
      const sharePath = `/clips/${json.id}`;
      setStatus({ kind: "ok", clipId: json.id, url: sharePath });
      scheduleDismiss();
    } catch (err) {
      setStatus({
        kind: "error",
        message: err instanceof Error ? err.message : "Upload failed",
      });
      scheduleDismiss();
    }
  }, [recorder, sessionId, title, scheduleDismiss]);

  const copyLink = useCallback(async (url: string) => {
    try {
      const absolute =
        typeof window !== "undefined" ? `${window.location.origin}${url}` : url;
      await navigator.clipboard.writeText(absolute);
    } catch {
      // Insecure context or no clipboard permission — fall back to the
      // user manually copying from the visible URL.
    }
  }, []);

  // ── Render: a single floating button + optional status pill ─────
  return (
    <div className="pointer-events-auto flex flex-col items-end gap-2">
      <button
        type="button"
        onClick={onClip}
        disabled={!recorder.supported || status.kind === "uploading"}
        title={
          !recorder.supported
            ? "Clipping not supported on this browser"
            : "Save the last 30 seconds as a shareable clip"
        }
        className={[
          "rounded-full border px-3 py-1.5 font-mono text-[12px] uppercase tracking-wider transition",
          recorder.supported
            ? "border-emerald-400/50 bg-emerald-500/15 text-emerald-100 hover:bg-emerald-500/25"
            : "border-white/10 bg-white/5 text-white/30",
          status.kind === "uploading" ? "opacity-60" : "",
        ].join(" ")}
      >
        {status.kind === "uploading" ? "uploading…" : "📎 Clip"}
      </button>

      {/* Status pill: only rendered when there's a result to show.
          Auto-dismisses after STATUS_TIMEOUT_MS. */}
      {status.kind === "ok" ? (
        <div className="flex max-w-[280px] flex-col gap-1 rounded-md border border-emerald-400/40 bg-emerald-950/85 px-3 py-2 text-[11px] text-emerald-50 shadow-lg shadow-emerald-500/20 backdrop-blur">
          <span className="font-mono uppercase tracking-wider text-emerald-300">
            saved
          </span>
          <a
            href={status.url}
            target="_blank"
            rel="noreferrer"
            className="break-all underline decoration-emerald-300/60 hover:text-emerald-200"
          >
            {status.url}
          </a>
          <button
            type="button"
            onClick={() => void copyLink(status.url)}
            className="self-start rounded border border-emerald-400/40 bg-emerald-500/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider hover:bg-emerald-500/20"
          >
            copy link
          </button>
        </div>
      ) : null}
      {status.kind === "error" ? (
        <div className="max-w-[280px] rounded-md border border-rose-400/40 bg-rose-950/85 px-3 py-1.5 text-[11px] text-rose-100 shadow-lg shadow-rose-500/20 backdrop-blur">
          {status.message}
        </div>
      ) : null}
    </div>
  );
}

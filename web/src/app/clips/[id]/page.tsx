"use client";

/**
 * Highlight Clip Generator — share/playback page (``/clips/<id>``).
 *
 * Standalone landing for a recorded clip. Mounts a ``<video>`` pointed
 * at the backend's blob endpoint and exposes a copy-link button. The
 * page deliberately stays light:
 *
 *   - No room WS, no swarm state — the clip is a static artifact.
 *   - We use ``use("react").use(params)`` to unwrap the Next 16
 *     params Promise without forcing async server-component plumbing;
 *     the page needs ``<video>`` semantics that work better in a
 *     client component anyway (autoplay policies, error events,
 *     ``navigator.clipboard``).
 *
 * On a missing clip the backend's ``/api/clips/{id}`` returns 404; the
 * ``<video>`` element's ``onError`` flips the UI into a "clip not
 * available" state with a back link.
 */

import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { API_BASE_URL } from "@/hooks/useSwarm";

interface ClipPageProps {
  params: Promise<{ id: string }>;
}

export default function ClipPage({ params }: ClipPageProps) {
  // Next 16 makes ``params`` a Promise. ``use(...)`` suspends the
  // component until it resolves, which is what we want — we don't need
  // any further data fetching here so the suspense boundary is the
  // parent layout's default fallback.
  const { id } = use(params);

  const safeId = useMemo(() => id.replace(/[^A-Za-z0-9_-]/g, ""), [id]);
  const src = `${API_BASE_URL}/api/clips/${safeId}`;
  const sharePath = useMemo(() => {
    if (typeof window === "undefined") return `/clips/${safeId}`;
    return `${window.location.origin}/clips/${safeId}`;
  }, [safeId]);

  const [errored, setErrored] = useState(false);
  const [copied, setCopied] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  // ``copied`` is a transient banner — clear it after a couple of
  // seconds so the page doesn't trap the affordance in the "Copied!"
  // state forever.
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 2200);
    return () => clearTimeout(t);
  }, [copied]);

  const copyLink = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(sharePath);
      setCopied(true);
    } catch {
      // Insecure context or no permission. We don't surface an error
      // toast — the URL is already visible in the textbox so the user
      // can copy it manually.
    }
  }, [sharePath]);

  return (
    <div className="min-h-[100dvh] w-screen bg-[#0a0a12] text-white">
      <header className="flex items-center justify-between gap-3 border-b border-white/10 bg-black/60 px-4 py-2 backdrop-blur">
        <Link
          href="/"
          className="rounded-md border border-white/10 bg-white/5 px-2.5 py-1 font-mono text-[11px] text-white/60 hover:bg-white/10"
        >
          ← home
        </Link>
        <div className="font-mono text-[11px] uppercase tracking-wider text-white/55">
          clip <span className="text-white/85">{safeId.slice(0, 8)}</span>
        </div>
        <div className="w-[64px]" /> {/* spacer */}
      </header>

      <main className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
        {errored ? (
          <div className="rounded-lg border border-rose-400/40 bg-rose-950/60 p-6 text-center font-mono text-sm text-rose-100">
            <p>이 클립을 불러올 수 없어요.</p>
            <p className="mt-2 text-rose-200/80">
              파일이 만료되었거나 잘못된 링크일 수 있습니다.
            </p>
            <Link
              href="/"
              className="mt-4 inline-block rounded-md border border-rose-300/40 bg-rose-500/15 px-3 py-1 text-rose-50 hover:bg-rose-500/25"
            >
              홈으로
            </Link>
          </div>
        ) : (
          <>
            <div className="overflow-hidden rounded-lg border border-white/10 bg-black shadow-lg">
              <video
                ref={videoRef}
                src={src}
                controls
                playsInline
                preload="metadata"
                onError={() => setErrored(true)}
                className="aspect-video h-auto w-full bg-black"
              >
                {/* Browsers without the codec render the message below. */}
                이 브라우저는 클립을 재생할 수 없어요.
              </video>
            </div>

            <div className="flex flex-col gap-2 rounded-md border border-white/10 bg-white/5 p-3 text-[12px]">
              <label className="font-mono uppercase tracking-wider text-white/55">
                share link
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  readOnly
                  value={sharePath}
                  onFocus={(e) => e.currentTarget.select()}
                  className="flex-1 rounded border border-white/10 bg-black/40 px-2 py-1 font-mono text-[11px] text-white/85"
                />
                <button
                  type="button"
                  onClick={() => void copyLink()}
                  className="rounded border border-emerald-400/40 bg-emerald-500/15 px-3 py-1 font-mono text-[11px] uppercase tracking-wider text-emerald-100 hover:bg-emerald-500/25"
                >
                  {copied ? "copied!" : "copy"}
                </button>
              </div>
              <p className="text-[11px] text-white/40">
                30초 하이라이트 클립입니다. 같은 스트림 시청자라면 이 링크로 바로 재생할 수 있어요.
              </p>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

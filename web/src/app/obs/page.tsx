"use client";

/**
 * OBS / streaming view.
 *
 *   /obs                       ← default, black bg, studio backdrop, record button
 *   /obs?bg=transparent        ← body bg forced to transparent for OBS Browser
 *                                 Source composite (enable "Shutdown source
 *                                 when not visible" to save CPU)
 *   /obs?bg=green              ← chromakey green (#00b140)
 *   /obs?bg=blue               ← chromakey blue (#0047ab)
 *   /obs?backdrop=studio       ← soft photo-studio cyclorama behind character
 *   /obs?backdrop=default      ← the original cyber/HUD grid + fuchsia glow
 *   /obs?backdrop=none         ← no backdrop (auto-selected for chroma/transparent)
 *   /obs?ui=0                  ← hide the record button (useful when the
 *                                 /obs page itself is being captured)
 *
 * The page reuses `useSwarm` so it's live-synced with the main dashboard:
 * whatever agent is speaking in the primary tab drives the spotlight
 * here too. That means a host can run Autonoma in one tab and point OBS
 * at this route in another.
 *
 * Recording:
 *   The record button captures the spotlight Canvas via
 *   `canvas.captureStream(30)` → MediaRecorder → webm blob → download.
 *   It does *not* capture any HTML overlays (name tag, speech bubble) —
 *   those are outside the canvas — which is fine for clip-sharing on
 *   Twitter/Discord where a bare character is often what you want. For
 *   a full-overlay recording, use OBS itself or `?ui=0` + a screen
 *   capture tool.
 */

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useSwarm } from "@/hooks/useSwarm";
import VTuberStage, { type BackdropPreset } from "@/components/vtuber/VTuberStage";
import ChatOverlay from "@/components/vtuber/ChatOverlay";
import AuthModal from "@/components/AuthModal";

// Next 15 requires `useSearchParams` consumers to be inside a Suspense
// boundary so static prerender can stream without pulling the search
// params synchronously. Thin wrapper satisfies that contract.
export default function ObsPage() {
  return (
    <Suspense fallback={<div className="h-screen w-screen bg-slate-950" />}>
      <ObsContent />
    </Suspense>
  );
}

function ObsContent() {
  const params = useSearchParams();
  const bg = params.get("bg") ?? "default";
  const showUi = params.get("ui") !== "0";

  // Backdrop default: chromakey / transparent modes want `none` so the
  // composited background shows through; otherwise fall back to the
  // studio preset which looks good on the dark default outer bg.
  const bgIsChroma = bg === "transparent" || bg === "green" || bg === "blue";
  const backdropParam = params.get("backdrop");
  const backdrop: BackdropPreset =
    backdropParam === "default" ||
    backdropParam === "studio" ||
    backdropParam === "none"
      ? backdropParam
      : bgIsChroma
        ? "none"
        : "studio";

  const {
    state,
    authState,
    authenticate,
    getMouthAmplitude,
    speakingAgents,
    chat,
  } = useSwarm();

  const needsAuth = authState.status !== "authenticated";
  const showChat = params.get("chat") !== "0";

  // Override the root layout's body background for transparent/chroma
  // modes. We do this via a one-shot effect rather than a route-level
  // layout because the root layout is shared with the main dashboard
  // and we don't want to duplicate it.
  useEffect(() => {
    const prev = document.body.style.background;
    if (bg === "transparent") {
      document.body.style.background = "transparent";
    } else if (bg === "green") {
      document.body.style.background = "#00b140";
    } else if (bg === "blue") {
      document.body.style.background = "#0047ab";
    } else {
      document.body.style.background = "#0a0a12";
    }
    return () => {
      document.body.style.background = prev;
    };
  }, [bg]);

  const outerBg =
    bg === "transparent"
      ? "bg-transparent"
      : bg === "green"
        ? "bg-[#00b140]"
        : bg === "blue"
          ? "bg-[#0047ab]"
          : "bg-slate-950";

  return (
    <div className={`relative h-screen w-screen overflow-hidden ${outerBg}`}>
      {state.agents.length > 0 ? (
        <VTuberStage
          agents={state.agents}
          getMouthAmplitude={getMouthAmplitude}
          speakingAgents={speakingAgents}
          obsMode
          backdrop={backdrop}
        />
      ) : (
        <div className="flex h-full items-center justify-center font-mono text-sm text-white/40">
          Awaiting cast…
        </div>
      )}

      {showChat && <ChatOverlay messages={chat} />}

      {showUi && <RecordButton />}

      {needsAuth && authState.status !== "unknown" && (
        <AuthModal authState={authState} onAuthenticate={authenticate} />
      )}
    </div>
  );
}

// ── MediaRecorder-based clip capture ────────────────────────────────
//
// Grabs the spotlight <canvas> (the only canvas in OBS mode, since the
// gallery is hidden) and streams it into MediaRecorder. We don't render
// a preview of the recording — for now, stopping the recorder triggers
// an immediate download, which is the workflow clip-sharers actually
// want (no "review, then save" round-trip).

function RecordButton() {
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const start = () => {
    // The spotlight canvas is the first (and only) <canvas> in OBS
    // mode. If there isn't one yet, the cast hasn't booted — bail
    // silently rather than surfacing an error.
    const canvas = document.querySelector("canvas");
    if (!canvas) return;
    const stream = (canvas as HTMLCanvasElement).captureStream(30);

    // Prefer vp9 where supported (much smaller files at identical
    // quality), fall back to the codec MediaRecorder picks by default
    // — Safari as of 17 still doesn't support vp9 in webm containers.
    const mime = MediaRecorder.isTypeSupported("video/webm;codecs=vp9")
      ? "video/webm;codecs=vp9"
      : "video/webm";
    const rec = new MediaRecorder(stream, { mimeType: mime });

    chunksRef.current = [];
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    rec.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: "video/webm" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `autonoma-${Date.now()}.webm`;
      a.click();
      // Revoke on the next tick so the download link has time to fire.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    rec.start();
    recorderRef.current = rec;
    setRecording(true);
  };

  const stop = () => {
    recorderRef.current?.stop();
    recorderRef.current = null;
    setRecording(false);
  };

  // Clean up if the component unmounts mid-recording — otherwise the
  // MediaRecorder would keep a reference to the canvas forever.
  useEffect(() => {
    return () => {
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
    };
  }, []);

  return (
    <button
      type="button"
      onClick={recording ? stop : start}
      className={`absolute bottom-3 right-3 rounded-full border px-3 py-1.5 font-mono text-xs font-bold backdrop-blur-sm transition-colors ${
        recording
          ? "border-red-400/70 bg-red-500/30 text-red-100 hover:bg-red-500/50"
          : "border-red-400/50 bg-black/80 text-red-300 hover:bg-red-500/20"
      }`}
      title={recording ? "Stop recording & download" : "Record spotlight canvas to .webm"}
    >
      {recording ? "■ stop" : "● rec"}
    </button>
  );
}

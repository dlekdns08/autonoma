"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Recognized voice command — shape produced by the wake-word parser. */
export interface VoiceCommand {
  /** The name caught after the wake word ("alice", "bear", …). Lowercased. */
  agent: string;
  /** The rest of the utterance. */
  text: string;
  /** Raw transcript including the wake word. */
  raw: string;
  /** Epoch ms when the command finalized. */
  at: number;
}

export interface UseVoiceCommandOptions {
  /** Words that precede the target agent name. Case-insensitive. Default:
   *  ``["hey", "yo", "okay", "오케이", "야", "봐봐"]``. */
  wakeWords?: string[];
  /** Called whenever a new command is recognized. */
  onCommand?: (cmd: VoiceCommand) => void;
  /** Must be true for recognition to start. */
  enabled: boolean;
  /** Language hint for SpeechRecognition. Default ``"ko-KR"``. */
  language?: string;
}

export interface UseVoiceCommand {
  listening: boolean;
  error: string | null;
  /** Most recent final command; also passed via ``onCommand``. */
  last: VoiceCommand | null;
  start: () => void;
  stop: () => void;
  /** True only if the browser supports the Web Speech API. */
  supported: boolean;
}

const DEFAULT_WAKES = ["hey", "yo", "okay", "오케이", "야", "봐봐"];

// Minimal shape of the webkit SpeechRecognition API to avoid lib.dom mismatch.
interface SRResult {
  isFinal: boolean;
  0: { transcript: string };
}
interface SREvent extends Event {
  resultIndex: number;
  results: { length: number; [i: number]: SRResult };
}
interface SRInstance {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: SREvent) => void) | null;
  onerror: ((e: Event) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}
type SRCtor = new () => SRInstance;

function getRecognitionCtor(): SRCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SRCtor;
    webkitSpeechRecognition?: SRCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** Parse "hey bear, fix this bug" → {agent: "bear", text: "fix this bug"}. */
function parseCommand(transcript: string, wakes: string[]): VoiceCommand | null {
  const trimmed = transcript.trim();
  if (!trimmed) return null;
  const lc = trimmed.toLowerCase();
  for (const w of wakes) {
    const key = w.toLowerCase();
    if (lc.startsWith(key + " ")) {
      const rest = trimmed.slice(key.length + 1).trim();
      // First word is the agent name; remove trailing comma.
      const [first, ...tail] = rest.split(/\s+/);
      if (!first) continue;
      const agent = first.replace(/[,.:!?]+$/, "").toLowerCase();
      return {
        agent,
        text: tail.join(" ").replace(/^[,.:!?\s]+/, ""),
        raw: transcript,
        at: Date.now(),
      };
    }
  }
  return null;
}

/** Browser wake-word listener built on the Web Speech API.
 *
 *  Utterances matching ``{wake_word} {agent_name}, {rest}`` become a
 *  ``VoiceCommand`` which the caller typically forwards to the server
 *  as a ``human.feedback`` event tagged ``origin="voice_command"``.
 *
 *  Safari (iOS) does not expose SpeechRecognition. ``supported`` is
 *  ``false`` there; the UI should fall back to typed input.
 */
export function useVoiceCommand({
  wakeWords,
  onCommand,
  enabled,
  language = "ko-KR",
}: UseVoiceCommandOptions): UseVoiceCommand {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<VoiceCommand | null>(null);
  const recRef = useRef<SRInstance | null>(null);
  const onCommandRef = useRef(onCommand);
  onCommandRef.current = onCommand;
  const wakes = (wakeWords && wakeWords.length > 0 ? wakeWords : DEFAULT_WAKES);
  const supported = getRecognitionCtor() !== null;

  const start = useCallback(() => {
    if (!supported || recRef.current) return;
    const Ctor = getRecognitionCtor();
    if (!Ctor) return;
    const rec = new Ctor();
    rec.lang = language;
    rec.continuous = true;
    rec.interimResults = false;
    rec.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i];
        if (!r.isFinal) continue;
        const cmd = parseCommand(r[0].transcript, wakes);
        if (cmd) {
          setLast(cmd);
          onCommandRef.current?.(cmd);
        }
      }
    };
    rec.onerror = (e) => {
      const msg = (e as ErrorEvent).message || "speech recognition error";
      setError(msg);
    };
    rec.onend = () => {
      // If still enabled, re-start (Chrome drops the stream on silence).
      if (recRef.current === rec) {
        try {
          rec.start();
        } catch {
          setListening(false);
          recRef.current = null;
        }
      }
    };
    recRef.current = rec;
    try {
      rec.start();
      setListening(true);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      recRef.current = null;
      setListening(false);
    }
  }, [language, supported, wakes]);

  const stop = useCallback(() => {
    const rec = recRef.current;
    recRef.current = null;
    if (rec) {
      try { rec.stop(); } catch { /* already stopped */ }
    }
    setListening(false);
  }, []);

  useEffect(() => {
    if (enabled) start();
    else stop();
    return stop;
  }, [enabled, start, stop]);

  return { listening, error, last, start, stop, supported };
}

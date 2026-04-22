/**
 * Trigger whitelist — frontend mirror of
 * ``src/autonoma/mocap/triggers.py``. Binding UIs compare against these
 * lists so the user can only pick triggers the renderer knows about, and
 * server-side validation rejects anything that slips through.
 *
 * Keep this list in lock-step with the Python side. The server also
 * exposes a live catalog at ``GET /api/mocap/triggers``; components that
 * want the authoritative list at mount time should fetch that endpoint
 * and fall back to this module when offline / pre-hydrate.
 */

export const MOOD_TRIGGERS = [
  "idle",
  "happy",
  "excited",
  "proud",
  "frustrated",
  "worried",
  "relaxed",
  "determined",
  "focused",
  "curious",
  "tired",
  "nostalgic",
  "inspired",
  "mischievous",
  "friendly",
] as const;
export type MoodTrigger = (typeof MOOD_TRIGGERS)[number];

/** Matches ``EMOTE_GESTURE_MAP`` keys in VRMCharacter.tsx. */
export const EMOTE_TRIGGERS = [
  "✦",
  "★",
  "‼",
  "💡",
  "♪",
  "?",
  "•",
  "💧",
  "💤",
  "💢",
  "✧",
  "～",
  "✿",
] as const;
export type EmoteTrigger = (typeof EMOTE_TRIGGERS)[number];

export const STATE_TRIGGERS = [
  "idle",
  "working",
  "talking",
  "thinking",
  "celebrating",
  "spawning",
  "error",
] as const;
export type StateTrigger = (typeof STATE_TRIGGERS)[number];

export const TRIGGER_KINDS = ["mood", "emote", "state", "manual"] as const;
export type TriggerKind = (typeof TRIGGER_KINDS)[number];

/** Same validator the server uses for ``kind === "manual"``. */
export const MANUAL_SLUG_RE = /^[a-z0-9_-]{1,32}$/;

/** Human-readable labels for the binding editor. Icons are shown as-is;
 *  mood + state get a translated label because the raw slug reads as
 *  jargon to end users. */
export const MOOD_LABELS: Record<MoodTrigger, string> = {
  idle: "대기",
  happy: "기쁨",
  excited: "흥분",
  proud: "자랑",
  frustrated: "짜증",
  worried: "걱정",
  relaxed: "편안",
  determined: "결연",
  focused: "집중",
  curious: "호기심",
  tired: "피곤",
  nostalgic: "향수",
  inspired: "영감",
  mischievous: "장난",
  friendly: "친근",
};

export const STATE_LABELS: Record<StateTrigger, string> = {
  idle: "대기",
  working: "작업 중",
  talking: "말하는 중",
  thinking: "생각 중",
  celebrating: "축하",
  spawning: "등장",
  error: "오류",
};

/** One-line description of what each emote icon means. Surfaced as hover
 *  text so the picker doesn't just show a wall of glyphs. */
export const EMOTE_LABELS: Record<EmoteTrigger, string> = {
  "✦": "흥분",
  "★": "자랑",
  "‼": "결연",
  "💡": "영감",
  "♪": "기쁨",
  "?": "호기심",
  "•": "집중",
  "💧": "걱정",
  "💤": "피곤",
  "💢": "짜증",
  "✧": "장난",
  "～": "편안",
  "✿": "향수",
};

export function validateTrigger(kind: TriggerKind, value: string): string | null {
  if (!value) return "invalid_value";
  switch (kind) {
    case "mood":
      return (MOOD_TRIGGERS as readonly string[]).includes(value)
        ? null
        : "unknown_mood";
    case "emote":
      return (EMOTE_TRIGGERS as readonly string[]).includes(value)
        ? null
        : "unknown_emote";
    case "state":
      return (STATE_TRIGGERS as readonly string[]).includes(value)
        ? null
        : "unknown_state";
    case "manual":
      return MANUAL_SLUG_RE.test(value) ? null : "invalid_manual_slug";
  }
}

export interface TriggerCatalog {
  mood: readonly string[];
  emote: readonly string[];
  state: readonly string[];
}

export const DEFAULT_TRIGGER_CATALOG: TriggerCatalog = {
  mood: MOOD_TRIGGERS,
  emote: EMOTE_TRIGGERS,
  state: STATE_TRIGGERS,
};

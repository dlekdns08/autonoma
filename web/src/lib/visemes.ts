/**
 * Text → vowel-shape (viseme) timeline for lip-sync.
 *
 * The stage drives the five VRM vowel blendshapes (aa / ih / ou / ee /
 * oh). Energy from the audio analyser decides how far the mouth opens;
 * this module decides *which shape* it opens into, from the text of the
 * line that is being spoken. No phoneme model: one syllable → one
 * vowel, timed proportionally across the utterance's real duration
 * (known once the <audio> element has metadata). That is enough for
 * the mouth to read as "saying the words" instead of flapping through
 * a random vowel cycle.
 *
 * Scripts handled:
 *   - Hangul syllables: decomposed to their medial vowel (중성).
 *   - Kana: vowel row from the hiragana/katakana table.
 *   - Latin: each vowel cluster of a word is one syllable.
 *   - Anything else (CJK ideographs, digits, …): one "aa" per char.
 *   - Punctuation and whitespace become short rests (mouth closed).
 *
 * Pure functions, no DOM — unit-tested in `__tests__/visemes.test.ts`.
 */

export type Viseme = "aa" | "ih" | "ou" | "ee" | "oh";

export interface VisemeCue {
  /** Start of the cue, seconds from the start of the utterance. */
  t: number;
  /** Duration in seconds. */
  d: number;
  /** ``null`` is a rest (pause between words / punctuation). */
  v: Viseme | null;
}

// Relative durations. A rest is shorter than a syllable so a comma
// does not look like a held breath.
const SYLLABLE_W = 1.0;
const REST_W = 0.55;
const SPACE_W = 0.3;

// Hangul 중성 (medial vowel) index → viseme. Order follows the Unicode
// jamo table: ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ.
const HANGUL_MEDIAL: Viseme[] = [
  "aa", "ee", "aa", "ee", "oh", "ee", "oh", "ee", "oh", "aa", "ee",
  "ee", "oh", "ou", "oh", "ee", "ih", "ou", "ih", "ih", "ih",
];

const LATIN_VOWEL: Record<string, Viseme> = {
  a: "aa", e: "ee", i: "ih", o: "oh", u: "ou", y: "ih",
  à: "aa", á: "aa", â: "aa", ä: "aa", è: "ee", é: "ee", ê: "ee", ë: "ee",
  ì: "ih", í: "ih", î: "ih", ï: "ih", ò: "oh", ó: "oh", ô: "oh", ö: "oh",
  ù: "ou", ú: "ou", û: "ou", ü: "ou",
};

// Kana vowel rows. Small kana (ゃゅょ, ャュョ, っ/ッ) are folded into the
// preceding syllable — ゃ/ゅ/ょ change its vowel, っ is a short rest.
const KANA_ROW: Record<string, Viseme> = {};
(function buildKana() {
  const rows: Array<[string, Viseme]> = [
    ["あかさたなはまやらわがざだばぱぁゃ", "aa"],
    ["いきしちにひみりゐぎじぢびぴぃ", "ih"],
    ["うくすつぬふむゆるぐずづぶぷぅゅ", "ou"],
    ["えけせてねへめれゑげぜでべぺぇ", "ee"],
    ["おこそとのほもよろをごぞどぼぽぉょ", "oh"],
  ];
  for (const [chars, v] of rows) {
    for (const c of chars) {
      KANA_ROW[c] = v;
      // Katakana is hiragana + 0x60 for the same row.
      KANA_ROW[String.fromCharCode(c.charCodeAt(0) + 0x60)] = v;
    }
  }
})();
const KANA_SMALL_VOWEL = new Set("ゃゅょャュョ");

function isHangulSyllable(code: number): boolean {
  return code >= 0xac00 && code <= 0xd7a3;
}

function isLatinLetter(ch: string): boolean {
  return /\p{Script=Latin}/u.test(ch);
}

interface Unit {
  w: number;
  v: Viseme | null;
}

/** Split text into weighted syllable/rest units. */
export function syllabify(text: string): Unit[] {
  const units: Unit[] = [];
  const chars = Array.from(text.normalize("NFC"));
  let i = 0;
  while (i < chars.length) {
    const ch = chars[i];
    const code = ch.codePointAt(0) ?? 0;

    if (/\s/u.test(ch)) {
      // Collapse runs of whitespace into one short rest.
      if (units.length && units[units.length - 1].v === null) {
        i++;
        continue;
      }
      units.push({ w: SPACE_W, v: null });
      i++;
      continue;
    }

    if (isHangulSyllable(code)) {
      const medial = Math.floor(((code - 0xac00) % (21 * 28)) / 28);
      units.push({ w: SYLLABLE_W, v: HANGUL_MEDIAL[medial] ?? "aa" });
      i++;
      continue;
    }

    if (ch in KANA_ROW || ch === "っ" || ch === "ッ" || ch === "ー") {
      if (ch === "っ" || ch === "ッ") {
        units.push({ w: REST_W * 0.5, v: null });
        i++;
        continue;
      }
      if (ch === "ー") {
        // Long vowel: stretch the previous syllable.
        const prev = units[units.length - 1];
        if (prev && prev.v) prev.w += SYLLABLE_W * 0.6;
        i++;
        continue;
      }
      let v = KANA_ROW[ch];
      if (i + 1 < chars.length && KANA_SMALL_VOWEL.has(chars[i + 1])) {
        v = KANA_ROW[chars[i + 1]];
        i++;
      }
      units.push({ w: SYLLABLE_W, v });
      i++;
      continue;
    }

    if (isLatinLetter(ch)) {
      // Consume one word; each vowel cluster is a syllable.
      let j = i;
      let sawVowel = false;
      let inCluster = false;
      while (j < chars.length && isLatinLetter(chars[j])) {
        const v = LATIN_VOWEL[chars[j].toLowerCase()];
        if (v) {
          if (!inCluster) {
            units.push({ w: SYLLABLE_W, v });
            inCluster = true;
            sawVowel = true;
          }
        } else {
          inCluster = false;
        }
        j++;
      }
      if (!sawVowel) units.push({ w: SYLLABLE_W * 0.7, v: "aa" });
      i = j;
      continue;
    }

    if (/[\p{L}\p{N}]/u.test(ch)) {
      // CJK ideographs, digits, other scripts: one open syllable each.
      units.push({ w: SYLLABLE_W, v: "aa" });
      i++;
      continue;
    }

    // Punctuation → rest (merged with an adjacent rest).
    const last = units[units.length - 1];
    if (last && last.v === null) last.w = Math.max(last.w, REST_W);
    else units.push({ w: REST_W, v: null });
    i++;
  }
  // Leading/trailing rests carry no information.
  while (units.length && units[0].v === null) units.shift();
  while (units.length && units[units.length - 1].v === null) units.pop();
  return units;
}

/**
 * Build the cue list for ``text`` spoken over ``durationS`` seconds.
 * Speech rarely fills the audio to the last sample (TTS adds a tail of
 * silence), so the timeline is squeezed into ``durationS * fill``.
 */
export function buildVisemeTimeline(
  text: string,
  durationS: number,
  fill = 0.94,
): VisemeCue[] {
  const units = syllabify(text);
  if (!units.length || !(durationS > 0)) return [];
  const total = units.reduce((s, u) => s + u.w, 0);
  const scale = (durationS * fill) / total;
  const cues: VisemeCue[] = [];
  let t = 0;
  for (const u of units) {
    const d = u.w * scale;
    cues.push({ t, d, v: u.v });
    t += d;
  }
  return cues;
}

/**
 * Sample the timeline at ``timeS``. Returns the current viseme, the
 * next one, and ``blend`` (0..1) — how far into the transition toward
 * the next cue we are. Rests return ``viseme: null``.
 */
export function sampleVisemes(
  cues: VisemeCue[],
  timeS: number,
): { viseme: Viseme | null; next: Viseme | null; blend: number } {
  if (!cues.length) return { viseme: null, next: null, blend: 0 };
  // Binary search for the cue containing timeS.
  let lo = 0;
  let hi = cues.length - 1;
  if (timeS < cues[0].t) return { viseme: null, next: cues[0].v, blend: 0 };
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (cues[mid].t <= timeS) lo = mid;
    else hi = mid - 1;
  }
  const cue = cues[lo];
  if (timeS >= cue.t + cue.d) return { viseme: null, next: null, blend: 0 };
  const next = lo + 1 < cues.length ? cues[lo + 1].v : null;
  // Start blending toward the next shape over the last 35% of the cue so
  // consecutive vowels glide instead of snapping.
  const frac = (timeS - cue.t) / cue.d;
  const blend = frac > 0.65 ? (frac - 0.65) / 0.35 : 0;
  return { viseme: cue.v, next, blend };
}

/** Rough speaking-time estimate for text without audio (fallback). */
export function estimateDurationS(text: string, syllablesPerSecond = 5.5): number {
  const units = syllabify(text);
  const total = units.reduce((s, u) => s + u.w, 0);
  return Math.max(0.6, total / syllablesPerSecond);
}

/**
 * Duration of a PCM WAV buffer from its header, or ``null`` when the
 * bytes are not a plain RIFF/WAVE file. Lets the timeline be built
 * before the <audio> element reports ``duration`` (and for browsers
 * that report ``Infinity`` for blob URLs until playback ends).
 */
export function wavDurationS(bytes: Uint8Array): number | null {
  if (bytes.byteLength < 44) return null;
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (o: number) => String.fromCharCode(bytes[o], bytes[o + 1], bytes[o + 2], bytes[o + 3]);
  if (tag(0) !== "RIFF" || tag(8) !== "WAVE") return null;
  // Walk the chunks: "fmt " gives the byte rate, "data" the payload size.
  let off = 12;
  let byteRate = 0;
  let dataLen = 0;
  while (off + 8 <= bytes.byteLength) {
    const id = tag(off);
    const size = dv.getUint32(off + 4, true);
    if (id === "fmt " && off + 20 <= bytes.byteLength) {
      byteRate = dv.getUint32(off + 16, true);
    } else if (id === "data") {
      // Streaming encoders write 0 / 0xFFFFFFFF here; fall back to what
      // is actually in the buffer.
      dataLen = size === 0 || size === 0xffffffff ? bytes.byteLength - off - 8 : size;
      break;
    }
    off += 8 + size + (size & 1);
  }
  if (!byteRate || !dataLen) return null;
  return dataLen / byteRate;
}

import { describe, expect, it } from "vitest";

import {
  buildVisemeTimeline,
  estimateDurationS,
  sampleVisemes,
  syllabify,
  wavDurationS,
} from "../visemes";

describe("syllabify", () => {
  it("maps Hangul syllables to their medial vowel", () => {
    const v = syllabify("안녕하세요").map((u) => u.v);
    // 안(ㅏ) 녕(ㅕ) 하(ㅏ) 세(ㅔ) 요(ㅛ)
    expect(v).toEqual(["aa", "oh", "aa", "ee", "oh"]);
  });

  it("treats each Latin vowel cluster as one syllable", () => {
    const v = syllabify("hello beautiful").map((u) => u.v);
    // hel-lo (e, o) | rest | beau-ti-ful (ea→e, i, u)
    expect(v).toEqual(["ee", "oh", null, "ee", "ih", "ou"]);
  });

  it("reads kana rows and folds small kana / long vowels", () => {
    const units = syllabify("きょうは ラーメン");
    expect(units.map((u) => u.v)).toEqual(["oh", "ou", "aa", null, "aa", "ee", "aa"]);
    // ラー stretches the ラ syllable.
    expect(units[4].w).toBeGreaterThan(units[5].w);
  });

  it("turns punctuation into rests and trims the edges", () => {
    const units = syllabify("...가, 나!  ");
    expect(units.map((u) => u.v)).toEqual(["aa", null, "aa"]);
    expect(units[1].w).toBeGreaterThan(0);
  });

  it("gives consonant-only words a short open shape", () => {
    expect(syllabify("hmm").map((u) => u.v)).toEqual(["aa"]);
  });
});

describe("buildVisemeTimeline / sampleVisemes", () => {
  it("spreads syllables over the utterance duration in order", () => {
    const cues = buildVisemeTimeline("가나다", 3);
    expect(cues).toHaveLength(3);
    expect(cues[0].t).toBe(0);
    const end = cues[2].t + cues[2].d;
    expect(end).toBeCloseTo(3 * 0.94, 5);
    expect(sampleVisemes(cues, 0.1).viseme).toBe("aa");
    expect(sampleVisemes(cues, 1.0).viseme).toBe("aa");
  });

  it("samples the right cue, blends near the end, and rests after", () => {
    const cues = buildVisemeTimeline("ho hi", 2, 1);
    // ho | rest | hi
    expect(sampleVisemes(cues, 0.05)).toMatchObject({ viseme: "oh", blend: 0 });
    const late = sampleVisemes(cues, cues[0].d * 0.9);
    expect(late.viseme).toBe("oh");
    expect(late.next).toBeNull(); // next cue is the rest
    expect(late.blend).toBeGreaterThan(0.5);
    expect(sampleVisemes(cues, 1.5).viseme).toBe("ih");
    expect(sampleVisemes(cues, 2.5).viseme).toBeNull();
    expect(sampleVisemes(cues, -1).viseme).toBeNull();
  });

  it("returns nothing for empty text or zero duration", () => {
    expect(buildVisemeTimeline("", 2)).toEqual([]);
    expect(buildVisemeTimeline("가", 0)).toEqual([]);
    expect(sampleVisemes([], 0.5).viseme).toBeNull();
  });

  it("estimates a plausible duration from syllable count", () => {
    expect(estimateDurationS("안녕하세요 여러분")).toBeGreaterThan(1);
    expect(estimateDurationS("a")).toBe(0.6);
  });
});

describe("wavDurationS", () => {
  function wav(seconds: number, rate = 16000, channels = 1, bits = 16, dataSize?: number): Uint8Array {
    const byteRate = (rate * channels * bits) / 8;
    const data = Math.round(seconds * byteRate);
    const buf = new Uint8Array(44 + data);
    const dv = new DataView(buf.buffer);
    const put = (o: number, s: string) => { for (let i = 0; i < 4; i++) buf[o + i] = s.charCodeAt(i); };
    put(0, "RIFF"); dv.setUint32(4, 36 + data, true); put(8, "WAVE");
    put(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
    dv.setUint16(22, channels, true); dv.setUint32(24, rate, true);
    dv.setUint32(28, byteRate, true); dv.setUint16(32, (channels * bits) / 8, true);
    dv.setUint16(34, bits, true);
    put(36, "data"); dv.setUint32(40, dataSize ?? data, true);
    return buf;
  }

  it("reads the duration from fmt byte rate and data size", () => {
    expect(wavDurationS(wav(2.5))).toBeCloseTo(2.5, 3);
    expect(wavDurationS(wav(1, 24000, 2))).toBeCloseTo(1, 3);
  });

  it("falls back to the buffer length for streaming headers", () => {
    expect(wavDurationS(wav(1.5, 16000, 1, 16, 0xffffffff))).toBeCloseTo(1.5, 3);
  });

  it("rejects non-WAV bytes", () => {
    expect(wavDurationS(new Uint8Array(10))).toBeNull();
    expect(wavDurationS(new Uint8Array(64))).toBeNull();
  });
});

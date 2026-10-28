import { describe, expect, it } from "vitest";
import {
  CHAT_MOOD_EMOTE,
  labelToSentiment,
  resolveAddressedAgent,
} from "@/hooks/useChatSentiment";

describe("labelToSentiment", () => {
  it("maps SST-2, 3-way and star-rating labels", () => {
    expect(labelToSentiment("POSITIVE", 0.99)).toBe("positive");
    expect(labelToSentiment("NEGATIVE", 0.9)).toBe("negative");
    expect(labelToSentiment("neutral", 0.9)).toBe("neutral");
    expect(labelToSentiment("5 stars", 0.8)).toBe("positive");
    expect(labelToSentiment("1 star", 0.8)).toBe("negative");
    expect(labelToSentiment("3 stars", 0.8)).toBe("neutral");
  });

  it("treats low-confidence calls as neutral", () => {
    expect(labelToSentiment("POSITIVE", 0.55)).toBe("neutral");
    expect(labelToSentiment("NEGATIVE", Number.NaN)).toBe("neutral");
  });

  it("has an emote for every bucket", () => {
    expect(Object.keys(CHAT_MOOD_EMOTE).sort()).toEqual(["negative", "neutral", "positive"]);
  });
});

describe("resolveAddressedAgent", () => {
  const agents = ["Alex", "Mina", "Director"];

  it("prefers an @mention", () => {
    expect(resolveAddressedAgent("@mina nice one!", agents, "Alex")).toBe("Mina");
  });

  it("matches a name used as a whole word, case-insensitively", () => {
    expect(resolveAddressedAgent("great job alex", agents, null)).toBe("Alex");
    expect(resolveAddressedAgent("alexander?", agents, null)).toBeNull();
  });

  it("falls back to the on-stage agent, but only if it still exists", () => {
    expect(resolveAddressedAgent("lol", agents, "Mina")).toBe("Mina");
    expect(resolveAddressedAgent("lol", agents, "Gone")).toBeNull();
    expect(resolveAddressedAgent("lol", [], "Mina")).toBeNull();
  });
});

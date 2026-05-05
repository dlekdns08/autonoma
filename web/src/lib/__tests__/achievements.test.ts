/**
 * Tests for the Achievements API helpers.
 *
 * Mocks ``global.fetch`` because the real endpoint expects a session
 * cookie and a running backend. We only care about:
 *   - URL construction (path, encoding, query string)
 *   - ``credentials: "include"`` was passed (cookie auth contract)
 *   - HTTP errors surface as Error with the status code
 */
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  fetchAgentAchievements,
  fetchRecentAchievements,
} from "@/lib/achievements";

const mockFetch = vi.fn();

beforeEach(() => {
  globalThis.fetch = mockFetch as unknown as typeof fetch;
});

afterEach(() => {
  mockFetch.mockReset();
});

describe("fetchAgentAchievements", () => {
  it("sends an authenticated GET to the per-agent endpoint", async () => {
    const payload = { character_uuid: "abc", count: 0, items: [] };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => payload,
    });

    const out = await fetchAgentAchievements("abc-123");

    expect(out).toEqual(payload);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/agents/abc-123/achievements");
    expect(init).toMatchObject({ credentials: "include" });
  });

  it("URI-encodes the uuid path segment", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ character_uuid: "x", count: 0, items: [] }),
    });

    await fetchAgentAchievements("a/b c");

    const [url] = mockFetch.mock.calls[0];
    // ``encodeURIComponent`` turns "/" into "%2F" and " " into "%20".
    expect(String(url)).toContain("/api/agents/a%2Fb%20c/achievements");
  });

  it("throws Error with HTTP status on non-2xx", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      json: async () => ({}),
    });

    await expect(fetchAgentAchievements("missing")).rejects.toThrow(/404/);
  });
});

describe("fetchRecentAchievements", () => {
  it("defaults to limit=20 and uses the global feed URL", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => [],
    });

    await fetchRecentAchievements();

    const [url] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/achievements/recent?limit=20");
  });

  it("URL-encodes a custom limit", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => [],
    });

    await fetchRecentAchievements(5);

    const [url] = mockFetch.mock.calls[0];
    expect(String(url)).toContain("/api/achievements/recent?limit=5");
  });

  it("throws on non-2xx with the status in the message", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: async () => ({}),
    });

    await expect(fetchRecentAchievements()).rejects.toThrow(/503/);
  });
});

import { describe, expect, it } from "vitest";
import { GENERATED_CLIP_PREFIX, clipEndpoint } from "@/lib/mocap/clipPlayer";

describe("clipEndpoint", () => {
  it("routes recorded mocap clips to /api/mocap-clips", () => {
    expect(clipEndpoint("abc-123")).toBe("/api/mocap-clips/abc-123");
  });

  it("routes generated gestures to /api/gestures/{id}/clip", () => {
    expect(clipEndpoint(`${GENERATED_CLIP_PREFIX}9f/x`)).toBe("/api/gestures/9f%2Fx/clip");
  });
});

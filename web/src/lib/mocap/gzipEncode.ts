/**
 * Encode a ``MocapClip`` the way the upload endpoint expects:
 *
 *   JSON.stringify → UTF-8 bytes → gzip → base64
 *
 * Uses ``CompressionStream("gzip")`` which every target browser ships
 * (the recorder requires a WebGL-capable browser anyway, so the feature
 * detection path is mostly defensive). Reports the pre-gzip size so the
 * caller can pass it to the server as ``expected_size_bytes`` for the
 * payload cross-check.
 */

import type { MocapClip } from "./clipFormat";

export interface EncodedClip {
  payloadGzB64: string;
  /** Raw JSON size in bytes, pre-gzip. Matches the server's tolerance
   *  check — within 512KiB and at least a couple hundred bytes. */
  rawSizeBytes: number;
  compressedSizeBytes: number;
}

export async function encodeClip(clip: MocapClip): Promise<EncodedClip> {
  const json = JSON.stringify(clip);
  const bytes = new TextEncoder().encode(json);
  const rawSizeBytes = bytes.byteLength;

  const CS = (globalThis as unknown as { CompressionStream?: typeof CompressionStream })
    .CompressionStream;
  if (!CS) {
    throw new Error("CompressionStream not available in this browser");
  }
  const stream = new Blob([new Uint8Array(bytes)])
    .stream()
    .pipeThrough(new CS("gzip"));
  const gz = new Uint8Array(await new Response(stream).arrayBuffer());

  return {
    payloadGzB64: bytesToBase64(gz),
    rawSizeBytes,
    compressedSizeBytes: gz.byteLength,
  };
}

function bytesToBase64(bytes: Uint8Array): string {
  // btoa only accepts binary-string inputs; chunked to stay under the
  // argument-length limit for large clips.
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(
      ...bytes.subarray(i, Math.min(i + chunk, bytes.length)),
    );
  }
  return btoa(binary);
}

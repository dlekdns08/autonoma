/**
 * Centralised client-side tunables for the mocap pipeline. Server-
 * enforced values (payload cap, clip duration cap) live in
 * ``src/autonoma/mocap/validator.py`` and are fetched via
 * ``/api/mocap/triggers``.
 *
 * To tune: change the value here and re-test. Do NOT inline-paste new
 * constants in the consumers — keep this file the only source.
 */

/** Recorded clip framerate. 30 fps matches MediaPipe Tasks Vision's
 *  default frame rate and suits the webcam stream we request. */
export const RECORD_FPS = 30;

/** Fallback clip-duration ceiling used when the server's
 *  ``max_clip_duration_s`` can't be fetched. */
export const DEFAULT_MAX_CLIP_SECONDS = 60;

/** Path (relative to web origin) that serves the MediaPipe WASM
 *  runtime and model bundles. Populated by ``npm run mocap:fetch``. */
export const MEDIAPIPE_WASM_BASE = "/mediapipe";

export const MEDIAPIPE_MODEL_URLS = {
  face: `${MEDIAPIPE_WASM_BASE}/face_landmarker.task`,
  pose: `${MEDIAPIPE_WASM_BASE}/pose_landmarker_full.task`,
  hand: `${MEDIAPIPE_WASM_BASE}/hand_landmarker.task`,
} as const;

/** One-Euro filter defaults — adaptive low-pass for mocap streams.
 *  ``minCutoff`` suppresses static tremor; ``beta`` controls how
 *  quickly the cutoff opens up when the signal moves. Tuned for
 *  webcam-face + landmark-IK body + finger-28-bone output. */
export interface OneEuroConfig {
  minCutoff: number;
  beta: number;
  dCutoff: number;
}

export const ONE_EURO_DEFAULTS: OneEuroConfig = {
  minCutoff: 0.5,
  beta: 0.4,
  dCutoff: 1.0,
};

/** Softer filter preset for body bones — wider-amplitude slower
 *  motion than fingers, so the aggressive minCutoff needed for
 *  anti-finger-jitter over-smooths body motion. This preset keeps
 *  responsiveness high while retaining some anti-jitter. */
export const ONE_EURO_BODY: OneEuroConfig = {
  minCutoff: 1.0,
  beta: 0.7,
  dCutoff: 1.0,
};

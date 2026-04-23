"use client";

/**
 * Real-time skeleton + guide-pose overlay for the mocap webcam panel.
 *
 * Reads MediaPipe pose / hand landmarks from refs (not state) so the
 * 30fps redraw never triggers React's render cycle. The canvas sits
 * absolutely positioned over the ``<video>`` inside ``WebcamPanel``
 * and shares its CSS mirror transform — landmark coords are therefore
 * drawn raw (``x * width``, ``y * height``) and the flip happens at
 * compositor level.
 *
 * Layering:
 *   1. Clear.
 *   2. Guide pose (if present) — translucent white.
 *   3. User pose — green. Each joint whose distance from the guide
 *      exceeds ``matchThreshold`` flips to red, giving the operator a
 *      per-joint "where you're off" readout.
 *   4. Hands, when present — thin green lines, no guide comparison
 *      (gesture overlap is hard to grade at this resolution).
 */

import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import type {
  HandLandmarkerResult,
  NormalizedLandmark,
} from "@mediapipe/tasks-vision";
import {
  HAND_CONNECTIONS,
  HAND_JOINTS,
  MIN_VISIBILITY,
  POSE_CONNECTIONS,
  POSE_JOINTS,
  landmarkDist2,
} from "@/lib/mocap/skeleton";

interface Props {
  /** Live pose landmarks — 33 points in normalized image coords, or
   *  null when the camera hasn't produced a frame. */
  poseLandmarksRef: RefObject<NormalizedLandmark[] | null>;
  /** Live hand-landmarker result (contains up to 2 hands × 21 pts). */
  handLandmarksRef: RefObject<HandLandmarkerResult | null>;
  /** Frozen guide pose — a snapshot of pose landmarks. Null = no guide. */
  referencePose: NormalizedLandmark[] | null;
  /** Distance (normalized 0..1 coords) beyond which a joint is drawn
   *  as "off-pose" (red). ~0.08 ≈ 8% of image width, roughly a fist. */
  matchThreshold?: number;
  /** Mirror the canvas horizontally to match the webcam video's
   *  ``scaleX(-1)`` CSS transform. Default true. */
  mirror?: boolean;
}

const USER_COLOR = "rgba(74, 222, 128, 0.95)"; // emerald-400
const USER_OFF_COLOR = "rgba(248, 113, 113, 0.95)"; // rose-400
const GUIDE_COLOR = "rgba(255, 255, 255, 0.4)";
const JOINT_RADIUS = 5;
const HAND_JOINT_RADIUS = 2.5;
const LINE_WIDTH_USER = 3;
const LINE_WIDTH_GUIDE = 2;
const LINE_WIDTH_HAND = 2;

export default function SkeletonOverlay({
  poseLandmarksRef,
  handLandmarksRef,
  referencePose,
  matchThreshold = 0.08,
  mirror = true,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const threshold2 = matchThreshold * matchThreshold;

    const syncSize = () => {
      const rect = canvas.getBoundingClientRect();
      const w = Math.max(1, Math.round(rect.width));
      const h = Math.max(1, Math.round(rect.height));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
    };

    const tick = () => {
      syncSize();
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      // Guide first so the user skeleton paints over it.
      if (referencePose) {
        drawPose(ctx, referencePose, w, h, GUIDE_COLOR, LINE_WIDTH_GUIDE);
      }

      const pose = poseLandmarksRef.current;
      if (pose) {
        drawPose(
          ctx,
          pose,
          w,
          h,
          USER_COLOR,
          LINE_WIDTH_USER,
          referencePose
            ? { ref: referencePose, threshold2, offColor: USER_OFF_COLOR }
            : undefined,
        );
      }

      const handsResult = handLandmarksRef.current;
      if (handsResult?.landmarks) {
        for (const handLm of handsResult.landmarks) {
          drawHand(ctx, handLm, w, h, USER_COLOR);
        }
      }

      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [poseLandmarksRef, handLandmarksRef, referencePose, matchThreshold]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="pointer-events-none absolute inset-0 h-full w-full"
      style={{ transform: mirror ? "scaleX(-1)" : undefined }}
    />
  );
}

interface DiffConfig {
  ref: NormalizedLandmark[];
  /** Squared threshold — callers pre-square to skip a sqrt per joint. */
  threshold2: number;
  offColor: string;
}

function drawPose(
  ctx: CanvasRenderingContext2D,
  lm: NormalizedLandmark[],
  w: number,
  h: number,
  color: string,
  lineWidth: number,
  diff?: DiffConfig,
): void {
  ctx.lineCap = "round";
  ctx.lineWidth = lineWidth;

  // Edges — colour per edge depends on either endpoint being off.
  for (const [a, b] of POSE_CONNECTIONS) {
    const la = lm[a];
    const lb = lm[b];
    if (!la || !lb) continue;
    if ((la.visibility ?? 1) < MIN_VISIBILITY) continue;
    if ((lb.visibility ?? 1) < MIN_VISIBILITY) continue;
    let strokeColor = color;
    if (diff) {
      const da = landmarkDist2(la, diff.ref[a]);
      const db = landmarkDist2(lb, diff.ref[b]);
      if (da > diff.threshold2 || db > diff.threshold2) {
        strokeColor = diff.offColor;
      }
    }
    ctx.strokeStyle = strokeColor;
    ctx.beginPath();
    ctx.moveTo(la.x * w, la.y * h);
    ctx.lineTo(lb.x * w, lb.y * h);
    ctx.stroke();
  }

  // Joint dots — own colour, independent of edges so a single off-
  // joint at the tip of a chain gets obvious red emphasis.
  for (const idx of POSE_JOINTS) {
    const p = lm[idx];
    if (!p) continue;
    if ((p.visibility ?? 1) < MIN_VISIBILITY) continue;
    let fillColor = color;
    if (diff) {
      if (landmarkDist2(p, diff.ref[idx]) > diff.threshold2) {
        fillColor = diff.offColor;
      }
    }
    ctx.fillStyle = fillColor;
    ctx.beginPath();
    ctx.arc(p.x * w, p.y * h, JOINT_RADIUS, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawHand(
  ctx: CanvasRenderingContext2D,
  lm: NormalizedLandmark[],
  w: number,
  h: number,
  color: string,
): void {
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = LINE_WIDTH_HAND;
  for (const [a, b] of HAND_CONNECTIONS) {
    const la = lm[a];
    const lb = lm[b];
    if (!la || !lb) continue;
    ctx.beginPath();
    ctx.moveTo(la.x * w, la.y * h);
    ctx.lineTo(lb.x * w, lb.y * h);
    ctx.stroke();
  }
  for (const idx of HAND_JOINTS) {
    const p = lm[idx];
    if (!p) continue;
    ctx.beginPath();
    ctx.arc(p.x * w, p.y * h, HAND_JOINT_RADIUS, 0, Math.PI * 2);
    ctx.fill();
  }
}

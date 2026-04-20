"use client";

import { useEffect, useRef } from "react";

interface Props {
  intensity?: number; // 0-1, controls star density & speed
  sky?: string;       // sky line text to determine color theme
}

interface Star {
  x: number;
  y: number;
  size: number;
  speed: number;
  brightness: number;
  twinklePhase: number;
  color: string;
}

const SKY_THEMES: Record<string, { bg1: string; bg2: string; starColors: string[] }> = {
  dawn: {
    bg1: "rgba(30,10,40,1)",
    bg2: "rgba(60,20,50,1)",
    starColors: ["#fcd34d", "#f9a8d4", "#fde68a"],
  },
  morning: {
    bg1: "rgba(15,15,40,1)",
    bg2: "rgba(20,30,60,1)",
    starColors: ["#93c5fd", "#bfdbfe", "#dbeafe"],
  },
  afternoon: {
    bg1: "rgba(10,10,20,0.3)",
    bg2: "rgba(20,20,40,0.3)",
    starColors: ["#fde68a", "#fbbf24", "#f59e0b"],
  },
  evening: {
    bg1: "rgba(20,5,30,1)",
    bg2: "rgba(40,10,50,1)",
    starColors: ["#c084fc", "#e879f9", "#f0abfc"],
  },
  night: {
    bg1: "rgba(5,5,15,1)",
    bg2: "rgba(10,10,25,1)",
    starColors: ["#e2e8f0", "#94a3b8", "#cbd5e1"],
  },
};

function getTheme(sky: string): (typeof SKY_THEMES)["night"] {
  const s = sky.toLowerCase();
  if (s.includes("dawn") || s.includes("sunrise")) return SKY_THEMES.dawn;
  if (s.includes("morning")) return SKY_THEMES.morning;
  if (s.includes("afternoon") || s.includes("noon")) return SKY_THEMES.afternoon;
  if (s.includes("evening") || s.includes("sunset") || s.includes("dusk")) return SKY_THEMES.evening;
  return SKY_THEMES.night;
}

export default function Starfield({ intensity = 0.5, sky = "" }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const starsRef = useRef<Star[]>([]);
  const animRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Cap DPR so 4K displays don't quadruple the fill-rate cost while a
    // fleet of VRM WebGL contexts is already competing for GPU time. The
    // starfield is purely atmospheric — 1.5x is more than enough.
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);

    const resize = () => {
      canvas.width = canvas.offsetWidth * dpr;
      canvas.height = canvas.offsetHeight * dpr;
      // Reset transform first — repeated resizes would compound scale().
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.scale(dpr, dpr);
    };
    resize();
    window.addEventListener("resize", resize);

    // Honour prefers-reduced-motion: render once, then stop animating.
    const reduceMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Density dropped ~40% — we don't need 180 stars to sell the mood,
    // and the old count was a measurable frame-budget hog on laptop GPUs.
    const numStars = Math.floor(35 + intensity * 75);
    const theme = getTheme(sky);

    starsRef.current = Array.from({ length: numStars }, () => ({
      x: Math.random() * canvas.offsetWidth,
      y: Math.random() * canvas.offsetHeight,
      size: Math.random() * 2 + 0.5,
      speed: (Math.random() * 0.3 + 0.05) * (0.5 + intensity),
      brightness: Math.random(),
      twinklePhase: Math.random() * Math.PI * 2,
      color: theme.starColors[Math.floor(Math.random() * theme.starColors.length)],
    }));

    // Target ~30fps — twinkle/drift look identical, half the GPU work.
    const frameInterval = 1000 / 30;
    let lastFrameAt = 0;
    let frame = 0;
    let running = true;

    const draw = () => {
      const w = canvas.offsetWidth;
      const h = canvas.offsetHeight;

      ctx.clearRect(0, 0, w, h);
      frame++;
      const stars = starsRef.current;

      for (const star of stars) {
        star.twinklePhase += 0.02 + Math.random() * 0.01;
        const twinkle = 0.5 + 0.5 * Math.sin(star.twinklePhase);
        const alpha = star.brightness * twinkle * (0.4 + intensity * 0.6);

        star.y += star.speed;
        star.x += Math.sin(frame * 0.005 + star.twinklePhase) * 0.1;

        if (star.y > h) {
          star.y = 0;
          star.x = Math.random() * w;
        }
        if (star.x < 0) star.x = w;
        if (star.x > w) star.x = 0;

        ctx.beginPath();
        ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
        ctx.fillStyle = star.color;
        ctx.globalAlpha = alpha;
        ctx.fill();

        if (star.size > 1.5) {
          ctx.beginPath();
          ctx.arc(star.x, star.y, star.size * 3, 0, Math.PI * 2);
          ctx.fillStyle = star.color;
          ctx.globalAlpha = alpha * 0.15;
          ctx.fill();
        }
      }

      if (Math.random() < 0.003 * intensity) {
        const sx = Math.random() * w;
        const sy = Math.random() * h * 0.5;
        ctx.beginPath();
        ctx.moveTo(sx, sy);
        ctx.lineTo(sx + 40 + Math.random() * 60, sy + 20 + Math.random() * 30);
        ctx.strokeStyle = "rgba(255,255,255,0.6)";
        ctx.lineWidth = 1;
        ctx.globalAlpha = 0.8;
        ctx.stroke();
      }

      ctx.globalAlpha = 1;
    };

    const tick = (now: number) => {
      if (!running) return;
      if (now - lastFrameAt >= frameInterval) {
        lastFrameAt = now;
        draw();
      }
      animRef.current = requestAnimationFrame(tick);
    };

    // Pause while the tab is hidden so we don't keep burning battery.
    const onVisibility = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(animRef.current);
      } else if (!reduceMotion) {
        running = true;
        lastFrameAt = 0;
        animRef.current = requestAnimationFrame(tick);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    if (reduceMotion) {
      draw(); // single static frame
    } else {
      animRef.current = requestAnimationFrame(tick);
    }

    return () => {
      running = false;
      cancelAnimationFrame(animRef.current);
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intensity, sky]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full pointer-events-none"
      style={{ opacity: 0.7 }}
    />
  );
}

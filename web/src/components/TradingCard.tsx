"use client";

/**
 * Phase 1-#6b — shareable trading-card PNG export for a persisted
 * character. We render onto a 1200×630 ``<canvas>`` (Open Graph image
 * aspect) entirely client-side: no server round-trip, no extra fonts to
 * ship. The output is downloaded as a PNG with the character's name in
 * the filename so screenshots feel intentional, not haphazard.
 */

import { useCallback, useEffect, useMemo, useRef } from "react";
import type { AgentProfile, AgentProfileCharacter } from "@/hooks/useAgentProfile";

const CARD_WIDTH = 1200;
const CARD_HEIGHT = 630;

const RARITY_PALETTE: Record<
  string,
  { from: string; to: string; ring: string; label: string }
> = {
  legendary: {
    from: "#fbbf24",
    to: "#f43f5e",
    ring: "rgba(253, 224, 71, 0.7)",
    label: "LEGENDARY",
  },
  rare: {
    from: "#22d3ee",
    to: "#a855f7",
    ring: "rgba(125, 211, 252, 0.6)",
    label: "RARE",
  },
  uncommon: {
    from: "#34d399",
    to: "#14b8a6",
    ring: "rgba(110, 231, 183, 0.5)",
    label: "UNCOMMON",
  },
  common: {
    from: "#94a3b8",
    to: "#475569",
    ring: "rgba(148, 163, 184, 0.4)",
    label: "COMMON",
  },
};

function pickRarity(rarity: string) {
  return RARITY_PALETTE[rarity] ?? RARITY_PALETTE.common;
}

/** Pick the most "interesting" diary line we can show on a card.
 *  Priority: lore → diary → memory → note. Within each kind, prefer
 *  the most recent entry. */
function featuredJournalLine(journal: AgentProfile["journal"]): string {
  if (!journal.length) return "";
  const order = ["lore", "diary", "memory", "note"];
  for (const kind of order) {
    const hit = [...journal].reverse().find((e) => e.kind === kind && e.text.trim());
    if (hit) return hit.text.trim();
  }
  return journal[journal.length - 1]?.text?.trim() ?? "";
}

function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  maxLines: number,
): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (ctx.measureText(candidate).width > maxWidth && current) {
      lines.push(current);
      current = word;
      if (lines.length >= maxLines - 1) break;
    } else {
      current = candidate;
    }
  }
  if (current && lines.length < maxLines) lines.push(current);
  // If we ran out of room, ellipsize the last visible line.
  if (lines.length === maxLines) {
    let last = lines[maxLines - 1];
    while (ctx.measureText(`${last}…`).width > maxWidth && last.length > 4) {
      last = last.slice(0, -1);
    }
    lines[maxLines - 1] = `${last}…`;
  }
  return lines;
}

function drawStarfield(ctx: CanvasRenderingContext2D, seed: number) {
  // Deterministic pseudo-random so re-renders look identical (mulberry32).
  let s = seed | 0;
  const rand = () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  ctx.save();
  for (let i = 0; i < 220; i++) {
    const x = rand() * CARD_WIDTH;
    const y = rand() * CARD_HEIGHT;
    const r = rand() * 1.6 + 0.2;
    ctx.fillStyle = `rgba(255,255,255,${0.25 + rand() * 0.55})`;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}

function nameSeed(name: string): number {
  let h = 5381;
  for (let i = 0; i < name.length; i++) h = ((h << 5) + h) ^ name.charCodeAt(i);
  return h >>> 0;
}

function renderTradingCard(
  canvas: HTMLCanvasElement,
  profile: AgentProfile,
): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = CARD_WIDTH * dpr;
  canvas.height = CARD_HEIGHT * dpr;
  canvas.style.width = `${CARD_WIDTH}px`;
  canvas.style.height = `${CARD_HEIGHT}px`;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const { character, runs } = profile;
  const palette = pickRarity(character.rarity);

  // ── Background gradient + starfield ─────────────────────────────
  const bg = ctx.createLinearGradient(0, 0, CARD_WIDTH, CARD_HEIGHT);
  bg.addColorStop(0, "#050618");
  bg.addColorStop(1, "#0a0a12");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, CARD_WIDTH, CARD_HEIGHT);
  drawStarfield(ctx, nameSeed(character.name));

  // Soft rarity glow behind the emoji
  const glow = ctx.createRadialGradient(220, 220, 0, 220, 220, 380);
  glow.addColorStop(0, palette.ring);
  glow.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = glow;
  ctx.fillRect(0, 0, CARD_WIDTH, CARD_HEIGHT);

  // ── Emoji medallion ─────────────────────────────────────────────
  const medallionX = 220;
  const medallionY = 220;
  const medGrad = ctx.createLinearGradient(
    medallionX - 130,
    medallionY - 130,
    medallionX + 130,
    medallionY + 130,
  );
  medGrad.addColorStop(0, palette.from);
  medGrad.addColorStop(1, palette.to);
  ctx.fillStyle = medGrad;
  ctx.beginPath();
  ctx.arc(medallionX, medallionY, 120, 0, Math.PI * 2);
  ctx.fill();
  ctx.lineWidth = 6;
  ctx.strokeStyle = "rgba(255,255,255,0.35)";
  ctx.stroke();

  ctx.font = "180px serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#fff";
  ctx.fillText(character.species_emoji || "❔", medallionX, medallionY + 8);

  // ── Rarity ribbon ───────────────────────────────────────────────
  const ribbonY = 80;
  const ribbonText = `${palette.label} · Lv ${character.level}`;
  ctx.font =
    "700 24px ui-monospace, SFMono-Regular, Menlo, monospace";
  const ribbonW = ctx.measureText(ribbonText).width + 56;
  const ribbonX = CARD_WIDTH - ribbonW - 60;
  const ribbonGrad = ctx.createLinearGradient(ribbonX, 0, ribbonX + ribbonW, 0);
  ribbonGrad.addColorStop(0, palette.from);
  ribbonGrad.addColorStop(1, palette.to);
  ctx.fillStyle = ribbonGrad;
  ctx.beginPath();
  // Manual rounded-rect — Path2D's roundRect has spotty Safari support.
  const ribbonH = 44;
  const r = 22;
  ctx.moveTo(ribbonX + r, ribbonY);
  ctx.lineTo(ribbonX + ribbonW - r, ribbonY);
  ctx.quadraticCurveTo(ribbonX + ribbonW, ribbonY, ribbonX + ribbonW, ribbonY + r);
  ctx.lineTo(ribbonX + ribbonW, ribbonY + ribbonH - r);
  ctx.quadraticCurveTo(
    ribbonX + ribbonW,
    ribbonY + ribbonH,
    ribbonX + ribbonW - r,
    ribbonY + ribbonH,
  );
  ctx.lineTo(ribbonX + r, ribbonY + ribbonH);
  ctx.quadraticCurveTo(ribbonX, ribbonY + ribbonH, ribbonX, ribbonY + ribbonH - r);
  ctx.lineTo(ribbonX, ribbonY + r);
  ctx.quadraticCurveTo(ribbonX, ribbonY, ribbonX + r, ribbonY);
  ctx.fill();
  ctx.fillStyle = "rgba(15,23,42,0.95)";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(ribbonText, ribbonX + ribbonW / 2, ribbonY + ribbonH / 2 + 1);

  // ── Name + role line ────────────────────────────────────────────
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.font =
    "800 64px ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif";
  const nameGrad = ctx.createLinearGradient(380, 160, 1100, 220);
  nameGrad.addColorStop(0, "#f0abfc");
  nameGrad.addColorStop(1, "#67e8f9");
  ctx.fillStyle = nameGrad;
  ctx.fillText(character.name, 380, 200);

  ctx.font = "500 22px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.fillStyle = "rgba(255,255,255,0.65)";
  ctx.fillText(
    `${character.role || "agent"} · ${character.species || "??"}`,
    380,
    240,
  );

  if (character.catchphrase) {
    ctx.font = "italic 24px Georgia, 'Times New Roman', serif";
    ctx.fillStyle = "rgba(255,255,255,0.78)";
    const lines = wrapText(ctx, `"${character.catchphrase}"`, 720, 2);
    lines.forEach((line, i) => ctx.fillText(line, 380, 282 + i * 32));
  }

  // ── Counters strip ──────────────────────────────────────────────
  const counters: { label: string; value: string }[] = [
    { label: "RUNS", value: String(runs) },
    { label: "XP", value: character.total_xp.toLocaleString() },
    { label: "TASKS", value: String(character.tasks_completed) },
    { label: "FILES", value: String(character.files_created) },
  ];
  const counterY = 380;
  counters.forEach((c, idx) => {
    const x = 380 + idx * 175;
    ctx.fillStyle = "rgba(15,23,42,0.55)";
    ctx.fillRect(x, counterY, 155, 80);
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1;
    ctx.strokeRect(x, counterY, 155, 80);

    ctx.font =
      "700 30px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillStyle = "#fff";
    ctx.fillText(c.value, x + 16, counterY + 40);

    ctx.font =
      "500 12px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillStyle = "rgba(255,255,255,0.45)";
    ctx.fillText(c.label, x + 16, counterY + 62);
  });

  // ── Featured journal entry ─────────────────────────────────────
  const featured = featuredJournalLine(profile.journal);
  if (featured) {
    ctx.font = "700 14px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.fillStyle = "rgba(255,255,255,0.45)";
    ctx.fillText("MOST QUOTABLE", 60, 510);

    ctx.font =
      "500 22px ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif";
    ctx.fillStyle = "rgba(255,255,255,0.92)";
    const wrapped = wrapText(ctx, `"${featured}"`, CARD_WIDTH - 120, 3);
    wrapped.forEach((line, i) => ctx.fillText(line, 60, 540 + i * 28));
  }

  // ── Footer ──────────────────────────────────────────────────────
  ctx.font = "500 13px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.fillStyle = "rgba(255,255,255,0.35)";
  ctx.textAlign = "right";
  ctx.fillText("autonoma · self-organizing agent swarm", CARD_WIDTH - 60, 600);
  ctx.textAlign = "left";
  ctx.fillText(`uuid:${character.uuid.slice(0, 8)}`, 60, 600);
}

export interface TradingCardProps {
  profile: AgentProfile;
}

/**
 * Visible thumbnail (downscaled by CSS) + download button. Render this
 * inside the agent profile page; it self-paints whenever ``profile``
 * changes. Returns null if the profile is missing required fields.
 */
export default function TradingCard({ profile }: TradingCardProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const character: AgentProfileCharacter = profile.character;

  const filename = useMemo(() => {
    const slug = character.name
      .normalize("NFKD")
      .replace(/[^\w가-힣-]+/g, "_")
      .replace(/^_+|_+$/g, "");
    return `${slug || "agent"}-card.png`;
  }, [character.name]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    renderTradingCard(canvas, profile);
  }, [profile]);

  const handleDownload = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }, "image/png");
  }, [filename]);

  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-hidden rounded-2xl border border-white/10 bg-black/40 shadow-xl">
        <canvas
          ref={canvasRef}
          // CSS sizing — actual draw resolution is set in renderTradingCard.
          style={{ width: "100%", height: "auto", display: "block" }}
          aria-label={`${character.name} trading card preview`}
        />
      </div>
      <div className="flex items-center justify-between font-mono text-xs text-white/50">
        <span>1200×630 · OG-image ratio · 공유에 최적화</span>
        <button
          type="button"
          onClick={handleDownload}
          className="rounded-xl border border-fuchsia-400/40 bg-fuchsia-500/10 px-4 py-2 font-mono text-xs text-fuchsia-200 transition hover:bg-fuchsia-500/20"
        >
          PNG 다운로드
        </button>
      </div>
    </div>
  );
}

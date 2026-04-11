"use client";

import { useEffect, useRef, useState } from "react";
import type { AgentData } from "@/lib/types";
import type { MapLayout } from "./pixel/mapData";
import { isWalkable } from "./pixel/mapData";
import { STAGE } from "./pixel/types";

export interface MotionState {
  name: string;
  x: number; // percent of stage width
  y: number; // percent of stage height (feet anchor)
  facingLeft: boolean;
  walkPhase: number;
  isMoving: boolean;
  jumpOffset: number;
}

export interface DialogueBubble {
  /** which agent is currently speaking */
  speaker: string;
  /** partner they are talking to */
  partner: string;
  text: string;
  /** absolute timestamp when the bubble should disappear */
  expiresAt: number;
}

interface Options {
  agents: AgentData[];
  /** Pre-built map layout with collision grid. */
  map: MapLayout;
}

const SPEED_IDLE = 0.24;
const SPEED_WALK = 0.42;
const SPEED_RUN = 0.72;
const IDLE_PAUSE_MIN = 1200;
const IDLE_PAUSE_MAX = 3200;
const WANDER_RANGE = 28;
const INTERACT_DISTANCE = 7;
const DIALOGUE_LINE_MS = 2400;
const DIALOGUE_COOLDOWN_MS = 6000;

// Percent-space bounds. The ground area in the canvas runs from roughly
// row 6 (horizon) to row 12, i.e. 50%..100% of the stage height. We keep
// characters inside a slightly tighter band so their heads can poke a
// little above the horizon without feet leaving the map.
const MIN_X = 4;
const MAX_X = 96;
const MIN_Y = 56;
const MAX_Y = 96;

interface MotionInternal extends MotionState {
  targetX: number;
  targetY: number;
  nextActionAt: number;
  homeX: number;
  homeY: number;
  /** name of the agent we are currently locked into a dialogue with */
  dialogueWith: string | null;
  /** earliest time we are willing to start another dialogue */
  dialogueCooldownUntil: number;
}

function rand(min: number, max: number) {
  return min + Math.random() * (max - min);
}

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}

function pctToPx(xPct: number, yPct: number): [number, number] {
  return [
    Math.round((xPct / 100) * STAGE.width),
    Math.round((yPct / 100) * STAGE.height),
  ];
}

function canStand(map: MapLayout, xPct: number, yPct: number): boolean {
  const [px, py] = pctToPx(xPct, yPct);
  return isWalkable(map, px, py);
}

/** Find a walkable point near (homeX, homeY) using a random walk fallback. */
function pickWalkableTarget(
  map: MapLayout,
  homeX: number,
  homeY: number,
): [number, number] {
  for (let tries = 0; tries < 8; tries++) {
    const angle = rand(0, Math.PI * 2);
    const dist = rand(6, WANDER_RANGE);
    const tx = clamp(homeX + Math.cos(angle) * dist, MIN_X, MAX_X);
    const ty = clamp(homeY + Math.sin(angle) * dist * 0.55, MIN_Y, MAX_Y);
    if (canStand(map, tx, ty)) return [tx, ty];
  }
  // fall back to home itself if even that fails we just stay put
  if (canStand(map, homeX, homeY)) return [homeX, homeY];
  // scan outward for any walkable pixel as a last resort
  for (let r = 0; r < 40; r++) {
    for (let a = 0; a < 8; a++) {
      const ang = (a * Math.PI) / 4;
      const tx = clamp(homeX + Math.cos(ang) * r, MIN_X, MAX_X);
      const ty = clamp(homeY + Math.sin(ang) * r * 0.55, MIN_Y, MAX_Y);
      if (canStand(map, tx, ty)) return [tx, ty];
    }
  }
  return [homeX, homeY];
}

/** Deterministic home position spread across the walkable ground. */
function pickHome(
  map: MapLayout,
  idx: number,
  total: number,
): [number, number] {
  const spread = total > 1 ? 80 / total : 0;
  const baseX = 10 + idx * spread;
  const baseY = 72 + ((idx % 3) - 1) * 6;
  const hx = clamp(baseX, MIN_X + 2, MAX_X - 2);
  const hy = clamp(baseY, MIN_Y + 2, MAX_Y - 2);
  if (canStand(map, hx, hy)) return [hx, hy];
  return pickWalkableTarget(map, hx, hy);
}

// ── dialogue content ─────────────────────────────────────────────────────
const DIALOGUE_LINES: Record<string, string[]> = {
  greeting: [
    "yo!",
    "hey there!",
    "hi :)",
    "oh, hey",
    "sup",
  ],
  reply: [
    "what's up?",
    "how's it going?",
    "you good?",
    "need a hand?",
    "same here",
  ],
  work: [
    "refactoring this...",
    "just shipped it",
    "tests are green",
    "checking the logs",
    "let's pair on this",
  ],
  play: [
    "coffee?",
    "lunch soon?",
    "ugh, bugs",
    "that's clean",
    "nice work!",
  ],
};

function pickLine(bank: string[], seed: number): string {
  return bank[seed % bank.length];
}

function buildDialogue(aName: string, bName: string, seed: number): string[] {
  return [
    pickLine(DIALOGUE_LINES.greeting, seed),
    pickLine(DIALOGUE_LINES.reply, seed * 3 + 1),
    pickLine(DIALOGUE_LINES.work, seed * 5 + 2),
    pickLine(DIALOGUE_LINES.play, seed * 7 + 3),
  ];
}

let dialogueSeedCounter = 0;

interface MotionResult {
  motions: Record<string, MotionState>;
  bubbles: DialogueBubble[];
}

export function useAgentMotion({ agents, map }: Options): MotionResult {
  const [tick, setTick] = useState(0);
  const internalRef = useRef<Map<string, MotionInternal>>(new Map());
  const bubblesRef = useRef<DialogueBubble[]>([]);
  const pendingLinesRef = useRef<
    Map<string, { lines: string[]; partner: string; nextAt: number }>
  >(new Map());
  const lastFrameRef = useRef<number>(0);

  // Seed / re-seed character motion state when the agent list or map changes.
  useEffect(() => {
    const now = performance.now();
    lastFrameRef.current = now;
    const next = new Map<string, MotionInternal>();
    const existing = internalRef.current;

    agents.forEach((agent, idx) => {
      const prior = existing.get(agent.name);
      if (prior) {
        next.set(agent.name, prior);
        return;
      }
      const [homeX, homeY] = pickHome(map, idx, agents.length);
      next.set(agent.name, {
        name: agent.name,
        x: homeX,
        y: homeY,
        facingLeft: Math.random() < 0.5,
        walkPhase: 0,
        isMoving: false,
        jumpOffset: 0,
        targetX: homeX,
        targetY: homeY,
        nextActionAt: now + rand(IDLE_PAUSE_MIN, IDLE_PAUSE_MAX),
        homeX,
        homeY,
        dialogueWith: null,
        dialogueCooldownUntil: 0,
      });
    });
    internalRef.current = next;
    // drop bubbles for agents that have left
    bubblesRef.current = bubblesRef.current.filter(
      (b) => next.has(b.speaker) && next.has(b.partner),
    );
    pendingLinesRef.current.forEach((_, key) => {
      const [a, b] = key.split("→");
      if (!next.has(a) || !next.has(b)) pendingLinesRef.current.delete(key);
    });
  }, [agents, map]);

  // Animation loop.
  useEffect(() => {
    let raf = 0;

    const tryStartDialogue = (
      m: MotionInternal,
      partner: MotionInternal,
      now: number,
    ) => {
      if (m.dialogueWith || partner.dialogueWith) return;
      if (now < m.dialogueCooldownUntil || now < partner.dialogueCooldownUntil)
        return;
      const dx = m.x - partner.x;
      const dy = m.y - partner.y;
      if (Math.hypot(dx, dy) > INTERACT_DISTANCE) return;
      m.dialogueWith = partner.name;
      partner.dialogueWith = m.name;
      const seed = ++dialogueSeedCounter;
      const lines = buildDialogue(m.name, partner.name, seed);
      const key = `${m.name}→${partner.name}`;
      pendingLinesRef.current.set(key, {
        lines,
        partner: partner.name,
        nextAt: now,
      });
      // face each other
      m.facingLeft = partner.x < m.x;
      partner.facingLeft = m.x < partner.x;
    };

    const endDialogue = (m: MotionInternal, now: number) => {
      const partnerName = m.dialogueWith;
      if (!partnerName) return;
      const partner = internalRef.current.get(partnerName);
      if (partner) {
        partner.dialogueWith = null;
        partner.dialogueCooldownUntil = now + DIALOGUE_COOLDOWN_MS;
      }
      m.dialogueWith = null;
      m.dialogueCooldownUntil = now + DIALOGUE_COOLDOWN_MS;
      pendingLinesRef.current.delete(`${m.name}→${partnerName}`);
    };

    const step = (dt: number, now: number) => {
      const motions = internalRef.current;
      const agentMap = new Map(agents.map((a) => [a.name, a]));

      // drive dialogue bubble queues
      pendingLinesRef.current.forEach((queue, key) => {
        if (now < queue.nextAt) return;
        const [speakerName] = key.split("→");
        const speaker = motions.get(speakerName);
        const partner = motions.get(queue.partner);
        if (!speaker || !partner) {
          pendingLinesRef.current.delete(key);
          return;
        }
        if (queue.lines.length === 0) {
          endDialogue(speaker, now);
          return;
        }
        const line = queue.lines.shift()!;
        bubblesRef.current.push({
          speaker: speakerName,
          partner: queue.partner,
          text: line,
          expiresAt: now + DIALOGUE_LINE_MS,
        });
        // swap which side speaks next by re-keying under the partner
        const reverseKey = `${queue.partner}→${speakerName}`;
        pendingLinesRef.current.delete(key);
        if (queue.lines.length > 0) {
          pendingLinesRef.current.set(reverseKey, {
            lines: queue.lines,
            partner: speakerName,
            nextAt: now + DIALOGUE_LINE_MS,
          });
        } else {
          // last line just played; schedule cleanup after it fades
          setTimeout(() => {
            const s = internalRef.current.get(speakerName);
            if (s) endDialogue(s, performance.now());
          }, DIALOGUE_LINE_MS + 50);
        }
      });

      // expire old bubbles
      bubblesRef.current = bubblesRef.current.filter((b) => b.expiresAt > now);

      motions.forEach((m) => {
        const agent = agentMap.get(m.name);
        if (!agent) return;
        const state = agent.state || "idle";
        const inDialogue = m.dialogueWith !== null;

        // Dialogue hold — stand still and look at partner.
        if (inDialogue) {
          const partner = motions.get(m.dialogueWith!);
          if (partner) {
            m.facingLeft = partner.x < m.x;
          }
          m.isMoving = false;
          m.jumpOffset *= 0.85;
          return;
        }

        // Pick a new action when the current one expires.
        if (now >= m.nextActionAt) {
          if (state === "talking" || state === "thinking") {
            // approach the nearest available partner
            const partner = findInteractionPartner(agent, agents, motions);
            if (partner) {
              const pm = motions.get(partner.name);
              if (pm) {
                const offset = pm.x > m.x ? -INTERACT_DISTANCE : INTERACT_DISTANCE;
                const tx = clamp(pm.x + offset, MIN_X, MAX_X);
                const ty = clamp(pm.y, MIN_Y, MAX_Y);
                if (canStand(map, tx, ty)) {
                  m.targetX = tx;
                  m.targetY = ty;
                } else {
                  [m.targetX, m.targetY] = pickWalkableTarget(
                    map,
                    m.homeX,
                    m.homeY,
                  );
                }
              }
            } else {
              [m.targetX, m.targetY] = pickWalkableTarget(
                map,
                m.homeX,
                m.homeY,
              );
            }
            m.nextActionAt = now + rand(1500, 3500);
          } else if (state === "celebrating") {
            [m.targetX, m.targetY] = pickWalkableTarget(map, m.x, m.y);
            m.nextActionAt = now + rand(400, 900);
          } else if (state === "working") {
            [m.targetX, m.targetY] = pickWalkableTarget(
              map,
              m.homeX,
              m.homeY,
            );
            m.nextActionAt = now + rand(2500, 5000);
          } else {
            [m.targetX, m.targetY] = pickWalkableTarget(
              map,
              m.homeX,
              m.homeY,
            );
            m.nextActionAt = now + rand(IDLE_PAUSE_MIN, IDLE_PAUSE_MAX);
          }
        }

        const dx = m.targetX - m.x;
        const dy = m.targetY - m.y;
        const dist = Math.hypot(dx, dy);
        const speed =
          state === "celebrating"
            ? SPEED_RUN
            : state === "talking"
              ? SPEED_WALK
              : SPEED_IDLE * 2;
        const stepSize = (speed * dt) / 16;

        if (dist > 0.4) {
          const invDist = 1 / dist;
          const dirX = dx * invDist;
          const dirY = dy * invDist;
          const advance = Math.min(stepSize, dist);
          const nx = clamp(m.x + dirX * advance, MIN_X, MAX_X);
          const ny = clamp(m.y + dirY * advance, MIN_Y, MAX_Y);

          // Collision: try full step, then x-only, then y-only.
          if (canStand(map, nx, ny)) {
            m.x = nx;
            m.y = ny;
          } else if (canStand(map, nx, m.y)) {
            m.x = nx;
          } else if (canStand(map, m.x, ny)) {
            m.y = ny;
          } else {
            // stuck — pick a new target next frame
            m.nextActionAt = 0;
          }

          if (Math.abs(dx) > 0.5) {
            m.facingLeft = dirX < 0;
          }
          m.isMoving = true;
          m.walkPhase = (m.walkPhase + stepSize * 0.08) % 1;
        } else {
          m.isMoving = false;
        }

        if (state === "celebrating") {
          m.jumpOffset = Math.abs(Math.sin(now / 160)) * 6;
        } else if (state === "working") {
          m.jumpOffset = Math.sin(now / 400) * 0.6;
        } else {
          m.jumpOffset *= 0.85;
          if (Math.abs(m.jumpOffset) < 0.05) m.jumpOffset = 0;
        }
      });

      // After motion: try to kick off dialogues between nearby idle pairs.
      const list = Array.from(motions.values());
      for (let i = 0; i < list.length; i++) {
        const a = list[i];
        if (a.dialogueWith) continue;
        if (a.isMoving) continue;
        for (let j = i + 1; j < list.length; j++) {
          const b = list[j];
          if (b.dialogueWith) continue;
          if (b.isMoving) continue;
          tryStartDialogue(a, b, now);
          if (a.dialogueWith) break;
        }
      }
    };

    const loop = (t: number) => {
      const dt = Math.min(64, t - lastFrameRef.current);
      lastFrameRef.current = t;
      step(dt, t);
      setTick((n) => (n + 1) % 1_000_000);
      raf = requestAnimationFrame(loop);
    };

    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [agents, map]);

  const snapshot: Record<string, MotionState> = {};
  internalRef.current.forEach((m, name) => {
    snapshot[name] = {
      name,
      x: m.x,
      y: m.y,
      facingLeft: m.facingLeft,
      walkPhase: m.walkPhase,
      isMoving: m.isMoving,
      jumpOffset: m.jumpOffset,
    };
  });
  void tick;
  return { motions: snapshot, bubbles: bubblesRef.current.slice() };
}

function findInteractionPartner(
  self: AgentData,
  all: AgentData[],
  motions: Map<string, MotionInternal>,
): AgentData | null {
  const selfMotion = motions.get(self.name);
  if (!selfMotion) return null;
  let closest: AgentData | null = null;
  let bestDist = Infinity;
  for (const other of all) {
    if (other.name === self.name) continue;
    if (
      other.state !== "talking" &&
      other.state !== "thinking" &&
      other.state !== "idle"
    )
      continue;
    const om = motions.get(other.name);
    if (!om) continue;
    const d = Math.hypot(om.x - selfMotion.x, om.y - selfMotion.y);
    if (d < bestDist) {
      bestDist = d;
      closest = other;
    }
  }
  return closest;
}

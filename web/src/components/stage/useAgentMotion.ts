"use client";

import { useEffect, useRef, useState } from "react";
import type { AgentData } from "@/lib/types";

export interface MotionState {
  name: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  facingLeft: boolean;
  walkPhase: number;
  isMoving: boolean;
  jumpOffset: number;
  lastX: number;
}

interface Options {
  agents: AgentData[];
  /** Mid-ground y percent used as the home row for new agents. */
  groundYPercent: number;
  stageWidth?: number;
  stageHeight?: number;
}

const SPEED_IDLE = 0.12;
const SPEED_WALK = 0.35;
const SPEED_RUN = 0.6;
const IDLE_PAUSE_MIN = 1200;
const IDLE_PAUSE_MAX = 3200;
const WANDER_RANGE_MIN = 8;
const WANDER_RANGE_MAX = 35;
const VERTICAL_WANDER_RANGE_MIN = 4;
const VERTICAL_WANDER_RANGE_MAX = 14;
const INTERACT_DISTANCE = 6;
const MIN_X = 6;
const MAX_X = 94;
// Ground band (percent of stage height) in which characters can wander.
// The horizon sits at ~50%, we keep feet inside the grass area.
const MIN_Y = 62;
const MAX_Y = 96;

interface MotionInternal extends MotionState {
  targetX: number;
  targetY: number;
  nextActionAt: number;
  homeX: number;
  homeY: number;
}

function rand(min: number, max: number) {
  return min + Math.random() * (max - min);
}

export function useAgentMotion({ agents, groundYPercent }: Options): Record<string, MotionState> {
  const [tick, setTick] = useState(0);
  const internalRef = useRef<Map<string, MotionInternal>>(new Map());
  const lastFrameRef = useRef<number>(0);

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
      const spread = agents.length > 1 ? 80 / agents.length : 0;
      const homeX = Math.max(MIN_X + 4, Math.min(MAX_X - 4, 12 + idx * spread + rand(-4, 4)));
      const homeY = clamp(groundYPercent + rand(-8, 4), MIN_Y + 2, MAX_Y - 2);
      next.set(agent.name, {
        name: agent.name,
        x: homeX,
        y: homeY,
        vx: 0,
        vy: 0,
        facingLeft: Math.random() < 0.5,
        walkPhase: 0,
        isMoving: false,
        jumpOffset: 0,
        lastX: homeX,
        targetX: homeX,
        targetY: homeY,
        nextActionAt: now + rand(IDLE_PAUSE_MIN, IDLE_PAUSE_MAX),
        homeX,
        homeY,
      });
    });
    internalRef.current = next;
  }, [agents, groundYPercent]);

  useEffect(() => {
    let raf = 0;
    const loop = (t: number) => {
      const dt = Math.min(64, t - lastFrameRef.current);
      lastFrameRef.current = t;
      step(dt, t);
      setTick((n) => (n + 1) % 1_000_000);
      raf = requestAnimationFrame(loop);
    };

    const step = (dt: number, now: number) => {
      const motions = internalRef.current;
      const agentMap = new Map(agents.map((a) => [a.name, a]));

      motions.forEach((m) => {
        const agent = agentMap.get(m.name);
        if (!agent) return;
        const state = agent.state || "idle";

        if (now >= m.nextActionAt) {
          if (state === "talking" || state === "thinking") {
            const partner = findInteractionPartner(agent, agents, motions);
            if (partner) {
              const target = motions.get(partner.name);
              if (target) {
                const offset = target.x > m.x ? -INTERACT_DISTANCE : INTERACT_DISTANCE;
                m.targetX = clamp(target.x + offset, MIN_X, MAX_X);
                m.targetY = clamp(target.y, MIN_Y, MAX_Y);
              }
            } else {
              [m.targetX, m.targetY] = pickWanderTarget(m.homeX, m.homeY);
            }
            m.nextActionAt = now + rand(1500, 3500);
          } else if (state === "celebrating") {
            m.targetX = clamp(m.x + rand(-12, 12), MIN_X, MAX_X);
            m.targetY = clamp(m.y + rand(-6, 6), MIN_Y, MAX_Y);
            m.nextActionAt = now + rand(400, 900);
          } else if (state === "working") {
            m.targetX = clamp(m.homeX + rand(-3, 3), MIN_X, MAX_X);
            m.targetY = clamp(m.homeY + rand(-2, 2), MIN_Y, MAX_Y);
            m.nextActionAt = now + rand(2500, 5000);
          } else {
            [m.targetX, m.targetY] = pickWanderTarget(m.homeX, m.homeY);
            m.nextActionAt = now + rand(IDLE_PAUSE_MIN, IDLE_PAUSE_MAX);
          }
        }

        const dx = m.targetX - m.x;
        const dy = m.targetY - m.y;
        const dist = Math.hypot(dx, dy);
        const speed =
          state === "celebrating" ? SPEED_RUN : state === "talking" ? SPEED_WALK : SPEED_IDLE * 2;
        const stepSize = (speed * dt) / 16;

        if (dist > 0.4) {
          const invDist = 1 / dist;
          const dirX = dx * invDist;
          const dirY = dy * invDist;
          const advance = Math.min(stepSize, dist);
          m.x = clamp(m.x + dirX * advance, MIN_X, MAX_X);
          m.y = clamp(m.y + dirY * advance, MIN_Y, MAX_Y);
          // Only flip facing when horizontal motion dominates; otherwise
          // keep the last facing so the character doesn't whip around
          // during vertical-only strides.
          if (Math.abs(dx) > 0.5) {
            m.facingLeft = dirX < 0;
          }
          m.isMoving = true;
          m.walkPhase = (m.walkPhase + stepSize * 0.08) % 1;
        } else {
          m.isMoving = false;
        }

        if (state === "celebrating") {
          const bounce = Math.abs(Math.sin(now / 160));
          m.jumpOffset = bounce * 6;
        } else if (state === "working") {
          m.jumpOffset = Math.sin(now / 400) * 0.6;
        } else {
          m.jumpOffset *= 0.85;
          if (Math.abs(m.jumpOffset) < 0.05) m.jumpOffset = 0;
        }

        m.lastX = m.x;
      });
    };

    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [agents, groundYPercent]);

  const snapshot: Record<string, MotionState> = {};
  internalRef.current.forEach((m, name) => {
    snapshot[name] = {
      name,
      x: m.x,
      y: m.y,
      vx: m.vx,
      vy: m.vy,
      facingLeft: m.facingLeft,
      walkPhase: m.walkPhase,
      isMoving: m.isMoving,
      jumpOffset: m.jumpOffset,
      lastX: m.lastX,
    };
  });
  void tick;
  return snapshot;
}

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}

function pickWanderTarget(homeX: number, homeY: number): [number, number] {
  const rangeX = rand(WANDER_RANGE_MIN, WANDER_RANGE_MAX);
  const rangeY = rand(VERTICAL_WANDER_RANGE_MIN, VERTICAL_WANDER_RANGE_MAX);
  const angle = rand(0, Math.PI * 2);
  const tx = clamp(homeX + Math.cos(angle) * rangeX, MIN_X, MAX_X);
  const ty = clamp(homeY + Math.sin(angle) * rangeY, MIN_Y, MAX_Y);
  return [tx, ty];
}

function findInteractionPartner(
  self: AgentData,
  all: AgentData[],
  motions: Map<string, MotionState | { x: number; y: number }>,
): AgentData | null {
  const selfMotion = motions.get(self.name);
  if (!selfMotion) return null;
  let closest: AgentData | null = null;
  let bestDist = Infinity;
  for (const other of all) {
    if (other.name === self.name) continue;
    if (other.state !== "talking" && other.state !== "thinking" && other.state !== "idle") continue;
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

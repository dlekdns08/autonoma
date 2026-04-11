"use client";

import { useEffect, useRef, useState } from "react";
import type { AgentData, BossData, CookieData } from "@/lib/types";
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
  /** Current boss, if one is on the stage. Agents gather around it. */
  boss?: BossData | null;
  /** Fortune cookies sitting on the map. Recipients walk over to open them. */
  cookies?: CookieData[];
}

const SPEED_IDLE = 0.38;
const SPEED_WALK = 0.56;
const SPEED_RUN = 0.88;
const IDLE_PAUSE_MIN = 500;
const IDLE_PAUSE_MAX = 1800;
const WANDER_RANGE = 34;
const INTERACT_DISTANCE = 9;
const DIALOGUE_LINE_MS = 2200;
const DIALOGUE_COOLDOWN_MS = 3500;
/** Chance per action reroll that an agent picks a target in a DIFFERENT room. */
const CROSS_ROOM_CHANCE = 0.35;
/** Chance per action reroll that an agent seeks out a nearby partner to chat. */
const SEEK_PARTNER_CHANCE = 0.25;
/** Radius (percent space) at which a boss pulls agents in to attack it. */
const BOSS_ORBIT_RADIUS = 10;
/** Distance within which an agent counts as "open enough" to pick up a cookie. */
const COOKIE_PICKUP_RADIUS = 4;

// Generic percent-space bounds; a room override narrows these per agent.
const MIN_X = 2;
const MAX_X = 98;
const MIN_Y = 30;
const MAX_Y = 84;

// ── HQ interior rooms (percent space) ────────────────────────────────────
// The HQ map has three rooms separated by inner walls at cols 7 and 13
// (px 112-127, 208-223). Each agent gets assigned to a "home room" and
// mostly wanders within it — but the door gaps at row 6-7 (px 96-127)
// let them cross between rooms when they pick a target on the other side.
interface Room {
  id: string;
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  centerX: number;
  centerY: number;
}

const HQ_ROOMS: Room[] = [
  { id: "coder-lab", minX: 3, maxX: 33, minY: 34, maxY: 82, centerX: 16, centerY: 68 },
  { id: "war-room", minX: 42, maxX: 62, minY: 34, maxY: 82, centerX: 52, centerY: 68 },
  { id: "design", minX: 72, maxX: 97, minY: 34, maxY: 82, centerX: 84, centerY: 68 },
];

interface MotionInternal extends MotionState {
  targetX: number;
  targetY: number;
  nextActionAt: number;
  homeX: number;
  homeY: number;
  /** room this agent calls home (defines wander bounds) */
  room: Room;
  /** name of the agent we are currently locked into a dialogue with */
  dialogueWith: string | null;
  /** earliest time we are willing to start another dialogue */
  dialogueCooldownUntil: number;
  /** extra offset pumped each frame while attacking the boss (for lunge VFX) */
  attackPulse: number;
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

/** Find a walkable point near (homeX, homeY), clamped to the agent's room. */
function pickWalkableTarget(
  map: MapLayout,
  homeX: number,
  homeY: number,
  room: Room,
): [number, number] {
  for (let tries = 0; tries < 12; tries++) {
    const angle = rand(0, Math.PI * 2);
    const dist = rand(4, WANDER_RANGE);
    const tx = clamp(homeX + Math.cos(angle) * dist, room.minX, room.maxX);
    const ty = clamp(homeY + Math.sin(angle) * dist * 0.55, room.minY, room.maxY);
    if (canStand(map, tx, ty)) return [tx, ty];
  }
  if (canStand(map, homeX, homeY)) return [homeX, homeY];
  // last-resort outward scan bounded by the room
  for (let r = 0; r < 40; r++) {
    for (let a = 0; a < 8; a++) {
      const ang = (a * Math.PI) / 4;
      const tx = clamp(homeX + Math.cos(ang) * r, room.minX, room.maxX);
      const ty = clamp(homeY + Math.sin(ang) * r * 0.55, room.minY, room.maxY);
      if (canStand(map, tx, ty)) return [tx, ty];
    }
  }
  return [room.centerX, room.centerY];
}

/** Pick a walkable target somewhere inside another room (for cross-room visits). */
function pickCrossRoomTarget(
  map: MapLayout,
  currentRoom: Room,
): [number, number] | null {
  const candidates = HQ_ROOMS.filter((r) => r.id !== currentRoom.id);
  if (candidates.length === 0) return null;
  for (let pick = 0; pick < 4; pick++) {
    const target = candidates[Math.floor(Math.random() * candidates.length)];
    for (let tries = 0; tries < 8; tries++) {
      const tx = rand(target.minX + 3, target.maxX - 3);
      const ty = rand(target.minY + 4, target.maxY - 4);
      if (canStand(map, tx, ty)) return [tx, ty];
    }
  }
  return null;
}

/** Pick a home position inside the agent's assigned room. */
function pickHome(
  map: MapLayout,
  idx: number,
  total: number,
): { xy: [number, number]; room: Room } {
  if (map.interior) {
    const room = HQ_ROOMS[idx % HQ_ROOMS.length];
    // spread multiple agents within a single room across its width
    const perRoom = Math.max(1, Math.ceil(total / HQ_ROOMS.length));
    const slot = Math.floor(idx / HQ_ROOMS.length);
    const span = (room.maxX - room.minX - 6) / Math.max(1, perRoom);
    const hx = clamp(
      room.minX + 3 + slot * span + span / 2,
      room.minX + 2,
      room.maxX - 2,
    );
    const hy = clamp(
      room.centerY + ((idx * 7) % 10) - 5,
      room.minY + 2,
      room.maxY - 2,
    );
    if (canStand(map, hx, hy)) return { xy: [hx, hy], room };
    return { xy: pickWalkableTarget(map, hx, hy, room), room };
  }
  // outdoor fallback — everyone shares one "room" covering the full bounds
  const room: Room = {
    id: "outdoor",
    minX: MIN_X,
    maxX: MAX_X,
    minY: MIN_Y,
    maxY: MAX_Y,
    centerX: (MIN_X + MAX_X) / 2,
    centerY: (MIN_Y + MAX_Y) / 2,
  };
  const spread = total > 1 ? (MAX_X - MIN_X - 8) / total : 0;
  const hx = clamp(MIN_X + 4 + idx * spread, MIN_X + 2, MAX_X - 2);
  const hy = clamp(70 + ((idx % 3) - 1) * 6, MIN_Y + 2, MAX_Y - 2);
  if (canStand(map, hx, hy)) return { xy: [hx, hy], room };
  return { xy: pickWalkableTarget(map, hx, hy, room), room };
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

export interface DialoguePair {
  a: string;
  b: string;
  /** midpoint x in percent space — useful for rendering a heart icon */
  midX: number;
  midY: number;
}

interface MotionResult {
  motions: Record<string, MotionState>;
  bubbles: DialogueBubble[];
  pairs: DialoguePair[];
}

export function useAgentMotion({
  agents,
  map,
  boss = null,
  cookies = [],
}: Options): MotionResult {
  const [tick, setTick] = useState(0);
  const internalRef = useRef<Map<string, MotionInternal>>(new Map());
  const bubblesRef = useRef<DialogueBubble[]>([]);
  const pendingLinesRef = useRef<
    Map<string, { lines: string[]; partner: string; nextAt: number }>
  >(new Map());
  const lastFrameRef = useRef<number>(0);
  // Keep the latest boss/cookies in refs so the animation loop always reads
  // fresh values without needing to be re-created on every update.
  const bossRef = useRef<BossData | null>(boss);
  const cookiesRef = useRef<CookieData[]>(cookies);

  useEffect(() => {
    bossRef.current = boss;
  }, [boss]);

  useEffect(() => {
    cookiesRef.current = cookies;
  }, [cookies]);

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
      const { xy: [homeX, homeY], room } = pickHome(map, idx, agents.length);
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
        room,
        dialogueWith: null,
        dialogueCooldownUntil: 0,
        attackPulse: 0,
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
          const room = m.room;
          const activeBoss = bossRef.current;
          const myCookie = cookiesRef.current.find(
            (c) => c.recipient === m.name && c.openedAt === undefined,
          );

          // PRIORITY 1: if a boss is on the map, drop everything and fight.
          // Each agent picks a slightly different orbit slot so they don't
          // stack on top of each other.
          if (activeBoss) {
            const seed = hashString(m.name);
            const angle = ((seed % 360) * Math.PI) / 180;
            const radius = 6 + (seed % 5);
            const tx = clamp(
              activeBoss.x + Math.cos(angle) * radius,
              MIN_X,
              MAX_X,
            );
            const ty = clamp(
              activeBoss.y + Math.sin(angle) * radius * 0.55,
              MIN_Y,
              MAX_Y,
            );
            if (canStand(map, tx, ty)) {
              m.targetX = tx;
              m.targetY = ty;
            } else {
              // Best-effort fallback: walk towards the boss centre.
              m.targetX = activeBoss.x;
              m.targetY = activeBoss.y + 4;
            }
            // Short reroll so agents keep shuffling around the boss.
            m.nextActionAt = now + rand(600, 1400);
          }
          // PRIORITY 2: an agent with a cookie waddles over to open it.
          else if (myCookie) {
            if (canStand(map, myCookie.x, myCookie.y)) {
              m.targetX = myCookie.x;
              m.targetY = myCookie.y;
            } else {
              [m.targetX, m.targetY] = pickWalkableTarget(map, myCookie.x, myCookie.y, room);
            }
            m.nextActionAt = now + rand(1500, 2500);
          } else {

          // Partner-seek: regardless of state, occasionally walk toward a
          // nearby agent to trigger a dialogue. This is what actually makes
          // the swarm look alive instead of everyone hovering at their desk.
          const shouldSeekPartner =
            !m.dialogueWith && Math.random() < SEEK_PARTNER_CHANCE;
          const partner = shouldSeekPartner
            ? findInteractionPartner(agent, agents, motions)
            : null;

          if (partner) {
            const pm = motions.get(partner.name);
            if (pm) {
              const offset = pm.x > m.x ? -INTERACT_DISTANCE + 2 : INTERACT_DISTANCE - 2;
              const tx = pm.x + offset;
              const ty = pm.y;
              if (canStand(map, tx, ty)) {
                m.targetX = tx;
                m.targetY = ty;
              } else if (canStand(map, pm.x, pm.y)) {
                m.targetX = pm.x;
                m.targetY = pm.y;
              } else {
                [m.targetX, m.targetY] = pickWalkableTarget(map, m.homeX, m.homeY, room);
              }
              m.nextActionAt = now + rand(800, 1800);
            }
          } else if (
            map.interior &&
            HQ_ROOMS.length > 1 &&
            Math.random() < CROSS_ROOM_CHANCE
          ) {
            // Take a stroll into a different room.
            const cross = pickCrossRoomTarget(map, room);
            if (cross) {
              [m.targetX, m.targetY] = cross;
              m.nextActionAt = now + rand(2000, 4000);
            } else {
              [m.targetX, m.targetY] = pickWalkableTarget(map, m.homeX, m.homeY, room);
              m.nextActionAt = now + rand(IDLE_PAUSE_MIN, IDLE_PAUSE_MAX);
            }
          } else if (state === "celebrating") {
            [m.targetX, m.targetY] = pickWalkableTarget(map, m.x, m.y, room);
            m.nextActionAt = now + rand(300, 700);
          } else if (state === "working") {
            [m.targetX, m.targetY] = pickWalkableTarget(map, m.homeX, m.homeY, room);
            m.nextActionAt = now + rand(1800, 3600);
          } else {
            [m.targetX, m.targetY] = pickWalkableTarget(map, m.homeX, m.homeY, room);
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
          // Full-map bounds during travel so agents can cross through
          // doors and corridors; per-room clamping only applies when
          // picking targets, not while walking.
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

      // After motion: try to kick off dialogues between nearby pairs.
      // We intentionally allow moving agents to strike up conversations when
      // they pass each other — that's what makes the swarm feel alive.
      const list = Array.from(motions.values());
      for (let i = 0; i < list.length; i++) {
        const a = list[i];
        if (a.dialogueWith) continue;
        for (let j = i + 1; j < list.length; j++) {
          const b = list[j];
          if (b.dialogueWith) continue;
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

  const seen = new Set<string>();
  const pairs: DialoguePair[] = [];
  internalRef.current.forEach((m) => {
    if (!m.dialogueWith) return;
    const key = [m.name, m.dialogueWith].sort().join("↔");
    if (seen.has(key)) return;
    seen.add(key);
    const partner = internalRef.current.get(m.dialogueWith);
    if (!partner) return;
    pairs.push({
      a: m.name,
      b: partner.name,
      midX: (m.x + partner.x) / 2,
      midY: (m.y + partner.y) / 2,
    });
  });

  void tick;
  return { motions: snapshot, bubbles: bubblesRef.current.slice(), pairs };
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
    // Don't try to chat with celebrating/error agents; everything else is fair game.
    if (other.state === "celebrating" || other.state === "error") continue;
    const om = motions.get(other.name);
    if (!om) continue;
    if (om.dialogueWith) continue;
    const d = Math.hypot(om.x - selfMotion.x, om.y - selfMotion.y);
    if (d < bestDist) {
      bestDist = d;
      closest = other;
    }
  }
  return closest;
}

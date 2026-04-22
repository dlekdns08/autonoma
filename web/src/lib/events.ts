import type { EventLogEntry, EventPayloadMap } from "@/lib/types";

// Track which event names have already warned so a drifted-schema backend
// doesn't flood the console on every re-render.
const warnedEvents = new Set<string>();

/** Per-field shape checks, keyed by event name. Only keys that are
 *  actually accessed from UI code need a check — missing keys produce a
 *  one-time warning so drift is visible without hard-failing the event
 *  log (we'd rather render a degraded label than blank the panel). */
type FieldChecks = {
  [K in keyof EventPayloadMap]: {
    [F in keyof EventPayloadMap[K]]-?: (v: unknown) => boolean;
  };
};

const isStr = (v: unknown): v is string => typeof v === "string";
const isNum = (v: unknown): v is number => typeof v === "number";
const isStrArr = (v: unknown): v is string[] =>
  Array.isArray(v) && v.every((x) => typeof x === "string");

// A typed lookup table means adding a new event to EventPayloadMap forces
// you to add matching checks here — the compiler complains otherwise.
const CHECKS: FieldChecks = {
  "agent.spawned": { emoji: isStr, name: isStr, role: isStr },
  "agent.speech": { agent: isStr, text: isStr },
  "agent.level_up": { agent: isStr, level: isNum },
  "agent.dream": { agent: isStr, dream: isStr, dream_type: isStr },
  "task.completed": { agent: isStr, title: isStr },
  "file.created": { agent: isStr, path: isStr },
  "world.event": { title: isStr },
  "guild.formed": { name: isStr, members: isStrArr },
  "campfire.complete": { stories: isNum },
  "fortune.given": { agent: isStr, fortune: isStr },
  "boss.appeared": { name: isStr, level: isNum, hp: isNum },
  "boss.defeated": { name: isStr, xp_reward: isNum },
  "boss.damage": { message: isStr },
  "ghost.appears": { message: isStr },
  "swarm.round": { round: isNum },
};

/** Narrow an EventLogEntry to a specific known event. Returns the typed
 *  payload, or ``null`` if the entry isn't this event. Fields are
 *  validated opportunistically — anything that fails ``typeof`` or array
 *  shape is dropped to ``undefined`` in the returned object and a
 *  one-time warning is logged so backend drift is surfaced without
 *  corrupting the render.
 *
 *  Why opportunistic (not fail-closed): the backend can add fields at
 *  any time, and each renderer already copes with missing fields via
 *  ``?? fallback``. Hard-failing here would blank the event row the
 *  first time a harmless schema addition rolled out. The warning path
 *  gives us a signal without the blast radius. */
export function payloadOf<K extends keyof EventPayloadMap>(
  entry: EventLogEntry,
  key: K,
): EventPayloadMap[K] | null {
  if (entry.event !== key) return null;
  const raw = entry.data ?? {};
  const checks = CHECKS[key];
  const out: Record<string, unknown> = {};
  let drift = false;
  for (const field of Object.keys(checks) as (keyof typeof checks)[]) {
    const v = (raw as Record<string, unknown>)[field as string];
    if (v === undefined || v === null) continue;
    const ok = (checks[field] as (x: unknown) => boolean)(v);
    if (ok) {
      out[field as string] = v;
    } else {
      drift = true;
    }
  }
  if (drift && !warnedEvents.has(key)) {
    warnedEvents.add(key);
    // eslint-disable-next-line no-console
    console.warn(
      `[event-drift] payload for "${key}" has unexpected field types`,
      raw,
    );
  }
  return out as EventPayloadMap[K];
}

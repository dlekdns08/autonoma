/**
 * Feature #4 — Viewer betting (channel-points style).
 *
 * Frontend-only client for the viewer-betting flow. The backend has the
 * ``viewer_bets`` and ``viewer_bet_entries`` tables migrated but no
 * router yet, so this module simulates the entire flow on top of
 * ``localStorage``. Every public mutation funnels through ``placeBet``
 * / ``resolveBet`` so the same call sites can be re-pointed at a real
 * ``fetch`` once the API lands — see ``// TODO(api):`` markers below.
 *
 * Storage layout (namespace ``autonoma:vb:``):
 *   - ``autonoma:vb:balance:{viewerId}`` -> JSON ``Balance``  (per-viewer wallet)
 *   - ``autonoma:vb:bets``               -> JSON ``BetEntry[]`` (every entry ever placed)
 *   - ``autonoma:vb:resolutions``        -> JSON ``string[]``   (marketIds we've already settled)
 *   - ``autonoma:vb:lb``                 -> JSON ``LeaderboardRow[]`` (lifetime winnings, sorted desc)
 *
 * All readers are defensive against malformed JSON: every parsed shape
 * is narrowed with ``typeof``/``Array.isArray`` guards before being
 * returned, and parse failures fall back to the documented empty value
 * rather than throwing. SSR-safe: every accessor checks ``typeof window``
 * first and returns a neutral default on the server.
 */

export const STORAGE_NS = "autonoma:vb:" as const;
export const STARTING_BALANCE = 1000;
export const PAYOUT_MULTIPLIER = 3;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** A betting market the host has opened. */
export interface Market {
  /** Stable id, unique per session. */
  id: string;
  /** Numeric session id from the harness, used for backend correlation. */
  sessionId: number;
  /** Human-readable question, e.g. "Will the boss fall this round?". */
  question: string;
  /** Selectable outcomes, e.g. ``["yes","no"]`` or ``["Midori","Bear","Other"]``. */
  options: string[];
  /** Wall-clock ms (``Date.now()``) when betting closes. ``null`` = open until resolved. */
  closesAtMs: number | null;
  /** ``"open"`` accepts new entries; ``"locked"`` is awaiting resolution; ``"resolved"`` is final. */
  status: "open" | "locked" | "resolved";
}

/** A single viewer's stake on a market option. */
export interface BetEntry {
  marketId: string;
  viewerId: string;
  displayName: string;
  option: string;
  stake: number;
  /** ``Date.now()`` at placement. */
  placedAtMs: number;
  /** Resolution outcome, populated once ``resolveBet`` runs over the market. */
  result?: "won" | "lost";
  /** Channel points credited back on a win (``stake * PAYOUT_MULTIPLIER``). 0 on a loss. */
  payout?: number;
}

/** Host-side resolution announcement: "market X settled to option Y". */
export interface BetResolution {
  marketId: string;
  winningOption: string;
  /** Optional wall-clock ms for ordering / debugging. */
  resolvedAtMs?: number;
}

/** Per-viewer wallet. */
export interface Balance {
  viewerId: string;
  amount: number;
}

/** Single row of the lifetime-winnings leaderboard. */
export interface LeaderboardRow {
  viewerId: string;
  displayName: string;
  winnings: number;
}

// ---------------------------------------------------------------------------
// Internal storage helpers
// ---------------------------------------------------------------------------

function hasStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function readJSON(key: string): unknown {
  if (!hasStorage()) return undefined;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw == null) return undefined;
    return JSON.parse(raw) as unknown;
  } catch (err) {
    console.warn(`[viewerBetting] malformed JSON at ${key}, resetting`, err);
    return undefined;
  }
}

function writeJSON(key: string, value: unknown): void {
  if (!hasStorage()) return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch (err) {
    // Storage may be full or disabled (private mode). Swallow — the demo
    // shouldn't crash the dashboard if persistence fails.
    console.warn(`[viewerBetting] failed to persist ${key}`, err);
  }
}

/** Type guards — these are deliberately narrow so anything unexpected
 *  in localStorage falls back to a fresh default rather than poisoning
 *  the UI. */

function isBalance(v: unknown): v is Balance {
  return (
    typeof v === "object" &&
    v !== null &&
    typeof (v as { viewerId?: unknown }).viewerId === "string" &&
    typeof (v as { amount?: unknown }).amount === "number" &&
    Number.isFinite((v as { amount: number }).amount)
  );
}

function isBetEntry(v: unknown): v is BetEntry {
  if (typeof v !== "object" || v === null) return false;
  const o = v as Record<string, unknown>;
  if (typeof o.marketId !== "string") return false;
  if (typeof o.viewerId !== "string") return false;
  if (typeof o.displayName !== "string") return false;
  if (typeof o.option !== "string") return false;
  if (typeof o.stake !== "number" || !Number.isFinite(o.stake)) return false;
  if (typeof o.placedAtMs !== "number" || !Number.isFinite(o.placedAtMs)) return false;
  if (o.result !== undefined && o.result !== "won" && o.result !== "lost") return false;
  if (o.payout !== undefined && (typeof o.payout !== "number" || !Number.isFinite(o.payout))) {
    return false;
  }
  return true;
}

function isLeaderboardRow(v: unknown): v is LeaderboardRow {
  if (typeof v !== "object" || v === null) return false;
  const o = v as Record<string, unknown>;
  return (
    typeof o.viewerId === "string" &&
    typeof o.displayName === "string" &&
    typeof o.winnings === "number" &&
    Number.isFinite(o.winnings)
  );
}

function balanceKey(viewerId: string): string {
  return `${STORAGE_NS}balance:${viewerId}`;
}

const BETS_KEY = `${STORAGE_NS}bets`;
const RESOLUTIONS_KEY = `${STORAGE_NS}resolutions`;
const LB_KEY = `${STORAGE_NS}lb`;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Read the wallet for ``viewerId``, seeding the default ``STARTING_BALANCE``
 *  if no row has been written yet. Always returns a valid ``Balance``. */
export function getBalance(viewerId: string): Balance {
  const raw = readJSON(balanceKey(viewerId));
  if (isBalance(raw)) return raw;
  const seeded: Balance = { viewerId, amount: STARTING_BALANCE };
  writeJSON(balanceKey(viewerId), seeded);
  return seeded;
}

function setBalance(next: Balance): void {
  writeJSON(balanceKey(next.viewerId), next);
}

/** Read every persisted ``BetEntry``, dropping any malformed rows. */
export function getBets(): BetEntry[] {
  const raw = readJSON(BETS_KEY);
  if (!Array.isArray(raw)) return [];
  return raw.filter(isBetEntry);
}

function setBets(entries: BetEntry[]): void {
  writeJSON(BETS_KEY, entries);
}

function getResolvedMarketIds(): string[] {
  const raw = readJSON(RESOLUTIONS_KEY);
  if (!Array.isArray(raw)) return [];
  return raw.filter((v): v is string => typeof v === "string");
}

function setResolvedMarketIds(ids: string[]): void {
  writeJSON(RESOLUTIONS_KEY, ids);
}

/** Read the lifetime-winnings leaderboard, sorted descending. */
export function getLeaderboard(): LeaderboardRow[] {
  const raw = readJSON(LB_KEY);
  if (!Array.isArray(raw)) return [];
  return raw
    .filter(isLeaderboardRow)
    .sort((a, b) => b.winnings - a.winnings);
}

function setLeaderboard(rows: LeaderboardRow[]): void {
  writeJSON(LB_KEY, rows);
}

function bumpLeaderboard(viewerId: string, displayName: string, delta: number): void {
  if (delta <= 0) return;
  const rows = getLeaderboard();
  const idx = rows.findIndex((r) => r.viewerId === viewerId);
  if (idx >= 0) {
    rows[idx] = {
      viewerId,
      displayName, // refresh in case the viewer changed their display name
      winnings: rows[idx].winnings + delta,
    };
  } else {
    rows.push({ viewerId, displayName, winnings: delta });
  }
  rows.sort((a, b) => b.winnings - a.winnings);
  setLeaderboard(rows);
}

/** Result of a ``placeBet`` call. ``ok=false`` carries a user-facing reason
 *  ("insufficient funds", "duplicate", …) so the widget can surface a toast
 *  without re-implementing the validation logic. */
export type PlaceBetResult =
  | { ok: true; entry: BetEntry; balance: Balance }
  | { ok: false; reason: "insufficient_balance" | "invalid_stake" | "duplicate" };

/** Atomic-ish: validate, deduct, append. Returns the persisted entry plus
 *  the post-deduction balance. Idempotent on (viewerId, marketId): a viewer
 *  can only stake once per market in this MVP. */
export function placeBet(
  entry: Omit<BetEntry, "placedAtMs" | "result" | "payout">,
): PlaceBetResult {
  // TODO(api): replace this body with `await fetch("/api/viewer-bets", { method: "POST", … })`
  // once the backend router lands; the response shape should mirror PlaceBetResult.
  if (!Number.isFinite(entry.stake) || entry.stake <= 0) {
    return { ok: false, reason: "invalid_stake" };
  }

  const existing = getBets();
  if (existing.some((b) => b.marketId === entry.marketId && b.viewerId === entry.viewerId)) {
    return { ok: false, reason: "duplicate" };
  }

  const balance = getBalance(entry.viewerId);
  if (balance.amount < entry.stake) {
    return { ok: false, reason: "insufficient_balance" };
  }

  const nextBalance: Balance = { viewerId: entry.viewerId, amount: balance.amount - entry.stake };
  setBalance(nextBalance);

  const persisted: BetEntry = { ...entry, placedAtMs: Date.now() };
  setBets([...existing, persisted]);
  return { ok: true, entry: persisted, balance: nextBalance };
}

/** Per-entry settlement record returned by ``resolveBet``. The widget uses
 *  this to fan out one toast per affected entry. */
export interface SettledEntry {
  entry: BetEntry;
  delta: number; // net change to balance: +payout on win, 0 on loss
}

/** Apply a host resolution: mark every matching entry won/lost, credit
 *  payouts (``stake * PAYOUT_MULTIPLIER``), and bump the leaderboard for
 *  winners. Idempotent: a second call for the same ``marketId`` is a no-op. */
export function resolveBet(resolution: BetResolution): SettledEntry[] {
  // TODO(api): replace with a server call once the router is up; the
  // server will emit ``viewer_bet_resolved`` over the bus and the client
  // will receive the same SettledEntry[] shape.
  const resolved = getResolvedMarketIds();
  if (resolved.includes(resolution.marketId)) return [];

  const entries = getBets();
  const affected: SettledEntry[] = [];

  const nextEntries = entries.map((e) => {
    if (e.marketId !== resolution.marketId) return e;
    if (e.result) return e; // already settled in a previous pass — defensive
    const won = e.option === resolution.winningOption;
    const payout = won ? e.stake * PAYOUT_MULTIPLIER : 0;
    const settled: BetEntry = {
      ...e,
      result: won ? "won" : "lost",
      payout,
    };
    affected.push({ entry: settled, delta: payout });

    if (won && payout > 0) {
      const bal = getBalance(e.viewerId);
      setBalance({ viewerId: e.viewerId, amount: bal.amount + payout });
      bumpLeaderboard(e.viewerId, e.displayName, payout);
    }
    return settled;
  });

  setBets(nextEntries);
  setResolvedMarketIds([...resolved, resolution.marketId]);
  return affected;
}

/** Test/demo helper — wipe every key under the namespace. Not exported
 *  through the hook on purpose; reach for it in dev tools only. */
export function __resetForTesting(): void {
  if (!hasStorage()) return;
  const keys: string[] = [];
  for (let i = 0; i < window.localStorage.length; i++) {
    const k = window.localStorage.key(i);
    if (k && k.startsWith(STORAGE_NS)) keys.push(k);
  }
  for (const k of keys) window.localStorage.removeItem(k);
}

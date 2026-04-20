/**
 * VRM asset catalog + credits.
 *
 * Single source of truth: `vrmCatalog.json` in this directory. Every
 * model in `public/vrm/` must have an entry there. This module just
 * imports that JSON and exposes a typed API.
 *
 * ── Adding a new VRM ────────────────────────────────────────────────
 *   1. Drop the `.vrm` file into `public/vrm/`.
 *   2. Add an entry (keyed by the filename) to `vrmCatalog.json`.
 *   3. Run `npm run vrm:sync-licenses` — regenerates `public/vrm/LICENSES.md`
 *      from the catalog so in-app credits and the license file never drift.
 *
 * The spotlight renders `credit.character` + `credit.author` as an
 * on-screen overlay to satisfy the "Attribution" clause of the VRoid Hub
 * terms. Agents are assigned to models deterministically via a djb2 hash
 * of their name so a given agent keeps the same character across sessions.
 */

import catalog from "./vrmCatalog.json";

/** License flags as listed on VRoid Hub — strings so we can mirror the
 *  creator's exact wording ("Allow", "Allow with credit", "Not required",
 *  "Deny", etc.) rather than losing nuance in booleans. */
export interface VrmLicense {
  avatarUse: string;
  violentActs: string;
  sexualActs: string;
  corporateUse: string;
  individualCommercialUse: string;
  redistribution: string;
  alterations: string;
  attribution: string;
}

export interface VrmCredit {
  /** Short display name shown in the spotlight overlay. */
  character: string;
  /** Optional fuller title for the LICENSES.md entry — falls back to
   *  `character` when omitted. */
  title?: string;
  /** Author as displayed on VRoid Hub — preserve original script (JP/EN). */
  author: string;
  /** Canonical VRoid Hub URL for the model. */
  url: string;
  /** ISO date the model was uploaded / version we pinned. */
  uploaded: string;
  license: VrmLicense;
}

/** All registered VRMs, keyed by the `.vrm` filename (no path). */
export const VRM_CREDITS: Record<string, VrmCredit> = catalog as Record<
  string,
  VrmCredit
>;

/** List of available .vrm filenames, in assignment order. */
export const VRM_FILES: string[] = Object.keys(VRM_CREDITS);

/** Deterministic name → .vrm filename mapping.
 *
 *  Uses a cheap djb2 hash modulo the roster size so the same agent name
 *  always ends up with the same character across sessions. The frontend
 *  already does this for procedural faces (see `faceSeed.ts`); we keep
 *  the pattern consistent here so a given agent has a stable identity
 *  whether the procedural face or the VRM is rendered.
 */
export function vrmFileForAgent(agentName: string): string {
  let h = 5381;
  for (let i = 0; i < agentName.length; i++) {
    h = ((h << 5) + h + agentName.charCodeAt(i)) >>> 0;
  }
  return VRM_FILES[h % VRM_FILES.length];
}

/** Convenience lookup — resolves agent name straight to the credit block. */
export function creditForAgent(agentName: string): VrmCredit {
  return VRM_CREDITS[vrmFileForAgent(agentName)];
}

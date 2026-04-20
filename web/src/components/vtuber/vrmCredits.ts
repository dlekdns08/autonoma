/**
 * VRM asset credits.
 *
 * Every model in `public/vrm/` must have an entry here. The VTuber
 * spotlight renders author + source link as an on-screen overlay to
 * satisfy the "Attribution: Required" clause of the VRoid Hub terms.
 *
 * Keys are the `.vrm` filename (without path). Update `public/vrm/LICENSES.md`
 * in lockstep whenever you add or remove an entry.
 */

export interface VrmCredit {
  /** Short display name shown beside the author handle. */
  character: string;
  /** Author as displayed on VRoid Hub — preserve original script (JP/EN). */
  author: string;
  /** Canonical VRoid Hub URL for the model. */
  url: string;
}

export const VRM_CREDITS: Record<string, VrmCredit> = {
  "midori.vrm": {
    character: "Midori",
    author: "NorthrnPoakr",
    url: "https://hub.vroid.com/en/characters/2042997311814320466/models/7960975711904385388",
  },
  "konomi.vrm": {
    character: "Konomi",
    author: "キャラクター紹介サイト管理人",
    url: "https://hub.vroid.com/en/characters/4633719109621809644/models/7532679100113812494",
  },
  "ca06.vrm": {
    character: "CA06",
    author: "桜田とまこ",
    url: "https://hub.vroid.com/en/characters/6149999330314262985/models/5866099083641763739",
  },
};

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

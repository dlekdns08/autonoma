import { useCallback, useRef, useState } from "react";

/** Row-level patch pushed from the server on binding mutations. Consumers
 *  splice this into their local state instead of refetching. */
export interface VoiceBindingEvent {
  vrm_file: string;
  profile_id: string | null;
  removed: boolean;
  seq: number;
}

export interface UseVoiceEventState {
  refreshToken: number;
  latestEvent: VoiceBindingEvent | null;
  /** Bump refreshToken — used on mount and after WS reconnect. */
  bumpRefresh: () => void;
  /** Push a patch from a ``voice.bindings.updated`` WS payload. */
  applyPatch: (args: { vrm_file: string; profile_id: string | null; removed: boolean }) => void;
}

/** Three-tier voice binding refresh state extracted from useSwarm.
 *
 *  Owns the `refreshToken` (full resync trigger), the `latestEvent`
 *  (row-level patch), and the monotonic seq the UI uses to dedupe.
 *  useSwarm composes this hook and wires the WS handler through
 *  ``applyPatch``; the voice page consumes ``refreshToken`` and
 *  ``latestEvent`` directly.
 */
export function useVoiceEventState(): UseVoiceEventState {
  const [refreshToken, setRefreshToken] = useState(0);
  const [latestEvent, setLatestEvent] = useState<VoiceBindingEvent | null>(null);
  const seqRef = useRef(0);

  const bumpRefresh = useCallback(() => {
    setRefreshToken((n) => n + 1);
  }, []);

  const applyPatch = useCallback(
    ({ vrm_file, profile_id, removed }: {
      vrm_file: string;
      profile_id: string | null;
      removed: boolean;
    }) => {
      setLatestEvent({
        vrm_file,
        profile_id,
        removed,
        seq: ++seqRef.current,
      });
    },
    [],
  );

  return { refreshToken, latestEvent, bumpRefresh, applyPatch };
}

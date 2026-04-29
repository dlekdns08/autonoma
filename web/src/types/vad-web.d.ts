/**
 * Minimal ambient declaration for ``@ricky0123/vad-web``. The real
 * package ships its own ``.d.ts`` once installed via npm; this stub
 * keeps ``tsc --noEmit`` green in environments where the install
 * hasn't happened yet (CI cold-clone, fresh checkout) so a missing
 * dependency surfaces at runtime rather than blocking type-checks
 * for unrelated changes.
 *
 * Once ``npm install`` lands, the real types take precedence
 * automatically (project includes ``node_modules`` first via tsconfig).
 */

declare module "@ricky0123/vad-web" {
  export interface MicVADOptions {
    onSpeechStart?: () => void;
    onSpeechEnd?: (audio: Float32Array) => void;
    onVADMisfire?: () => void;
    positiveSpeechThreshold?: number;
    negativeSpeechThreshold?: number;
    redemptionFrames?: number;
    preSpeechPadFrames?: number;
    minSpeechFrames?: number;
    submitUserSpeechOnPause?: boolean;
  }

  export class MicVAD {
    static new(options?: MicVADOptions): Promise<MicVAD>;
    start(): void;
    pause(): void;
    destroy(): void;
  }
}

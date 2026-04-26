"use client";

/**
 * Top-level "/pose-editor" panel — wraps the 3D scene with a character
 * picker, a selection inspector, and Save / Reset / Sync IK controls.
 *
 * Save path mirrors ``/mocap``: snapshot the VRM bone quaternions into
 * a 2-frame static-pose ``MocapClip``, gzip + b64-encode, and POST to
 * ``/api/mocap-clips`` via ``useMocapClips.upload``. The resulting clip
 * shows up in the same library as recorded clips and can be bound to
 * any trigger via the existing ``BindingEditor``.
 */

import { useMemo, useRef, useState } from "react";
import CharacterPicker from "@/components/mocap/CharacterPicker";
import PoseEditorScene, {
  type Selection,
} from "@/components/poseEditor/PoseEditorScene";
import { useMocapBindings } from "@/hooks/mocap/useMocapBindings";
import { useMocapClips } from "@/hooks/mocap/useMocapClips";
import { useSwarm } from "@/hooks/useSwarm";
import { encodeClip } from "@/lib/mocap/gzipEncode";
import {
  resetPoseToBaseline,
  snapshotPoseAsClip,
} from "@/lib/poseEditor/poseClip";
import type { MocapBone } from "@/lib/mocap/clipFormat";
import type { MocapBoneMap } from "@/lib/mocap/vrmShared";

const SELECTABLE_BONES: readonly MocapBone[] = [
  "head",
  "neck",
  "upperChest",
  "chest",
  "spine",
  "hips",
  "leftShoulder",
  "leftUpperArm",
  "leftLowerArm",
  "leftHand",
  "rightShoulder",
  "rightUpperArm",
  "rightLowerArm",
  "rightHand",
  "leftUpperLeg",
  "leftLowerLeg",
  "leftFoot",
  "rightUpperLeg",
  "rightLowerLeg",
  "rightFoot",
];

const FRIENDLY: Record<MocapBone, string> = {
  hips: "골반", spine: "허리", chest: "가슴", upperChest: "윗가슴",
  neck: "목", head: "머리",
  leftShoulder: "왼쪽 어깨", leftUpperArm: "왼쪽 윗팔",
  leftLowerArm: "왼쪽 아래팔", leftHand: "왼손",
  rightShoulder: "오른쪽 어깨", rightUpperArm: "오른쪽 윗팔",
  rightLowerArm: "오른쪽 아래팔", rightHand: "오른손",
  leftUpperLeg: "왼쪽 허벅지", leftLowerLeg: "왼쪽 종아리", leftFoot: "왼발",
  rightUpperLeg: "오른쪽 허벅지", rightLowerLeg: "오른쪽 종아리", rightFoot: "오른발",
  leftThumbMetacarpal: "왼쪽 엄지 중수",
  leftThumbProximal: "왼쪽 엄지 근위", leftThumbDistal: "왼쪽 엄지 말단",
  leftIndexProximal: "왼쪽 검지 근위",
  leftIndexIntermediate: "왼쪽 검지 중간", leftIndexDistal: "왼쪽 검지 말단",
  leftMiddleProximal: "왼쪽 중지 근위",
  leftMiddleIntermediate: "왼쪽 중지 중간", leftMiddleDistal: "왼쪽 중지 말단",
  leftRingProximal: "왼쪽 약지 근위",
  leftRingIntermediate: "왼쪽 약지 중간", leftRingDistal: "왼쪽 약지 말단",
  leftLittleProximal: "왼쪽 새끼 근위",
  leftLittleIntermediate: "왼쪽 새끼 중간", leftLittleDistal: "왼쪽 새끼 말단",
  rightThumbMetacarpal: "오른쪽 엄지 중수",
  rightThumbProximal: "오른쪽 엄지 근위", rightThumbDistal: "오른쪽 엄지 말단",
  rightIndexProximal: "오른쪽 검지 근위",
  rightIndexIntermediate: "오른쪽 검지 중간", rightIndexDistal: "오른쪽 검지 말단",
  rightMiddleProximal: "오른쪽 중지 근위",
  rightMiddleIntermediate: "오른쪽 중지 중간", rightMiddleDistal: "오른쪽 중지 말단",
  rightRingProximal: "오른쪽 약지 근위",
  rightRingIntermediate: "오른쪽 약지 중간", rightRingDistal: "오른쪽 약지 말단",
  rightLittleProximal: "오른쪽 새끼 근위",
  rightLittleIntermediate: "오른쪽 새끼 중간", rightLittleDistal: "오른쪽 새끼 말단",
};

type Stage = "idle" | "uploading" | "error" | "saved";

export default function PoseEditorPanel() {
  const [targetVrm, setTargetVrm] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [poseName, setPoseName] = useState("");
  const [stage, setStage] = useState<Stage>("idle");
  const [stageMessage, setStageMessage] = useState<string | null>(null);
  const [ikSyncToken, setIkSyncToken] = useState(0);
  const bonesRef = useRef<MocapBoneMap>({});

  const { mocapClipEvent } = useSwarm();
  const [refreshToken, setRefreshToken] = useState(0);
  const clipsApi = useMocapClips(refreshToken, mocapClipEvent);
  const bindingsApi = useMocapBindings(refreshToken);

  const handleBonesReady = (m: MocapBoneMap) => {
    bonesRef.current = m;
    setSelection(null);
    setStage("idle");
    setStageMessage(null);
  };

  const handleReset = () => {
    if (!Object.keys(bonesRef.current).length) return;
    resetPoseToBaseline(bonesRef.current);
    setSelection(null);
    // Bump the IK sync token so the wrist target spheres jump back to
    // the reset wrist positions instead of pointing at where the user
    // last dragged them.
    setIkSyncToken((n) => n + 1);
  };

  const handleSyncIK = () => {
    setIkSyncToken((n) => n + 1);
  };

  const handleSave = async () => {
    if (!targetVrm || !Object.keys(bonesRef.current).length) return;
    const name = poseName.trim() || `pose ${new Date().toLocaleTimeString()}`;
    const clip = snapshotPoseAsClip(bonesRef.current, {
      name,
      sourceVrm: targetVrm,
    });
    setStage("uploading");
    setStageMessage(null);
    try {
      const { payloadGzB64, rawSizeBytes } = await encodeClip(clip);
      const res = await clipsApi.upload({
        name: clip.name,
        sourceVrm: clip.sourceVrm,
        payloadGzB64,
        expectedSizeBytes: rawSizeBytes,
      });
      if (!res.ok) {
        setStage("error");
        setStageMessage(`저장 실패: ${res.reason}`);
        return;
      }
      setStage("saved");
      setStageMessage(`"${clip.name}" 저장 완료`);
      setRefreshToken((n) => n + 1);
    } catch (err) {
      setStage("error");
      setStageMessage(err instanceof Error ? err.message : String(err));
    }
  };

  const selLabel = useMemo(() => {
    if (!selection) return "(없음 — 핸들 또는 IK 큐브 클릭)";
    if (selection.kind === "fk") {
      return `FK 회전 · ${FRIENDLY[selection.bone] ?? selection.bone}`;
    }
    return `IK 드래그 · ${selection.side === "L" ? "왼손" : "오른손"}`;
  }, [selection]);

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2">
        <h2 className="font-mono text-sm font-semibold text-white/80">
          캐릭터 선택
        </h2>
        <CharacterPicker
          selected={targetVrm}
          onSelect={(f) => {
            setTargetVrm(f);
            setSelection(null);
            setStage("idle");
            setStageMessage(null);
          }}
          bindings={bindingsApi.bindings}
        />
      </section>

      {targetVrm && (
        <section className="grid gap-4 md:grid-cols-[minmax(0,1fr)_320px]">
          <PoseEditorScene
            vrmFile={targetVrm}
            selectableBones={SELECTABLE_BONES}
            selection={selection}
            onSelectionChange={setSelection}
            onBonesReady={handleBonesReady}
            ikSyncToken={ikSyncToken}
          />

          <aside className="flex flex-col gap-3 rounded-lg border border-white/10 bg-slate-950/70 p-3 font-mono text-xs">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-white/40">
                선택
              </div>
              <div className="mt-0.5 text-white">{selLabel}</div>
            </div>

            <div className="rounded border border-white/10 bg-slate-900/60 p-2 text-[11px] leading-relaxed text-white/60">
              <div className="mb-1 text-white/80">조작 방법</div>
              <ul className="ml-3 list-disc space-y-0.5">
                <li>
                  <span className="text-cyan-300">청록 점</span> = 관절 → 클릭하면 회전 기즈모 (FK)
                </li>
                <li>
                  <span className="text-orange-300">주황</span> / <span className="text-purple-300">보라</span> 큐브 = 손목 IK 타깃 → 드래그하면 어깨·팔꿈치가 자동
                </li>
                <li>빈 공간 클릭 = 선택 해제</li>
                <li>마우스 우클릭 + 드래그 = 카메라 회전</li>
              </ul>
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleSyncIK}
                className="rounded border border-white/15 bg-slate-900/60 px-3 py-1.5 text-white/80 hover:border-white/35"
                title="FK로 손목을 옮긴 뒤, IK 큐브를 현재 손목 위치로 다시 맞추기"
              >
                IK 타깃 동기화
              </button>
              <button
                type="button"
                onClick={handleReset}
                className="rounded border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-rose-200 hover:bg-rose-500/20"
              >
                초기화
              </button>
            </div>

            <div className="mt-2 border-t border-white/10 pt-3">
              <div className="text-[10px] uppercase tracking-wide text-white/40">
                저장
              </div>
              <input
                value={poseName}
                onChange={(e) => setPoseName(e.target.value)}
                placeholder="포즈 이름"
                className="mt-1 w-full rounded border border-white/15 bg-slate-950/80 px-2 py-1 text-xs text-white outline-none focus:border-fuchsia-500/60"
              />
              <button
                type="button"
                onClick={handleSave}
                disabled={stage === "uploading"}
                className="mt-2 w-full rounded border border-fuchsia-500/50 bg-fuchsia-500/15 px-3 py-1.5 text-fuchsia-100 hover:bg-fuchsia-500/25 disabled:opacity-40"
              >
                {stage === "uploading" ? "저장 중…" : "정적 포즈로 저장"}
              </button>
              {stageMessage && (
                <div
                  className={
                    "mt-2 text-[11px] " +
                    (stage === "error" ? "text-rose-300" : "text-emerald-300")
                  }
                >
                  {stageMessage}
                </div>
              )}
              <p className="mt-2 text-[10px] leading-relaxed text-white/35">
                저장된 포즈는 ``/mocap`` 의 클립 라이브러리에 1프레임짜리로 등록되며, 동일한 트리거 바인딩 파이프라인을 통해 mood / emote / state 에 연결할 수 있습니다.
              </p>
            </div>

            <div className="text-[10px] text-white/30">
              총 {clipsApi.clips.length}개 클립이 라이브러리에 있습니다.
            </div>
          </aside>
        </section>
      )}
    </div>
  );
}

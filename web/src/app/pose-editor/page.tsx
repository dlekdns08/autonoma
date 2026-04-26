"use client";

/**
 * ``/pose-editor`` page — manually compose a static VRM pose with mouse
 * controls (FK rotation gizmo per bone + IK drag handles for the
 * wrists), then save the snapshot as a 1-frame mocap clip that the
 * dashboard can trigger via the existing binding pipeline.
 *
 * Auth gating mirrors ``/mocap``: pending / denied / disabled users see
 * a 403 panel; only signed-in active users can edit and save poses.
 */

import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import PoseEditorPanel from "@/components/poseEditor/PoseEditorPanel";

export default function PoseEditorPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  const isActive = user?.status === "active";

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/40">
        loading…
      </div>
    );
  }
  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12]">
        <div className="max-w-md rounded-2xl border border-white/10 bg-slate-950/95 p-8 text-center shadow-2xl">
          <h1 className="font-mono text-2xl font-bold text-white">
            로그인이 필요합니다
          </h1>
          <p className="mt-2 font-mono text-xs text-white/50">
            /pose-editor 페이지는 인증된 계정으로만 접근할 수 있습니다.
          </p>
          <button
            onClick={() => router.push("/")}
            className="mt-4 rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/70 hover:bg-white/10"
          >
            홈으로
          </button>
        </div>
      </div>
    );
  }
  if (!isActive) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12]">
        <div className="max-w-md rounded-2xl border border-rose-500/30 bg-slate-950/95 p-8 text-center shadow-2xl">
          <div className="mb-3 text-4xl">⛔</div>
          <h1 className="font-mono text-2xl font-bold text-rose-300">403</h1>
          <p className="mt-2 font-mono text-sm text-white/60">
            계정이 아직 활성 상태가 아닙니다. 관리자 승인 후 다시 시도하세요.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0a12] p-4 text-white">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="bg-gradient-to-r from-fuchsia-400 to-cyan-400 bg-clip-text font-mono text-2xl font-bold text-transparent">
              포즈 에디터
            </h1>
            <p className="mt-1 font-mono text-xs text-white/40">
              마우스로 VRM 자세를 직접 만들고, 1프레임 정적 포즈로 저장합니다.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => router.push("/mocap")}
              className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/60 hover:bg-white/10"
            >
              모션 캡처 →
            </button>
            <button
              onClick={() => router.push("/")}
              className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 font-mono text-xs text-white/60 hover:bg-white/10"
            >
              ← 대시보드
            </button>
          </div>
        </header>

        <PoseEditorPanel />
      </div>
    </div>
  );
}

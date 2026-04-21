"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";
import type { AuthState, LLMProvider, UserCredentials } from "@/lib/types";

interface Props {
  authState: AuthState;
  onAuthenticate: (credentials: UserCredentials) => void;
  onClose: () => void;
}

type ModelOpt = { value: string; label: string };

const CLAUDE_MODELS: ModelOpt[] = [
  { value: "claude-opus-4-7", label: "Claude Opus 4.7" },
  { value: "claude-sonnet-4-6", label: "Claude Sonnet 4.6 (권장)" },
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5 (빠름)" },
];

const OPENAI_MODELS: ModelOpt[] = [
  { value: "gpt-4o", label: "GPT-4o (권장)" },
  { value: "gpt-4o-mini", label: "GPT-4o mini (빠름)" },
  { value: "gpt-4-turbo", label: "GPT-4 Turbo" },
  { value: "o1", label: "o1 (추론)" },
  { value: "o1-mini", label: "o1-mini" },
];

export default function ModelSettingsModal({
  authState,
  onAuthenticate,
  onClose,
}: Props) {
  const [provider, setProvider] = useState<LLMProvider>(
    (authState.provider as LLMProvider | null) ?? "anthropic",
  );
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(
    authState.model ?? CLAUDE_MODELS[0].value,
  );
  const [customModel, setCustomModel] = useState(
    authState.model && !CLAUDE_MODELS.find((m) => m.value === authState.model) && !OPENAI_MODELS.find((m) => m.value === authState.model)
      ? authState.model
      : "",
  );
  const [baseUrl, setBaseUrl] = useState("http://localhost:8080/v1");
  const [isCustomModel, setIsCustomModel] = useState(false);

  const [dynamicModels, setDynamicModels] = useState<ModelOpt[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [isLiveList, setIsLiveList] = useState(false);

  // Reset preset when provider changes. The currently-authenticated model
  // is only meaningful for the provider it was chosen under, so swap it
  // out whenever the user picks a different provider.
  useEffect(() => {
    setIsCustomModel(false);
    setCustomModel("");
    setDynamicModels(null);
    setModelsError(null);
    setIsLiveList(false);
    if (provider === "anthropic") setModel(CLAUDE_MODELS[0].value);
    else if (provider === "openai") setModel(OPENAI_MODELS[0].value);
    else setModel("");
  }, [provider]);

  const fallbackPresets =
    provider === "anthropic" ? CLAUDE_MODELS :
    provider === "openai" ? OPENAI_MODELS : [];

  const presets = dynamicModels ?? fallbackPresets;

  const canFetchModels =
    (provider !== "vllm" && apiKey.trim().length > 0) ||
    (provider === "vllm" && baseUrl.trim().length > 0);

  const fetchModels = useCallback(async () => {
    if (!canFetchModels) return;
    setModelsLoading(true);
    setModelsError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/models`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          provider,
          api_key: apiKey.trim(),
          base_url: provider === "vllm" ? baseUrl.trim() : "",
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as {
        models: ModelOpt[];
        is_live: boolean;
      };
      if (data.models && data.models.length > 0) {
        setDynamicModels(data.models);
        setIsLiveList(data.is_live);
        setModel(data.models[0].value);
      } else {
        setModelsError("사용 가능한 모델이 없습니다.");
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setModelsError(`모델 조회 실패: ${msg}`);
    } finally {
      setModelsLoading(false);
    }
  }, [provider, apiKey, baseUrl, canFetchModels]);

  const effectiveModel = isCustomModel || provider === "vllm" ? customModel : model;

  const canSubmit = useMemo(() => {
    if (!effectiveModel.trim()) return false;
    if (provider === "vllm") return baseUrl.trim().length > 0;
    return apiKey.trim().length > 0;
  }, [provider, apiKey, effectiveModel, baseUrl]);

  const handleSave = useCallback(() => {
    if (!canSubmit) return;
    onAuthenticate({
      type: "user",
      provider,
      api_key: apiKey.trim(),
      model: effectiveModel.trim(),
      base_url: provider === "vllm" ? baseUrl.trim() : undefined,
    });
    onClose();
  }, [canSubmit, provider, apiKey, effectiveModel, baseUrl, onAuthenticate, onClose]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") handleSave();
      else if (e.key === "Escape") onClose();
    },
    [handleSave, onClose],
  );

  const ProviderBtn = ({
    value, icon, label,
  }: { value: LLMProvider; icon: string; label: string }) => (
    <button
      type="button"
      onClick={() => setProvider(value)}
      className={`flex flex-1 flex-col items-center gap-1 rounded-lg border px-3 py-2 text-xs font-mono transition-all ${
        provider === value
          ? "border-fuchsia-500/70 bg-fuchsia-500/15 text-fuchsia-300"
          : "border-white/10 bg-white/5 text-white/40 hover:border-white/20 hover:text-white/60"
      }`}
    >
      <span className="text-base">{icon}</span>
      <span>{label}</span>
    </button>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-fuchsia-500/30 bg-slate-950/95 p-6 shadow-2xl shadow-fuchsia-500/10"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-base font-mono font-bold text-white/90">
            모델 설정
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-white/40 hover:text-white/80 text-lg leading-none"
            aria-label="닫기"
          >
            ×
          </button>
        </div>

        {authState.status === "authenticated" && authState.model && (
          <div className="mb-4 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-[11px] font-mono text-emerald-300/80">
            현재 사용 중: {authState.provider ?? "?"} / {authState.model}
          </div>
        )}

        <div className="flex flex-col gap-4">
          <div>
            <label className="mb-1.5 block text-xs font-mono text-white/50">
              LLM 프로바이더
            </label>
            <div className="flex gap-2">
              <ProviderBtn value="anthropic" icon="🟣" label="Claude" />
              <ProviderBtn value="openai" icon="🟢" label="OpenAI" />
              <ProviderBtn value="vllm" icon="⚡" label="vLLM" />
            </div>
          </div>

          {provider !== "vllm" && (
            <div>
              <label className="mb-1.5 block text-xs font-mono text-white/50">
                {provider === "anthropic" ? "Anthropic API 키" : "OpenAI API 키"}
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={
                  provider === "anthropic" ? "sk-ant-api03-..." : "sk-proj-..."
                }
                autoFocus
                className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-cyan-500/60 transition-colors"
              />
            </div>
          )}

          {provider === "vllm" && (
            <div>
              <label className="mb-1.5 block text-xs font-mono text-white/50">
                vLLM 서버 URL
              </label>
              <input
                type="text"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://localhost:8080/v1"
                autoFocus
                className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-cyan-500/60 transition-colors"
              />
              <p className="mt-1 text-[10px] text-white/30 font-mono">
                OpenAI-compatible endpoint (/v1/chat/completions)
              </p>
              <div className="mt-3">
                <label className="mb-1.5 block text-xs font-mono text-white/50">
                  API 키 (선택, 인증이 필요한 경우)
                </label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Bearer token (선택)"
                  className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-cyan-500/60 transition-colors"
                />
              </div>
            </div>
          )}

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <label className="block text-xs font-mono text-white/50">
                모델
                {isLiveList && (
                  <span className="ml-2 text-[10px] text-emerald-400/80">
                    ● live
                  </span>
                )}
              </label>
              <button
                type="button"
                onClick={fetchModels}
                disabled={!canFetchModels || modelsLoading}
                className="text-[10px] font-mono text-cyan-300/70 hover:text-cyan-300 disabled:text-white/20 disabled:cursor-not-allowed transition-colors"
              >
                {modelsLoading ? "불러오는 중..." : "모델 불러오기 ⟳"}
              </button>
            </div>
            {presets.length > 0 && !isCustomModel && (
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-slate-900 px-3 py-2.5 text-sm font-mono text-white outline-none focus:border-cyan-500/60 transition-colors"
              >
                {presets.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            )}
            {(presets.length === 0 || isCustomModel) && (
              <input
                type="text"
                value={customModel}
                onChange={(e) => setCustomModel(e.target.value)}
                placeholder={
                  provider === "vllm"
                    ? "모델명 (예: Llama-3-8B-Instruct)"
                    : "모델명 직접 입력"
                }
                className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-cyan-500/60 transition-colors"
              />
            )}
            {presets.length > 0 && (
              <button
                type="button"
                onClick={() => {
                  setIsCustomModel((v) => !v);
                  setCustomModel("");
                }}
                className="mt-1.5 text-[10px] font-mono text-white/30 hover:text-white/50 transition-colors"
              >
                {isCustomModel ? "← 프리셋 목록으로" : "직접 입력..."}
              </button>
            )}
            {modelsError && (
              <p className="mt-1.5 text-[10px] font-mono text-red-400/80">
                {modelsError}
              </p>
            )}
          </div>

          <button
            type="button"
            onClick={handleSave}
            disabled={!canSubmit}
            className="mt-1 w-full rounded-xl bg-gradient-to-r from-cyan-600 to-fuchsia-600 py-3 text-sm font-bold font-mono text-white hover:from-cyan-500 hover:to-fuchsia-500 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
          >
            저장 및 적용
          </button>

          {authState.error && (
            <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[11px] font-mono text-red-300">
              {authState.error}
            </div>
          )}

          <p className="text-center text-[10px] font-mono text-white/30">
            API 키는 이 브라우저 세션에만 저장됩니다.
          </p>
        </div>
      </div>
    </div>
  );
}

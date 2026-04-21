"use client";

import { useCallback, useEffect, useState } from "react";
import type { AuthState, LLMProvider, UserCredentials } from "@/lib/types";
import { API_BASE_URL } from "@/hooks/useSwarm";

interface Props {
  authState: AuthState;
  onAuthenticate: (credentials: UserCredentials) => void;
}

// ── Preset models per provider (fallback when dynamic lookup fails) ──────

type ModelOpt = { value: string; label: string };

const CLAUDE_MODELS: ModelOpt[] = [
  { value: "claude-opus-4-7",           label: "Claude Opus 4.7" },
  { value: "claude-sonnet-4-6",         label: "Claude Sonnet 4.6 (권장)" },
  { value: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5 (빠름)" },
];

const OPENAI_MODELS: ModelOpt[] = [
  { value: "gpt-4o",       label: "GPT-4o (권장)" },
  { value: "gpt-4o-mini",  label: "GPT-4o mini (빠름)" },
  { value: "gpt-4-turbo",  label: "GPT-4 Turbo" },
  { value: "o1",           label: "o1 (추론)" },
  { value: "o1-mini",      label: "o1-mini" },
];

type Tab = "admin" | "user";

export default function AuthModal({ authState, onAuthenticate }: Props) {
  const [tab, setTab] = useState<Tab>(authState.hasAdmin ? "admin" : "user");

  // ── Admin tab state ──────────────────────────────────────────────────
  const [adminPassword, setAdminPassword] = useState("");

  // ── User tab state ───────────────────────────────────────────────────
  const [provider, setProvider] = useState<LLMProvider>("anthropic");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(CLAUDE_MODELS[0].value);
  const [customModel, setCustomModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://localhost:8080/v1");
  const [isCustomModel, setIsCustomModel] = useState(false);

  // Dynamic model catalog — populated by /api/models when the user asks for it.
  const [dynamicModels, setDynamicModels] = useState<ModelOpt[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [isLiveList, setIsLiveList] = useState(false);

  // When provider changes, reset model to the first preset and clear dynamic list
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
    provider === "openai"    ? OPENAI_MODELS : [];

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

  // ── Submit handlers ──────────────────────────────────────────────────

  const handleAdminLogin = useCallback(() => {
    if (!adminPassword.trim()) return;
    onAuthenticate({ type: "admin", password: adminPassword });
  }, [adminPassword, onAuthenticate]);

  const handleUserLogin = useCallback(() => {
    if (!effectiveModel.trim()) return;
    if (provider !== "vllm" && !apiKey.trim()) return;
    if (provider === "vllm" && !baseUrl.trim()) return;
    onAuthenticate({
      type: "user",
      provider,
      api_key: apiKey.trim(),
      model: effectiveModel.trim(),
      base_url: provider === "vllm" ? baseUrl.trim() : undefined,
    });
  }, [provider, apiKey, effectiveModel, baseUrl, onAuthenticate]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      if (tab === "admin") handleAdminLogin();
      else handleUserLogin();
    }
  }, [tab, handleAdminLogin, handleUserLogin]);

  // ── Provider badge component ─────────────────────────────────────────

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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div
        className="w-full max-w-md rounded-2xl border border-fuchsia-500/30 bg-slate-950/95 p-6 shadow-2xl shadow-fuchsia-500/10"
        onKeyDown={handleKeyDown}
      >
        {/* Header */}
        <div className="mb-6 text-center">
          <h2 className="text-2xl font-bold font-mono text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 to-cyan-400">
            ~* Autonoma *~
          </h2>
          <p className="mt-1 text-xs text-white/40 font-mono">
            Self-Organizing Agent Swarm
          </p>
        </div>

        {/* Tab switcher */}
        {authState.hasAdmin && (
          <div className="mb-5 flex gap-1 rounded-lg border border-white/10 bg-white/5 p-1">
            <button
              type="button"
              onClick={() => setTab("admin")}
              className={`flex-1 rounded-md py-1.5 text-xs font-mono font-bold transition-all ${
                tab === "admin"
                  ? "bg-fuchsia-600/60 text-white shadow"
                  : "text-white/40 hover:text-white/60"
              }`}
            >
              관리자 로그인
            </button>
            <button
              type="button"
              onClick={() => setTab("user")}
              className={`flex-1 rounded-md py-1.5 text-xs font-mono font-bold transition-all ${
                tab === "user"
                  ? "bg-cyan-600/60 text-white shadow"
                  : "text-white/40 hover:text-white/60"
              }`}
            >
              내 API 키 사용
            </button>
          </div>
        )}

        {/* ── Admin tab ── */}
        {tab === "admin" && (
          <div className="flex flex-col gap-4">
            {authState.serverProvider && (
              <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs font-mono text-amber-300/80">
                서버 설정: {authState.serverProvider} / {authState.serverModel}
              </div>
            )}
            <div>
              <label className="mb-1.5 block text-xs font-mono text-white/50">
                관리자 비밀번호
              </label>
              <input
                type="password"
                value={adminPassword}
                onChange={(e) => setAdminPassword(e.target.value)}
                placeholder="••••••••"
                autoFocus
                className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-fuchsia-500/60 transition-colors"
              />
            </div>
            <button
              type="button"
              onClick={handleAdminLogin}
              disabled={!adminPassword.trim()}
              className="w-full rounded-xl bg-gradient-to-r from-fuchsia-600 to-purple-600 py-3 text-sm font-bold font-mono text-white hover:from-fuchsia-500 hover:to-purple-500 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            >
              관리자로 로그인
            </button>
          </div>
        )}

        {/* ── User / API key tab ── */}
        {tab === "user" && (
          <div className="flex flex-col gap-4">
            {/* Provider selector */}
            <div>
              <label className="mb-1.5 block text-xs font-mono text-white/50">
                LLM 프로바이더
              </label>
              <div className="flex gap-2">
                <ProviderBtn value="anthropic" icon="🟣" label="Claude" />
                <ProviderBtn value="openai"    icon="🟢" label="OpenAI" />
                <ProviderBtn value="vllm"      icon="⚡" label="vLLM" />
              </div>
            </div>

            {/* API key (not needed for some vLLM setups) */}
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

            {/* vLLM base URL */}
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
                {/* Optional API key for secured vLLM */}
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

            {/* Model selector */}
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
                    <option key={m.value} value={m.value}>{m.label}</option>
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
              onClick={handleUserLogin}
              disabled={
                !effectiveModel.trim() ||
                (provider !== "vllm" && !apiKey.trim()) ||
                (provider === "vllm" && !baseUrl.trim())
              }
              className="w-full rounded-xl bg-gradient-to-r from-cyan-600 to-fuchsia-600 py-3 text-sm font-bold font-mono text-white hover:from-cyan-500 hover:to-fuchsia-500 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            >
              연결
            </button>
          </div>
        )}

        {/* Error message */}
        {authState.error && (
          <div className="mt-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs font-mono text-red-300">
            {authState.error}
          </div>
        )}

        {/* Footer */}
        <p className="mt-5 text-center text-[10px] font-mono text-white/20">
          API 키는 세션 동안만 유지되며 서버에 저장되지 않습니다.
        </p>
      </div>
    </div>
  );
}

"use client";

import { useCallback, useMemo, useState } from "react";
import {
  useAuth,
  type AuthUser,
  type GuestProvider,
  type GuestReason,
  type LoginReason,
  type SignupReason,
} from "@/hooks/useAuth";
import { useModalA11y } from "@/hooks/useModalA11y";
import type { AuthState, UserCredentials } from "@/lib/types";
import { STRINGS } from "@/lib/strings";

// ── Props ─────────────────────────────────────────────────────────────────
//
// The modal is the single source of auth truth in the UI. It exposes the
// optional legacy `authState` / `onAuthenticate` pair so the old WebSocket
// admin-password login remains reachable while we migrate to HTTP cookie
// auth. `onAuthSuccess` fires once after a successful HTTP login so the
// parent page can re-render without waiting for a polling refresh.

interface Props {
  /** Legacy WS admin-password flow. When provided, the modal shows a
   *  "Legacy admin login" toggle at the bottom. */
  authState?: AuthState;
  onAuthenticate?: (credentials: UserCredentials) => void;
  /** Fires after `useAuth.login` resolves successfully. */
  onAuthSuccess?: (user: AuthUser) => void;
}

type Tab = "login" | "signup" | "guest" | "legacy";

// Client-side username rule hints. The server is authoritative; these just
// keep submit disabled until the input is at least plausibly valid so we
// don't round-trip obvious garbage.
const USERNAME_RE = /^[a-z0-9_-]{3,32}$/;
const MIN_PASSWORD_LEN = 8;

// Reasonable defaults the user can override. Picked per provider so the
// "fill in API key, hit enter" path works without forcing the visitor to
// know the exact model id.
const GUEST_DEFAULT_MODEL: Record<GuestProvider, string> = {
  anthropic: "claude-sonnet-4-6",
  openai: "gpt-4o-mini",
  vllm: "",
};

function reasonToMessage(
  reason: LoginReason | SignupReason | GuestReason,
): string {
  switch (reason) {
    case "bad_credentials":
    case "invalid_credentials":
      return STRINGS.auth.invalidCredentials;
    case "not_active":
      return STRINGS.auth.notActivated;
    case "username_taken":
      return STRINGS.auth.usernameTaken;
    case "invalid":
    case "invalid_input":
      return STRINGS.auth.invalidInput;
    case "network":
    default:
      return STRINGS.auth.networkError;
  }
}

export default function AuthModal({
  authState,
  onAuthenticate,
  onAuthSuccess,
}: Props) {
  const { login, signup, guestLogin } = useAuth();
  const dialogRef = useModalA11y<HTMLDivElement>();

  const [tab, setTab] = useState<Tab>("login");

  // ── Login tab ────────────────────────────────────────────────────────
  const [loginUsername, setLoginUsername] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loginSubmitting, setLoginSubmitting] = useState(false);

  // ── Signup tab ───────────────────────────────────────────────────────
  const [signupUsername, setSignupUsername] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [signupConfirm, setSignupConfirm] = useState("");
  const [signupError, setSignupError] = useState<string | null>(null);
  const [signupSubmitting, setSignupSubmitting] = useState(false);
  const [signupDone, setSignupDone] = useState(false);

  // ── Guest tab ────────────────────────────────────────────────────────
  // Lets a visitor try the app with their own LLM API key — no signup,
  // no admin approval. The key never leaves the browser session: the
  // backend issues a guest cookie, and the WS reads the key out of
  // sessionStorage on its first ``auth.status`` event.
  const [guestProvider, setGuestProvider] = useState<GuestProvider>("anthropic");
  const [guestApiKey, setGuestApiKey] = useState("");
  const [guestModel, setGuestModel] = useState(GUEST_DEFAULT_MODEL.anthropic);
  const [guestBaseUrl, setGuestBaseUrl] = useState("");
  const [guestError, setGuestError] = useState<string | null>(null);
  const [guestSubmitting, setGuestSubmitting] = useState(false);

  // ── Legacy admin tab ─────────────────────────────────────────────────
  const [adminPassword, setAdminPassword] = useState("");

  const usernameValid = useMemo(
    () => USERNAME_RE.test(signupUsername),
    [signupUsername],
  );
  const passwordsMatch = useMemo(
    () => signupPassword.length > 0 && signupPassword === signupConfirm,
    [signupPassword, signupConfirm],
  );
  const signupReady = useMemo(
    () =>
      usernameValid &&
      signupPassword.length >= MIN_PASSWORD_LEN &&
      passwordsMatch,
    [usernameValid, signupPassword, passwordsMatch],
  );

  // ── Handlers ─────────────────────────────────────────────────────────

  const handleLogin = useCallback(async () => {
    if (!loginUsername.trim() || !loginPassword) return;
    setLoginError(null);
    setLoginSubmitting(true);
    try {
      const result = await login(loginUsername.trim(), loginPassword);
      if (result.ok) {
        setLoginPassword("");
        onAuthSuccess?.(result.user);
      } else {
        setLoginError(reasonToMessage(result.reason));
      }
    } finally {
      setLoginSubmitting(false);
    }
  }, [login, loginUsername, loginPassword, onAuthSuccess]);

  const handleSignup = useCallback(async () => {
    if (!signupReady) return;
    setSignupError(null);
    setSignupSubmitting(true);
    try {
      const result = await signup(signupUsername.trim(), signupPassword);
      if (result.ok) {
        setSignupDone(true);
        // Scrub secrets from memory once the request is done.
        setSignupPassword("");
        setSignupConfirm("");
      } else {
        setSignupError(reasonToMessage(result.reason));
      }
    } finally {
      setSignupSubmitting(false);
    }
  }, [signup, signupReady, signupUsername, signupPassword]);

  const handleLegacyAdminLogin = useCallback(() => {
    if (!adminPassword.trim() || !onAuthenticate) return;
    onAuthenticate({ type: "admin", password: adminPassword });
  }, [adminPassword, onAuthenticate]);

  const guestReady = useMemo(() => {
    if (!guestModel.trim()) return false;
    if (guestProvider === "vllm") return guestBaseUrl.trim().length > 0;
    return guestApiKey.trim().length > 0;
  }, [guestProvider, guestApiKey, guestModel, guestBaseUrl]);

  const handleGuestLogin = useCallback(async () => {
    if (!guestReady) return;
    setGuestError(null);
    setGuestSubmitting(true);
    try {
      const result = await guestLogin({
        provider: guestProvider,
        apiKey: guestApiKey,
        model: guestModel,
        baseUrl: guestBaseUrl,
      });
      if (result.ok) {
        // Scrub the key from the controlled input — it's been handed
        // off to sessionStorage for the WS replay; keeping it in React
        // state would let it survive re-renders unnecessarily.
        setGuestApiKey("");
        onAuthSuccess?.(result.user);
      } else {
        // Prefer the server's localized message ("API 키가 올바르지
        // 않거나 권한이 없습니다.", "프로바이더 응답 시간 초과", etc.)
        // because it carries the actual diagnostic; the generic
        // ``reasonToMessage`` copy is only a fallback when the server
        // didn't supply one (e.g. fetch threw before reaching it).
        setGuestError(result.message ?? reasonToMessage(result.reason));
      }
    } finally {
      setGuestSubmitting(false);
    }
  }, [
    guestReady,
    guestLogin,
    guestProvider,
    guestApiKey,
    guestModel,
    guestBaseUrl,
    onAuthSuccess,
  ]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key !== "Enter") return;
      if (tab === "login") void handleLogin();
      else if (tab === "signup") void handleSignup();
      else if (tab === "guest") void handleGuestLogin();
      else if (tab === "legacy") handleLegacyAdminLogin();
    },
    [tab, handleLogin, handleSignup, handleGuestLogin, handleLegacyAdminLogin],
  );

  // ── Render ───────────────────────────────────────────────────────────

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-modal-title"
        className="w-full max-w-md rounded-2xl border border-fuchsia-500/30 bg-slate-950/95 p-6 shadow-2xl shadow-fuchsia-500/10"
        onKeyDown={handleKeyDown}
      >
        {/* Header */}
        <div className="mb-6 text-center">
          <h2
            id="auth-modal-title"
            className="text-2xl font-bold font-mono text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 to-cyan-400"
          >
            ~* Autonoma *~
          </h2>
          <p className="mt-1 text-xs text-white/40 font-mono">
            Self-Organizing Agent Swarm
          </p>
        </div>

        {/* Post-signup "waiting for approval" takeover — disables everything
            else so the user doesn't keep hammering the modal while pending. */}
        {signupDone ? (
          <div className="flex flex-col gap-4">
            <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-5 text-center">
              <div className="text-2xl mb-2">⏳</div>
              <p className="text-sm font-mono font-bold text-emerald-300">
                관리자 승인 대기 중
              </p>
              <p className="mt-2 text-xs font-mono text-emerald-200/70 leading-relaxed">
                회원가입이 접수되었습니다.
                <br />
                관리자가 계정을 승인하면 로그인할 수 있습니다.
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                setSignupDone(false);
                setTab("login");
                setSignupUsername("");
                setSignupError(null);
              }}
              className="w-full rounded-xl border border-white/10 bg-white/5 py-2.5 text-xs font-mono text-white/60 hover:bg-white/10 transition-all"
            >
              로그인 화면으로
            </button>
          </div>
        ) : (
          <>
            {/* Tab switcher (login / signup / guest) */}
            {tab !== "legacy" && (
              <div className="mb-5 flex gap-1 rounded-lg border border-white/10 bg-white/5 p-1">
                <button
                  type="button"
                  onClick={() => setTab("login")}
                  className={`flex-1 rounded-md py-1.5 text-xs font-mono font-bold transition-all ${
                    tab === "login"
                      ? "bg-fuchsia-600/60 text-white shadow"
                      : "text-white/40 hover:text-white/60"
                  }`}
                >
                  로그인
                </button>
                <button
                  type="button"
                  onClick={() => setTab("signup")}
                  className={`flex-1 rounded-md py-1.5 text-xs font-mono font-bold transition-all ${
                    tab === "signup"
                      ? "bg-cyan-600/60 text-white shadow"
                      : "text-white/40 hover:text-white/60"
                  }`}
                >
                  회원가입
                </button>
                <button
                  type="button"
                  onClick={() => setTab("guest")}
                  className={`flex-1 rounded-md py-1.5 text-xs font-mono font-bold transition-all ${
                    tab === "guest"
                      ? "bg-emerald-600/60 text-white shadow"
                      : "text-white/40 hover:text-white/60"
                  }`}
                >
                  게스트
                </button>
              </div>
            )}

            {/* ── Login tab ── */}
            {tab === "login" && (
              <div className="flex flex-col gap-4">
                <div>
                  <label className="mb-1.5 block text-xs font-mono text-white/50">
                    사용자 이름
                  </label>
                  <input
                    type="text"
                    autoComplete="username"
                    value={loginUsername}
                    onChange={(e) => setLoginUsername(e.target.value)}
                    placeholder="username"
                    autoFocus
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-fuchsia-500/60 transition-colors"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-mono text-white/50">
                    비밀번호
                  </label>
                  <input
                    type="password"
                    autoComplete="current-password"
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-fuchsia-500/60 transition-colors"
                  />
                </div>
                <button
                  type="button"
                  onClick={handleLogin}
                  disabled={
                    loginSubmitting ||
                    !loginUsername.trim() ||
                    !loginPassword
                  }
                  className="w-full rounded-xl bg-gradient-to-r from-fuchsia-600 to-purple-600 py-3 text-sm font-bold font-mono text-white hover:from-fuchsia-500 hover:to-purple-500 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  {loginSubmitting ? "로그인 중..." : "로그인"}
                </button>
                {loginError && (
                  <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs font-mono text-red-300">
                    {loginError}
                  </div>
                )}
              </div>
            )}

            {/* ── Signup tab ── */}
            {tab === "signup" && (
              <div className="flex flex-col gap-4">
                <div>
                  <label className="mb-1.5 block text-xs font-mono text-white/50">
                    사용자 이름
                  </label>
                  <input
                    type="text"
                    autoComplete="username"
                    value={signupUsername}
                    onChange={(e) =>
                      setSignupUsername(e.target.value.toLowerCase())
                    }
                    placeholder="username"
                    autoFocus
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-cyan-500/60 transition-colors"
                  />
                  <p
                    className={`mt-1 text-[10px] font-mono ${
                      signupUsername.length === 0
                        ? "text-white/30"
                        : usernameValid
                          ? "text-emerald-300/70"
                          : "text-amber-300/80"
                    }`}
                  >
                    3–32자 · 소문자/숫자/밑줄/하이픈
                  </p>
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-mono text-white/50">
                    비밀번호
                  </label>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={signupPassword}
                    onChange={(e) => setSignupPassword(e.target.value)}
                    placeholder="••••••••"
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-cyan-500/60 transition-colors"
                  />
                  <p
                    className={`mt-1 text-[10px] font-mono ${
                      signupPassword.length === 0
                        ? "text-white/30"
                        : signupPassword.length >= MIN_PASSWORD_LEN
                          ? "text-emerald-300/70"
                          : "text-amber-300/80"
                    }`}
                  >
                    최소 {MIN_PASSWORD_LEN}자 이상
                  </p>
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-mono text-white/50">
                    비밀번호 확인
                  </label>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={signupConfirm}
                    onChange={(e) => setSignupConfirm(e.target.value)}
                    placeholder="••••••••"
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-cyan-500/60 transition-colors"
                  />
                  {signupConfirm.length > 0 && !passwordsMatch && (
                    <p className="mt-1 text-[10px] font-mono text-amber-300/80">
                      비밀번호가 일치하지 않습니다.
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={handleSignup}
                  disabled={signupSubmitting || !signupReady}
                  className="w-full rounded-xl bg-gradient-to-r from-cyan-600 to-fuchsia-600 py-3 text-sm font-bold font-mono text-white hover:from-cyan-500 hover:to-fuchsia-500 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  {signupSubmitting ? "등록 중..." : "회원가입"}
                </button>
                {signupError && (
                  <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs font-mono text-red-300">
                    {signupError}
                  </div>
                )}
                <p className="text-center text-[10px] font-mono text-white/30">
                  신규 계정은 관리자 승인 후 활성화됩니다.
                </p>
              </div>
            )}

            {/* ── Guest tab ── */}
            {tab === "guest" && (
              <div className="flex flex-col gap-3">
                <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-[10px] font-mono text-emerald-200/80 leading-relaxed">
                  본인의 LLM API 키로 즉시 체험할 수 있습니다.
                  <br />
                  키는 서버에 저장되지 않으며, 브라우저 세션 종료 시 사라집니다.
                </div>
                <div>
                  <label className="mb-1.5 block text-xs font-mono text-white/50">
                    프로바이더
                  </label>
                  <div className="flex gap-1 rounded-lg border border-white/10 bg-white/5 p-1">
                    {(["anthropic", "openai", "vllm"] as GuestProvider[]).map(
                      (p) => (
                        <button
                          key={p}
                          type="button"
                          onClick={() => {
                            setGuestProvider(p);
                            setGuestModel(GUEST_DEFAULT_MODEL[p]);
                          }}
                          className={`flex-1 rounded-md py-1.5 text-[11px] font-mono font-bold transition-all ${
                            guestProvider === p
                              ? "bg-emerald-600/60 text-white shadow"
                              : "text-white/40 hover:text-white/60"
                          }`}
                        >
                          {p}
                        </button>
                      ),
                    )}
                  </div>
                </div>
                {guestProvider !== "vllm" && (
                  <div>
                    <label className="mb-1.5 block text-xs font-mono text-white/50">
                      API 키
                    </label>
                    <input
                      type="password"
                      autoComplete="off"
                      value={guestApiKey}
                      onChange={(e) => setGuestApiKey(e.target.value)}
                      placeholder={
                        guestProvider === "anthropic"
                          ? "sk-ant-..."
                          : "sk-..."
                      }
                      autoFocus
                      className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-emerald-500/60 transition-colors"
                    />
                  </div>
                )}
                {guestProvider === "vllm" && (
                  <div>
                    <label className="mb-1.5 block text-xs font-mono text-white/50">
                      vLLM 서버 URL
                    </label>
                    <input
                      type="text"
                      autoComplete="off"
                      value={guestBaseUrl}
                      onChange={(e) => setGuestBaseUrl(e.target.value)}
                      placeholder="http://localhost:8000"
                      autoFocus
                      className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-emerald-500/60 transition-colors"
                    />
                  </div>
                )}
                <div>
                  <label className="mb-1.5 block text-xs font-mono text-white/50">
                    모델
                  </label>
                  <input
                    type="text"
                    autoComplete="off"
                    value={guestModel}
                    onChange={(e) => setGuestModel(e.target.value)}
                    placeholder={
                      guestProvider === "vllm"
                        ? "model-name"
                        : GUEST_DEFAULT_MODEL[guestProvider]
                    }
                    className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2.5 text-sm font-mono text-white placeholder:text-white/20 outline-none focus:border-emerald-500/60 transition-colors"
                  />
                </div>
                <button
                  type="button"
                  onClick={handleGuestLogin}
                  disabled={guestSubmitting || !guestReady}
                  className="w-full rounded-xl bg-gradient-to-r from-emerald-600 to-cyan-600 py-3 text-sm font-bold font-mono text-white hover:from-emerald-500 hover:to-cyan-500 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  {guestSubmitting ? "API 키 검증 중..." : "게스트로 시작"}
                </button>
                {guestError && (
                  <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs font-mono text-red-300">
                    {guestError}
                  </div>
                )}
              </div>
            )}

            {/* ── Legacy admin tab ── */}
            {tab === "legacy" && (
              <div className="flex flex-col gap-4">
                {authState?.serverProvider && (
                  <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-xs font-mono text-amber-300/80">
                    서버 설정: {authState.serverProvider} /{" "}
                    {authState.serverModel}
                  </div>
                )}
                <div>
                  <label className="mb-1.5 block text-xs font-mono text-white/50">
                    관리자 비밀번호 (레거시)
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
                  onClick={handleLegacyAdminLogin}
                  disabled={!adminPassword.trim() || !onAuthenticate}
                  className="w-full rounded-xl bg-gradient-to-r from-fuchsia-600 to-purple-600 py-3 text-sm font-bold font-mono text-white hover:from-fuchsia-500 hover:to-purple-500 transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  관리자로 로그인
                </button>
                {authState?.error && (
                  <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs font-mono text-red-300">
                    {authState.error}
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => setTab("login")}
                  className="text-[10px] font-mono text-white/40 hover:text-white/60 transition-colors"
                >
                  ← 일반 로그인으로 돌아가기
                </button>
              </div>
            )}

            {/* Legacy link — only show when consumer wired it up. */}
            {tab !== "legacy" && onAuthenticate && (
              <div className="mt-5 text-center">
                <button
                  type="button"
                  onClick={() => setTab("legacy")}
                  className="text-[10px] font-mono text-white/30 hover:text-white/60 transition-colors underline underline-offset-2"
                >
                  Legacy admin login
                </button>
              </div>
            )}
          </>
        )}

        {/* Footer */}
        <p className="mt-5 text-center text-[10px] font-mono text-white/20">
          세션 쿠키는 로그아웃 시 만료됩니다.
        </p>
      </div>
    </div>
  );
}

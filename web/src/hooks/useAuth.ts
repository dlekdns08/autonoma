"use client";

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";

// ── User account model ────────────────────────────────────────────────────
// Mirrors the shape returned by GET /api/auth/me and POST /api/auth/login.

export type UserRole = "admin" | "user";
export type UserStatus = "pending" | "active" | "disabled" | "denied";

export interface AuthUser {
  id: number | string;
  username: string;
  role: UserRole;
  status: UserStatus;
  created_at: string;
  updated_at: string;
  /** Synthetic user backed by the guest cookie (no DB row). */
  is_guest?: boolean;
}

// ── Result discriminated unions ───────────────────────────────────────────

export type LoginReason = "bad_credentials" | "not_active" | "network";
export type SignupReason = "username_taken" | "invalid" | "network";
export type GuestReason =
  | "invalid"
  | "invalid_credentials"
  | "invalid_input"
  | "network";

export type LoginResult =
  | { ok: true; user: AuthUser }
  | { ok: false; reason: LoginReason };

export type SignupResult =
  | { ok: true }
  | { ok: false; reason: SignupReason };

export type GuestLoginResult =
  | { ok: true; user: AuthUser }
  // ``message`` is populated when the server returned a Korean error
  // string the modal should surface verbatim (e.g. "API 키가 올바르지
  // 않거나 권한이 없습니다."). Local validation paths leave it null so
  // the modal falls back to its generic copy.
  | { ok: false; reason: GuestReason; message?: string };

/** LLM provider name accepted by the WebSocket ``type=user`` handler. */
export type GuestProvider = "anthropic" | "openai" | "vllm";

export interface GuestCredentials {
  provider: GuestProvider;
  apiKey: string;
  model: string;
  /** Required iff provider === "vllm". */
  baseUrl?: string;
}

export interface UseAuthReturn {
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<LoginResult>;
  signup: (username: string, password: string) => Promise<SignupResult>;
  guestLogin: (creds: GuestCredentials) => Promise<GuestLoginResult>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

// ── Guest credential bridge to useSwarm ───────────────────────────────────
// useSwarm reads this sessionStorage key when the WS sends ``auth.status``
// and auto-replays the stored ``type=user`` authenticate command. By
// writing it here BEFORE the page transitions to <Dashboard /> (which is
// where useSwarm mounts and opens the socket), the guest's API key flows
// straight into the WS without requiring any extra UI step.
const SESSION_KEY = "autonoma_auth";

const JSON_HEADERS: HeadersInit = { "Content-Type": "application/json" };

// ── Context ───────────────────────────────────────────────────────────────
// The whole app shares ONE auth state. Before this, each useAuth() call
// created its own state tree — a successful login inside AuthModal didn't
// notify the gate in page.tsx, leaving the user stuck on the login screen
// after a 200 response. Context collapses all call sites onto one state.

const AuthCtx = createContext<UseAuthReturn | null>(null);

function useAuthStore(): UseAuthReturn {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/auth/me`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (res.status === 200) {
        const data = (await res.json()) as { user: AuthUser };
        setUser(data.user ?? null);
      } else if (res.status === 401) {
        setUser(null);
        setError("Session expired. Please log in again.");
      } else {
        setUser(null);
        setError(`unexpected status ${res.status}`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (username: string, password: string): Promise<LoginResult> => {
      setError(null);
      try {
        const res = await fetch(`${API_BASE_URL}/api/auth/login`, {
          method: "POST",
          credentials: "include",
          headers: JSON_HEADERS,
          body: JSON.stringify({ username, password }),
        });
        if (res.status === 200) {
          const data = (await res.json()) as { user: AuthUser };
          setUser(data.user ?? null);
          return { ok: true, user: data.user };
        }
        if (res.status === 401) return { ok: false, reason: "bad_credentials" };
        if (res.status === 403) return { ok: false, reason: "not_active" };
        return { ok: false, reason: "network" };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  const signup = useCallback(
    async (username: string, password: string): Promise<SignupResult> => {
      setError(null);
      try {
        const res = await fetch(`${API_BASE_URL}/api/auth/signup`, {
          method: "POST",
          credentials: "include",
          headers: JSON_HEADERS,
          body: JSON.stringify({ username, password }),
        });
        if (res.status === 201) return { ok: true };
        if (res.status === 409) return { ok: false, reason: "username_taken" };
        if (res.status === 400) return { ok: false, reason: "invalid" };
        return { ok: false, reason: "network" };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  const guestLogin = useCallback(
    async (creds: GuestCredentials): Promise<GuestLoginResult> => {
      // Client-side input gate. The WS server validates again, but
      // surfacing the error here avoids round-tripping obviously empty
      // payloads and keeps the modal's error UX local.
      const provider = creds.provider;
      const apiKey = creds.apiKey.trim();
      const model = creds.model.trim();
      const baseUrl = (creds.baseUrl ?? "").trim();
      if (!model) return { ok: false, reason: "invalid" };
      if (provider !== "vllm" && !apiKey) return { ok: false, reason: "invalid" };
      if (provider === "vllm" && !baseUrl) return { ok: false, reason: "invalid" };

      setError(null);
      try {
        const res = await fetch(`${API_BASE_URL}/api/auth/guest`, {
          method: "POST",
          credentials: "include",
          headers: JSON_HEADERS,
          // The backend pre-validates the key against the provider's
          // /v1/models endpoint before issuing the cookie, so we send
          // the same payload the WS would otherwise receive.
          body: JSON.stringify({
            provider,
            api_key: apiKey,
            model,
            ...(provider === "vllm" ? { base_url: baseUrl } : {}),
          }),
        });

        if (res.status !== 200) {
          // Backend returns ``{ detail: { reason, message } }`` on 4xx
          // (FastAPI HTTPException convention). We surface the message
          // verbatim so provider-specific errors ("API 키가 올바르지
          // 않거나 권한이 없습니다.") reach the user unfiltered.
          let reason: GuestReason = "network";
          let message: string | undefined;
          try {
            const body = (await res.json()) as {
              detail?: { reason?: string; message?: string };
            };
            const r = body.detail?.reason;
            if (r === "invalid_input") reason = "invalid_input";
            else if (r === "invalid_credentials") reason = "invalid_credentials";
            message = body.detail?.message;
          } catch {
            /* non-JSON error body — fall through to network reason */
          }
          return { ok: false, reason, message };
        }

        const data = (await res.json()) as { user: AuthUser };

        // Stash the WS credentials BEFORE flipping app state to
        // <Dashboard/> — useSwarm reads this on its first auth.status
        // event. ``type: "user"`` is what the backend's WS authenticate
        // handler at api.py:2218 expects.
        if (typeof window !== "undefined") {
          window.sessionStorage.setItem(
            SESSION_KEY,
            JSON.stringify({
              type: "user",
              provider,
              api_key: apiKey,
              model,
              ...(provider === "vllm" ? { base_url: baseUrl } : {}),
            }),
          );
        }

        setUser(data.user ?? null);
        return { ok: true, user: data.user };
      } catch {
        return { ok: false, reason: "network" };
      }
    },
    [],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      await fetch(`${API_BASE_URL}/api/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
    } catch {
      // Network-level failures shouldn't trap the user in a logged-in UI;
      // the server cookie will expire on its own.
    } finally {
      // Drop the guest WS credential bridge too — otherwise a subsequent
      // signed-up login would carry the previous guest's API key into
      // the new WS session.
      if (typeof window !== "undefined") {
        window.sessionStorage.removeItem(SESSION_KEY);
      }
      setUser(null);
    }
  }, []);

  return { user, loading, error, login, signup, guestLogin, logout, refresh };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const value = useAuthStore();
  return createElement(AuthCtx.Provider, { value }, children);
}

export function useAuth(): UseAuthReturn {
  const ctx = useContext(AuthCtx);
  if (!ctx) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}

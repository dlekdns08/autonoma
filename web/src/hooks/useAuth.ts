"use client";

import { useCallback, useEffect, useState } from "react";
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
}

// ── Result discriminated unions ───────────────────────────────────────────

export type LoginReason = "bad_credentials" | "not_active" | "network";
export type SignupReason = "username_taken" | "invalid" | "network";

export type LoginResult =
  | { ok: true; user: AuthUser }
  | { ok: false; reason: LoginReason };

export type SignupResult =
  | { ok: true }
  | { ok: false; reason: SignupReason };

// ── Hook return type ──────────────────────────────────────────────────────

export interface UseAuthReturn {
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<LoginResult>;
  signup: (username: string, password: string) => Promise<SignupResult>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

// Shared fetch defaults — cookie sessions require credentials: "include",
// and the API expects JSON bodies. Keeping them here avoids drift across
// the various endpoints.
const JSON_HEADERS: HeadersInit = { "Content-Type": "application/json" };

/**
 * useAuth — session-aware auth hook backed by cookie auth at `API_BASE_URL`.
 *
 * On mount it calls `GET /api/auth/me` once to hydrate. The returned
 * `login`/`signup` functions are discriminated so callers can render
 * specific error messages without parsing free-form strings.
 */
export function useAuth(): UseAuthReturn {
  const [user, setUser] = useState<AuthUser | null>(null);
  // Starts `true` so consumers can render a loading screen during the
  // initial hydrate. `refresh()` also flips it.
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
        // Not logged in — this is a normal state, not an error.
        setUser(null);
      } else {
        setUser(null);
        setError(`unexpected status ${res.status}`);
      }
    } catch (err) {
      // Network-level failures shouldn't wipe an existing user unless
      // the browser is truly offline; the caller can retry via refresh().
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // Hydrate once on mount. We deliberately avoid refetching on every
  // dependency change — login/signup/logout handlers update state
  // optimistically and explicitly.
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
        if (res.status === 401) {
          return { ok: false, reason: "bad_credentials" };
        }
        if (res.status === 403) {
          // Server signals the account exists but is pending/disabled/denied.
          return { ok: false, reason: "not_active" };
        }
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
        if (res.status === 201) {
          // Successful signup does NOT log the user in — they still need
          // admin approval. Leave `user` null.
          return { ok: true };
        }
        if (res.status === 409) {
          return { ok: false, reason: "username_taken" };
        }
        if (res.status === 400) {
          return { ok: false, reason: "invalid" };
        }
        return { ok: false, reason: "network" };
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
      // Swallow network errors — the local state flip below still lets
      // the user out of the UI; the cookie will expire server-side.
    } finally {
      setUser(null);
    }
  }, []);

  return { user, loading, error, login, signup, logout, refresh };
}

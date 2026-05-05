/**
 * Behaviour tests for the ``useAuth`` hook.
 *
 * The hook talks to ``/api/auth/{me,login,signup,guest,logout}``. We
 * mock ``global.fetch`` to assert on:
 *   - HTTP status mapping (401 -> ``bad_credentials``, 403 ->
 *     ``not_active``, 409 -> ``username_taken``, …) — this is the
 *     contract every login UI surface depends on.
 *   - Client-side input validation in ``guestLogin``.
 *   - The sessionStorage handshake with ``useSwarm`` — the modal writes
 *     the WS credentials before the page navigates, and ``logout``
 *     must drain them.
 *
 * Tests render the hook inside ``<AuthProvider>`` because the public
 * ``useAuth()`` throws otherwise.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { AuthProvider, useAuth } from "@/hooks/useAuth";

// ── fetch double ──────────────────────────────────────────────────────

const mockFetch = vi.fn();

beforeEach(() => {
  globalThis.fetch = mockFetch as unknown as typeof fetch;
  // Default: ``/api/auth/me`` (called on mount) returns 401 so the
  // hook settles into the logged-out state quickly.
  mockFetch.mockImplementation(async (url: string | URL) => {
    if (String(url).endsWith("/api/auth/me")) {
      return new Response(null, { status: 401 });
    }
    return new Response(null, { status: 500 });
  });
});

afterEach(() => {
  mockFetch.mockReset();
});

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}

async function mountAuth() {
  const { result } = renderHook(() => useAuth(), { wrapper });
  // Wait for the initial ``/api/auth/me`` round-trip to settle.
  await waitFor(() => expect(result.current.loading).toBe(false));
  return result;
}

// ── login ─────────────────────────────────────────────────────────────

describe("useAuth.login", () => {
  it("returns the user on 200", async () => {
    const result = await mountAuth();

    const fakeUser = {
      id: 1,
      username: "ada",
      role: "user",
      status: "active",
      created_at: "x",
      updated_at: "x",
    };
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ user: fakeUser }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    let outcome: Awaited<ReturnType<typeof result.current.login>> | undefined;
    await act(async () => {
      outcome = await result.current.login("ada", "pw");
    });

    expect(outcome).toEqual({ ok: true, user: fakeUser });
    expect(result.current.user).toEqual(fakeUser);
  });

  it("maps 401 to bad_credentials", async () => {
    const result = await mountAuth();
    mockFetch.mockResolvedValueOnce(new Response(null, { status: 401 }));

    let outcome: Awaited<ReturnType<typeof result.current.login>> | undefined;
    await act(async () => {
      outcome = await result.current.login("ada", "wrong");
    });

    expect(outcome).toEqual({ ok: false, reason: "bad_credentials" });
    expect(result.current.user).toBeNull();
  });

  it("maps 403 to not_active", async () => {
    const result = await mountAuth();
    mockFetch.mockResolvedValueOnce(new Response(null, { status: 403 }));

    let outcome: Awaited<ReturnType<typeof result.current.login>> | undefined;
    await act(async () => {
      outcome = await result.current.login("ada", "pw");
    });

    expect(outcome).toEqual({ ok: false, reason: "not_active" });
  });
});

// ── signup ────────────────────────────────────────────────────────────

describe("useAuth.signup", () => {
  it("returns ok on 201", async () => {
    const result = await mountAuth();
    mockFetch.mockResolvedValueOnce(new Response(null, { status: 201 }));

    let outcome: Awaited<ReturnType<typeof result.current.signup>> | undefined;
    await act(async () => {
      outcome = await result.current.signup("ada", "pw");
    });

    expect(outcome).toEqual({ ok: true });
  });

  it("maps 409 to username_taken", async () => {
    const result = await mountAuth();
    mockFetch.mockResolvedValueOnce(new Response(null, { status: 409 }));

    let outcome: Awaited<ReturnType<typeof result.current.signup>> | undefined;
    await act(async () => {
      outcome = await result.current.signup("dup", "pw");
    });

    expect(outcome).toEqual({ ok: false, reason: "username_taken" });
  });
});

// ── guestLogin: client-side validation ────────────────────────────────

describe("useAuth.guestLogin (client-side validation)", () => {
  it("rejects an empty model without hitting the network", async () => {
    const result = await mountAuth();
    const calls = mockFetch.mock.calls.length;

    let outcome: Awaited<
      ReturnType<typeof result.current.guestLogin>
    > | undefined;
    await act(async () => {
      outcome = await result.current.guestLogin({
        provider: "anthropic",
        apiKey: "k",
        model: "",
      });
    });

    expect(outcome).toEqual({ ok: false, reason: "invalid" });
    // Only the original ``/api/auth/me`` call should have been made.
    expect(mockFetch.mock.calls.length).toBe(calls);
  });

  it("requires baseUrl when provider=vllm", async () => {
    const result = await mountAuth();
    let outcome: Awaited<
      ReturnType<typeof result.current.guestLogin>
    > | undefined;
    await act(async () => {
      outcome = await result.current.guestLogin({
        provider: "vllm",
        apiKey: "",
        model: "Qwen/Qwen2.5",
      });
    });

    expect(outcome).toEqual({ ok: false, reason: "invalid" });
  });

  it("on success stashes WS credentials in sessionStorage", async () => {
    const result = await mountAuth();
    const fakeUser = {
      id: 2,
      username: "guest",
      role: "user",
      status: "active",
      created_at: "x",
      updated_at: "x",
      is_guest: true,
    };
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ user: fakeUser }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await act(async () => {
      await result.current.guestLogin({
        provider: "anthropic",
        apiKey: "sk-test-123",
        model: "claude-opus-4-7",
      });
    });

    const stashed = window.sessionStorage.getItem("autonoma_auth");
    expect(stashed).not.toBeNull();
    const parsed = JSON.parse(stashed as string);
    expect(parsed.type).toBe("user");
    expect(parsed.provider).toBe("anthropic");
    expect(parsed.api_key).toBe("sk-test-123");
    expect(parsed.model).toBe("claude-opus-4-7");
  });
});

// ── logout ────────────────────────────────────────────────────────────

describe("useAuth.logout", () => {
  it("clears the user and drops the sessionStorage credential bridge", async () => {
    // Pre-seed a user via guestLogin.
    const result = await mountAuth();
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          user: {
            id: 9,
            username: "g",
            role: "user",
            status: "active",
            created_at: "x",
            updated_at: "x",
            is_guest: true,
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    await act(async () => {
      await result.current.guestLogin({
        provider: "anthropic",
        apiKey: "k",
        model: "m",
      });
    });
    expect(window.sessionStorage.getItem("autonoma_auth")).not.toBeNull();
    expect(result.current.user).not.toBeNull();

    // Now log out.
    mockFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await act(async () => {
      await result.current.logout();
    });

    expect(result.current.user).toBeNull();
    expect(window.sessionStorage.getItem("autonoma_auth")).toBeNull();
  });
});

// ── Provider gate ─────────────────────────────────────────────────────

describe("useAuth without provider", () => {
  it("throws when called outside <AuthProvider>", () => {
    // Render a component that calls useAuth() with no provider — must throw.
    function Bad() {
      useAuth();
      return null;
    }
    // Suppress the React error overlay output for this expected throw.
    const orig = console.error;
    console.error = () => {};
    expect(() => render(<Bad />)).toThrow(/AuthProvider/);
    console.error = orig;
  });
});

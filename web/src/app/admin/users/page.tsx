"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth, type AuthUser, type UserStatus } from "@/hooks/useAuth";
import { API_BASE_URL } from "@/hooks/useSwarm";
import { STRINGS } from "@/lib/strings";

// Endpoint suffixes keyed by the action name. Each action POSTs to
// /api/admin/users/{id}/{suffix} and expects a 204.
const ACTION_ENDPOINTS = {
  approve: "approve",
  deny: "deny",
  disable: "disable",
  reactivate: "reactivate",
} as const;
type AdminAction = keyof typeof ACTION_ENDPOINTS;

function formatDate(value: string): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  // YYYY-MM-DD HH:mm, locale-independent so the admin table stays aligned.
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const STATUS_STYLES: Record<UserStatus, { bg: string; color: string; label: string }> = {
  pending:  { bg: "rgba(251,191,36,0.15)", color: "#fde68a", label: "pending"  },
  active:   { bg: "rgba(16,185,129,0.15)", color: "#6ee7b7", label: "active"   },
  disabled: { bg: "rgba(239,68,68,0.15)",  color: "#fca5a5", label: "disabled" },
  denied:   { bg: "rgba(148,163,184,0.15)", color: "#cbd5e1", label: "denied"  },
};

export default function AdminUsersPage() {
  const { user, loading: authLoading } = useAuth();

  const [users, setUsers] = useState<AuthUser[] | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  // Per-row pending action — used to disable buttons while the request
  // is in flight and to scope error messages to the affected row.
  const [pendingRow, setPendingRow] = useState<string | number | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  const isAdmin = user?.role === "admin";

  const fetchUsers = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/admin/users`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (res.status === 401 || res.status === 403) {
        setListError(STRINGS.admin.adminRequired);
        setUsers(null);
        return;
      }
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = (await res.json()) as { users: AuthUser[] };
      setUsers(data.users ?? []);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setListError(`목록을 불러오지 못했습니다: ${msg}`);
      setUsers(null);
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    // Only hit /api/admin/users once we know the caller is an admin.
    // This avoids emitting a 403 on every page load for non-admins.
    if (!authLoading && isAdmin) {
      void fetchUsers();
    }
  }, [authLoading, isAdmin, fetchUsers]);

  const runAction = useCallback(
    async (userId: string | number, action: AdminAction) => {
      setPendingRow(userId);
      setRowError(null);
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/admin/users/${userId}/${ACTION_ENDPOINTS[action]}`,
          {
            method: "POST",
            credentials: "include",
          },
        );
        if (!res.ok && res.status !== 204) {
          throw new Error(`HTTP ${res.status}`);
        }
        await fetchUsers();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setRowError(`작업 실패: ${msg}`);
      } finally {
        setPendingRow(null);
      }
    },
    [fetchUsers],
  );

  // ── Auth guards ───────────────────────────────────────────────────────

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12] font-mono text-sm text-white/40">
        loading...
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0a0a12]">
        <div className="max-w-md rounded-2xl border border-red-500/30 bg-slate-950/95 p-8 text-center shadow-2xl shadow-red-500/10">
          <div className="mb-3 text-4xl">⛔</div>
          <h1 className="text-2xl font-bold font-mono text-red-300">403</h1>
          <p className="mt-2 text-sm font-mono text-white/60">
            {STRINGS.admin.onlyAdmin}
          </p>
          {user && (
            <p className="mt-4 text-xs font-mono text-white/30">
              현재 계정: {user.username} ({user.role})
            </p>
          )}
        </div>
      </div>
    );
  }

  // ── Admin table ───────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[#0a0a12] p-6 text-white">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold font-mono text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 to-cyan-400">
              사용자 관리
            </h1>
            <p className="mt-1 text-xs font-mono text-white/40">
              승인 · 거부 · 비활성 · 재활성 — /api/admin/users
            </p>
          </div>
          <button
            type="button"
            onClick={fetchUsers}
            disabled={listLoading}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-white/60 hover:bg-white/10 disabled:opacity-30 transition-all"
          >
            {listLoading ? "새로고침 중..." : "새로고침 ⟳"}
          </button>
        </header>

        {listError && (
          <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs font-mono text-red-300">
            {listError}
          </div>
        )}
        {rowError && (
          <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs font-mono text-amber-300">
            {rowError}
          </div>
        )}

        <div
          className="overflow-hidden rounded-2xl border border-white/10"
          style={{
            background: "rgba(17,14,38,0.6)",
            boxShadow: "0 0 0 1px rgba(0,0,0,0.6) inset",
          }}
        >
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="border-b border-white/10 bg-white/5">
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  username
                </th>
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  role
                </th>
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  status
                </th>
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  created
                </th>
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  actions
                </th>
              </tr>
            </thead>
            <tbody>
              {listLoading && !users && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-8 text-center text-white/40"
                  >
                    loading...
                  </td>
                </tr>
              )}
              {users && users.length === 0 && !listLoading && (
                <tr>
                  <td
                    colSpan={5}
                    className="px-4 py-8 text-center text-white/30"
                  >
                    등록된 사용자가 없습니다.
                  </td>
                </tr>
              )}
              {users?.map((u) => {
                const style = STATUS_STYLES[u.status] ?? STATUS_STYLES.denied;
                const pending = pendingRow === u.id;
                return (
                  <tr
                    key={u.id}
                    className="border-b border-white/5 last:border-none hover:bg-white/[0.03] transition-colors"
                  >
                    <td className="px-4 py-3 text-white">{u.username}</td>
                    <td className="px-4 py-3 text-white/70">{u.role}</td>
                    <td className="px-4 py-3">
                      <span
                        className="rounded-full px-2 py-0.5 text-[10px]"
                        style={{ background: style.bg, color: style.color }}
                      >
                        {style.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-white/50">
                      {formatDate(u.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        <RowActions
                          status={u.status}
                          disabled={pending}
                          onAction={(action) => void runAction(u.id, action)}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── Row-level action buttons ─────────────────────────────────────────────

function RowActions({
  status,
  disabled,
  onAction,
}: {
  status: UserStatus;
  disabled: boolean;
  onAction: (action: AdminAction) => void;
}) {
  if (status === "pending") {
    return (
      <>
        <ActionButton
          tone="emerald"
          disabled={disabled}
          onClick={() => onAction("approve")}
        >
          Approve
        </ActionButton>
        <ActionButton
          tone="red"
          disabled={disabled}
          onClick={() => onAction("deny")}
        >
          Deny
        </ActionButton>
      </>
    );
  }
  if (status === "active") {
    return (
      <ActionButton
        tone="amber"
        disabled={disabled}
        onClick={() => onAction("disable")}
      >
        Disable
      </ActionButton>
    );
  }
  if (status === "disabled") {
    return (
      <ActionButton
        tone="cyan"
        disabled={disabled}
        onClick={() => onAction("reactivate")}
      >
        Reactivate
      </ActionButton>
    );
  }
  // denied — terminal state, no actions.
  return <span className="text-[10px] text-white/30">—</span>;
}

function ActionButton({
  tone,
  disabled,
  onClick,
  children,
}: {
  tone: "emerald" | "red" | "amber" | "cyan";
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  const toneClasses: Record<typeof tone, string> = {
    emerald:
      "border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20",
    red: "border-red-500/40 bg-red-500/10 text-red-300 hover:bg-red-500/20",
    amber:
      "border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20",
    cyan: "border-cyan-500/40 bg-cyan-500/10 text-cyan-300 hover:bg-cyan-500/20",
  };
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`rounded-md border px-3 py-1 text-[10px] font-bold transition-all disabled:opacity-30 disabled:cursor-not-allowed ${toneClasses[tone]}`}
    >
      {children}
    </button>
  );
}

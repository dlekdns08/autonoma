"use client";

/**
 * Custom Achievement DSL admin — list / create / toggle / delete the
 * runtime-defined badges that live in ``custom_achievements`` on the
 * backend. Mirrors the visual language of ``/admin/users``.
 *
 * Defensive parsing
 * -----------------
 * The list endpoint may return either ``{items: [...]}`` or a bare
 * array depending on the API version we land on; both shapes are
 * coerced into an array client-side and ``Array.isArray`` is used as
 * the gate everywhere we touch the response.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { API_BASE_URL } from "@/hooks/useSwarm";
import { STRINGS } from "@/lib/strings";

interface CustomDefinitionTrigger {
  event: string;
  count: number;
  scope: string;
  where?: Record<string, unknown>;
}

interface CustomDefinitionPayload {
  id: string;
  title: string;
  description?: string;
  tier?: string;
  xp_reward?: number;
  trigger: CustomDefinitionTrigger;
}

interface CustomDefinitionRow {
  id: string;
  enabled: boolean;
  created_at: string;
  created_by: string | null;
  definition: CustomDefinitionPayload | null;
  error?: string;
}

/**
 * Defensive normalizer — accepts either ``{items}`` or a bare array.
 * Anything else collapses to ``[]`` so the table never crashes.
 */
function normalizeItems(data: unknown): CustomDefinitionRow[] {
  if (Array.isArray(data)) {
    return data.filter((r): r is CustomDefinitionRow =>
      Boolean(r && typeof r === "object" && "id" in r),
    );
  }
  if (data && typeof data === "object" && "items" in data) {
    const inner = (data as { items: unknown }).items;
    if (Array.isArray(inner)) {
      return inner.filter((r): r is CustomDefinitionRow =>
        Boolean(r && typeof r === "object" && "id" in r),
      );
    }
  }
  return [];
}

const EXAMPLE_DSL = `{
  "id": "boss_slayer_3",
  "title": "Boss Slayer III",
  "description": "Defeat 3 bosses in your lifetime.",
  "tier": "gold",
  "xp_reward": 50,
  "trigger": {
    "event": "boss.defeated",
    "count": 3,
    "scope": "lifetime"
  }
}`;

const TIER_COLORS: Record<string, { bg: string; color: string }> = {
  bronze: { bg: "rgba(180,83,9,0.15)", color: "#fcd34d" },
  silver: { bg: "rgba(148,163,184,0.15)", color: "#e2e8f0" },
  gold: { bg: "rgba(234,179,8,0.18)", color: "#fde68a" },
  platinum: { bg: "rgba(167,139,250,0.18)", color: "#ddd6fe" },
};

function tierStyle(tier: string | undefined): { bg: string; color: string } {
  return (
    TIER_COLORS[(tier || "").toLowerCase()] ?? {
      bg: "rgba(148,163,184,0.10)",
      color: "#cbd5e1",
    }
  );
}

export default function AdminCustomAchievementsPage() {
  const { user, loading: authLoading } = useAuth();
  const isAdmin = user?.role === "admin";

  const [rows, setRows] = useState<CustomDefinitionRow[] | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  // "+ New" editor
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorText, setEditorText] = useState(EXAMPLE_DSL);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const fetchList = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/custom-achievements`, {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (res.status === 401 || res.status === 403) {
        setListError(STRINGS.admin.adminRequired);
        setRows(null);
        return;
      }
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data: unknown = await res.json();
      setRows(normalizeItems(data));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setListError(`목록을 불러오지 못했습니다: ${msg}`);
      setRows(null);
    } finally {
      setListLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authLoading && isAdmin) {
      void fetchList();
    }
  }, [authLoading, isAdmin, fetchList]);

  const toggleEnabled = useCallback(
    async (id: string, nextEnabled: boolean) => {
      setPendingId(id);
      setRowError(null);
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/custom-achievements/${encodeURIComponent(id)}`,
          {
            method: "PATCH",
            credentials: "include",
            headers: {
              "Content-Type": "application/json",
              Accept: "application/json",
            },
            body: JSON.stringify({ enabled: nextEnabled }),
          },
        );
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        await fetchList();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setRowError(`토글 실패: ${msg}`);
      } finally {
        setPendingId(null);
      }
    },
    [fetchList],
  );

  const removeRow = useCallback(
    async (id: string) => {
      const ok = window.confirm(`'${id}' 정의를 삭제할까요? 이미 획득한 배지는 유지됩니다.`);
      if (!ok) return;
      setPendingId(id);
      setRowError(null);
      try {
        const res = await fetch(
          `${API_BASE_URL}/api/custom-achievements/${encodeURIComponent(id)}`,
          {
            method: "DELETE",
            credentials: "include",
          },
        );
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        await fetchList();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setRowError(`삭제 실패: ${msg}`);
      } finally {
        setPendingId(null);
      }
    },
    [fetchList],
  );

  const submitNew = useCallback(async () => {
    setEditorError(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(editorText);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setEditorError(`JSON 파싱 실패: ${msg}`);
      return;
    }
    setCreating(true);
    try {
      const res = await fetch(`${API_BASE_URL}/api/custom-achievements`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(parsed),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const j = (await res.json()) as {
            detail?: { code?: string; message?: string } | string;
          };
          if (typeof j.detail === "string") {
            detail = j.detail;
          } else if (j.detail && typeof j.detail === "object") {
            detail = j.detail.message || j.detail.code || detail;
          }
        } catch {
          // body wasn't JSON; keep the HTTP-N detail
        }
        throw new Error(detail);
      }
      setEditorOpen(false);
      setEditorText(EXAMPLE_DSL);
      await fetchList();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setEditorError(msg);
    } finally {
      setCreating(false);
    }
  }, [editorText, fetchList]);

  const rowsArr = useMemo(() => (Array.isArray(rows) ? rows : []), [rows]);

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

  return (
    <div className="min-h-screen bg-[#0a0a12] p-6 text-white">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold font-mono text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 to-cyan-400">
              커스텀 업적 DSL
            </h1>
            <p className="mt-1 text-xs font-mono text-white/40">
              JSON DSL · /api/custom-achievements
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setEditorOpen((v) => !v);
                setEditorError(null);
              }}
              className="rounded-xl border border-fuchsia-500/40 bg-fuchsia-500/10 px-4 py-2 text-xs font-mono text-fuchsia-200 hover:bg-fuchsia-500/20 transition-all"
            >
              {editorOpen ? "닫기" : "+ New"}
            </button>
            <button
              type="button"
              onClick={fetchList}
              disabled={listLoading}
              className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-white/60 hover:bg-white/10 disabled:opacity-30 transition-all"
            >
              {listLoading ? "새로고침 중..." : "새로고침 ⟳"}
            </button>
          </div>
        </header>

        {editorOpen && (
          <div className="mb-6 overflow-hidden rounded-2xl border border-fuchsia-500/30 bg-fuchsia-500/[0.04] p-4">
            <p className="mb-2 text-[11px] font-mono uppercase tracking-widest text-fuchsia-300">
              새 정의 (JSON)
            </p>
            <p className="mb-3 text-[11px] font-mono text-white/40">
              지원 이벤트: <span className="text-white/60">boss.defeated</span> ·{" "}
              <span className="text-white/60">quest.completed</span> ·{" "}
              <span className="text-white/60">sandbox.run_finished</span> ·
              scope: <span className="text-white/60">lifetime</span>/<span className="text-white/60">session</span>
            </p>
            <textarea
              value={editorText}
              onChange={(e) => setEditorText(e.target.value)}
              spellCheck={false}
              rows={14}
              className="w-full rounded-lg border border-white/10 bg-slate-950/80 p-3 font-mono text-xs text-emerald-200 focus:border-fuchsia-400/60 focus:outline-none"
            />
            {editorError && (
              <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs font-mono text-red-300">
                {editorError}
              </div>
            )}
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setEditorText(EXAMPLE_DSL)}
                className="rounded-md border border-white/10 bg-white/5 px-3 py-1 text-[11px] font-mono text-white/50 hover:bg-white/10"
              >
                예제 복원
              </button>
              <button
                type="button"
                disabled={creating}
                onClick={() => void submitNew()}
                className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-[11px] font-bold font-mono text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-30"
              >
                {creating ? "저장 중..." : "저장"}
              </button>
            </div>
          </div>
        )}

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
                  id
                </th>
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  title
                </th>
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  trigger
                </th>
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  tier
                </th>
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  enabled
                </th>
                <th className="px-4 py-3 text-[11px] font-bold uppercase tracking-widest text-violet-300">
                  actions
                </th>
              </tr>
            </thead>
            <tbody>
              {listLoading && rowsArr.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-white/40">
                    loading...
                  </td>
                </tr>
              )}
              {!listLoading && rowsArr.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-white/30">
                    등록된 커스텀 업적이 없습니다.
                  </td>
                </tr>
              )}
              {rowsArr.map((row) => {
                const def = row.definition;
                const trig = def?.trigger;
                const ts = tierStyle(def?.tier);
                const pending = pendingId === row.id;
                return (
                  <tr
                    key={row.id}
                    className="border-b border-white/5 last:border-none hover:bg-white/[0.03] transition-colors"
                  >
                    <td className="px-4 py-3 text-white/80">{row.id}</td>
                    <td className="px-4 py-3 text-white">
                      {def?.title ?? <span className="text-red-300">(invalid)</span>}
                      {def?.description && (
                        <div className="mt-1 text-[10px] text-white/40">
                          {def.description}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-white/70">
                      {trig ? (
                        <>
                          <div>{trig.event}</div>
                          <div className="text-[10px] text-white/40">
                            ≥{trig.count} · {trig.scope}
                            {trig.where &&
                              Object.keys(trig.where).length > 0 &&
                              ` · where=${JSON.stringify(trig.where)}`}
                          </div>
                        </>
                      ) : (
                        <span className="text-red-300">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className="rounded-full px-2 py-0.5 text-[10px]"
                        style={{ background: ts.bg, color: ts.color }}
                      >
                        {def?.tier || "—"}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        disabled={pending}
                        onClick={() => void toggleEnabled(row.id, !row.enabled)}
                        className={`rounded-md border px-2 py-0.5 text-[10px] font-bold transition-all disabled:opacity-30 ${
                          row.enabled
                            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"
                            : "border-white/15 bg-white/5 text-white/40 hover:bg-white/10"
                        }`}
                      >
                        {row.enabled ? "enabled" : "disabled"}
                      </button>
                    </td>
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        disabled={pending}
                        onClick={() => void removeRow(row.id)}
                        className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1 text-[10px] font-bold text-red-300 hover:bg-red-500/20 disabled:opacity-30"
                      >
                        Delete
                      </button>
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

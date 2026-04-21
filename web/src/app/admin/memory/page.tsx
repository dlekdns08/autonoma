"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/hooks/useAuth";
import { API_BASE_URL } from "@/hooks/useSwarm";
import MemoryInspector from "@/components/MemoryInspector";

export default function AdminMemoryPage() {
  const { user, loading: authLoading } = useAuth();
  const [agentNames, setAgentNames] = useState<string[]>([]);

  const isAdmin = user?.role === "admin";

  // Fetch running agent names from the snapshot endpoint or agents endpoint.
  useEffect(() => {
    if (!isAdmin) return;
    const fetchAgents = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/agents`, {
          credentials: "include",
        });
        if (res.ok) {
          const data = (await res.json()) as { agents?: { name: string }[] } | { name: string }[];
          const agents = Array.isArray(data) ? data : (data.agents ?? []);
          setAgentNames(agents.map((a) => a.name).filter(Boolean));
        }
      } catch {
        // ignore — MemoryInspector will show an error when the user selects
      }
    };
    void fetchAgents();
  }, [isAdmin]);

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
          <p className="mt-2 text-sm font-mono text-white/60">관리자만 접근할 수 있습니다.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0a12] text-white p-6">
      <div className="max-w-2xl mx-auto flex flex-col gap-4">
        <div className="flex items-center gap-3 mb-2">
          <a
            href="/admin/users"
            className="text-violet-400/60 hover:text-violet-300 text-xs font-mono underline underline-offset-2 transition-colors"
          >
            ← users
          </a>
          <span className="text-white/20 text-xs">|</span>
          <span className="text-violet-300/70 text-xs font-mono">memory</span>
        </div>

        <MemoryInspector agentNames={agentNames} />
      </div>
    </div>
  );
}

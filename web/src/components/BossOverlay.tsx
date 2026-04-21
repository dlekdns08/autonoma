"use client";

import { useEffect, useRef, useState } from "react";
import type { BossData } from "@/lib/types";

interface Props {
  boss: BossData;
}

export default function BossOverlay({ boss }: Props) {
  const [shake, setShake] = useState(false);
  const prevHpRef = useRef(boss.hp);
  const hpPct = boss.max_hp > 0 ? (boss.hp / boss.max_hp) * 100 : 0;
  const hpColor = hpPct > 60 ? "from-green-500 to-emerald-400" : hpPct > 30 ? "from-yellow-500 to-orange-400" : "from-red-500 to-rose-400";

  if (prevHpRef.current !== boss.hp) {
    prevHpRef.current = boss.hp;
    if (!shake) setShake(true);
  }

  useEffect(() => {
    if (!shake) return;
    const t = setTimeout(() => setShake(false), 300);
    return () => clearTimeout(t);
  }, [shake]);

  return (
    <div className="absolute inset-x-0 top-0 z-20 flex justify-center">
      <div
        className={`mt-2 rounded-xl border-2 border-red-500/50 bg-gradient-to-b from-red-950/90 to-slate-950/90 px-6 py-3 backdrop-blur-sm shadow-2xl shadow-red-500/20 transition-transform ${shake ? "scale-105" : "scale-100"}`}
      >
        <div className="flex items-center gap-4">
          {/* Boss Icon */}
          <div className="text-3xl animate-pulse">☠</div>

          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="font-bold text-red-400 text-sm font-mono">{boss.name}</span>
              <span className="text-[10px] text-white/40">Lv{boss.level} {boss.species}</span>
            </div>

            {/* HP Bar */}
            <div className="flex items-center gap-2">
              <div className="h-3 w-48 rounded-full bg-white/10 overflow-hidden border border-white/10">
                <div
                  className={`h-full rounded-full bg-gradient-to-r ${hpColor} transition-all duration-300`}
                  style={{ width: `${hpPct}%` }}
                />
              </div>
              <span className="text-[10px] font-mono text-white/60">
                {boss.hp}/{boss.max_hp}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

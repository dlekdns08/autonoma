"use client";

import React, { useEffect, useRef, useState } from "react";

import Chibi from "@/components/stage/Chibi";

type Role =
  | "director" | "coder" | "reviewer" | "tester"
  | "writer" | "designer" | "generic";

type Species =
  | "human" | "cat" | "rabbit" | "fox" | "owl" | "bear"
  | "penguin" | "hamster" | "dog" | "panda" | "duck";

type Mood =
  | "happy" | "excited" | "proud" | "inspired" | "focused" | "determined"
  | "frustrated" | "tired" | "nostalgic" | "worried" | "curious"
  | "mischievous" | "relaxed" | "neutral";

type State =
  | "idle" | "walking" | "talking" | "thinking" | "working" | "celebrating";

type Rarity = "common" | "uncommon" | "rare" | "legendary";

const ROLES: Role[] = [
  "director", "coder", "reviewer", "tester", "writer", "designer", "generic",
];

const SPECIES: Species[] = [
  "human", "cat", "rabbit", "fox", "owl", "bear",
  "penguin", "hamster", "dog", "panda", "duck",
];

const MOODS: Mood[] = [
  "happy", "excited", "proud", "inspired", "focused", "determined",
  "frustrated", "tired", "nostalgic", "worried", "curious",
  "mischievous", "relaxed", "neutral",
];

const STATES: State[] = [
  "idle", "walking", "talking", "thinking", "working", "celebrating",
];

const RARITIES: Rarity[] = ["common", "uncommon", "rare", "legendary"];

const SEEDS: string[] = [
  "alice", "bob", "carol", "dave", "eve", "frank",
  "grace", "heidi", "ivan", "judy", "ken", "luna",
];

const WALK_SEEDS: string[] = ["alice", "bob", "carol", "dave"];

const CHIBI_SIZE = 140;

// --- hooks ---------------------------------------------------------------

/**
 * Shared walk phase ticker: cycles 0 → 1 over `durationMs` via rAF, then
 * loops. A single rAF loop drives every "walking" chibi on the page in
 * lockstep, so there is only one animation source feeding the grid.
 */
function useWalkPhase(durationMs = 800): number {
  const [phase, setPhase] = useState(0);
  const startRef = useRef<number | null>(null);

  useEffect(() => {
    let raf = 0;
    const tick = (now: number) => {
      if (startRef.current === null) startRef.current = now;
      const elapsed = (now - startRef.current) % durationMs;
      setPhase(elapsed / durationMs);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [durationMs]);

  return phase;
}

// --- presentational -------------------------------------------------------

function SectionHeader({ title }: { title: string }) {
  return <h2 className="text-2xl font-bold text-white mb-4 mt-12">{title}</h2>;
}

function Grid({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-6">
      {children}
    </div>
  );
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-end bg-slate-800/50 rounded-lg p-4 border border-slate-700">
      <div
        className="flex items-end justify-center"
        style={{ height: CHIBI_SIZE + 16 }}
      >
        {children}
      </div>
      <div className="mt-2 text-sm text-slate-200 font-mono text-center">
        {label}
      </div>
    </div>
  );
}

// --- page -----------------------------------------------------------------

export default function ChibiGalleryPage() {
  const walkPhase = useWalkPhase(800);

  return (
    <div className="min-h-screen bg-slate-900 text-white px-6 py-10">
      <header className="max-w-7xl mx-auto">
        <h1 className="text-5xl font-extrabold tracking-tight">Chibi Gallery</h1>
        <p className="mt-2 text-slate-400">
          Every meaningful chibi variant, rendered side-by-side.
        </p>
      </header>

      <main className="max-w-7xl mx-auto">
        <SectionHeader title="Roles" />
        <Grid>
          {ROLES.map((role) => (
            <Cell key={role} label={role}>
              <Chibi
                species="human"
                mood="happy"
                state="idle"
                role={role}
                size={CHIBI_SIZE}
                seed={`role-${role}`}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Species" />
        <Grid>
          {SPECIES.map((species) => (
            <Cell key={species} label={species}>
              <Chibi
                species={species}
                mood="happy"
                state="idle"
                role="coder"
                size={CHIBI_SIZE}
                seed={`species-${species}`}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Moods" />
        <Grid>
          {MOODS.map((mood) => (
            <Cell key={mood} label={mood}>
              <Chibi
                species="human"
                mood={mood}
                state="idle"
                role="writer"
                size={CHIBI_SIZE}
                seed={`mood-${mood}`}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="States" />
        <Grid>
          {STATES.map((state) => (
            <Cell key={state} label={state}>
              <Chibi
                species="human"
                mood="happy"
                state={state}
                role="coder"
                size={CHIBI_SIZE}
                seed={`state-${state}`}
                walkPhase={state === "walking" ? walkPhase : undefined}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Rarities" />
        <Grid>
          {RARITIES.map((rarity) => (
            <Cell key={rarity} label={rarity}>
              <Chibi
                species="human"
                mood="proud"
                state="idle"
                role="director"
                rarity={rarity}
                size={CHIBI_SIZE}
                seed={`rarity-${rarity}`}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Seeds" />
        <Grid>
          {SEEDS.map((seed) => (
            <Cell key={seed} label={seed}>
              <Chibi
                species="human"
                mood="happy"
                state="idle"
                role="coder"
                seed={seed}
                size={CHIBI_SIZE}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Walking" />
        <Grid>
          {WALK_SEEDS.map((seed, i) => (
            <Cell key={seed} label={`${seed} ${i % 2 === 0 ? "→" : "←"}`}>
              <Chibi
                species="human"
                mood="determined"
                state="walking"
                role="coder"
                seed={seed}
                facingLeft={i % 2 === 1}
                walkPhase={walkPhase}
                size={CHIBI_SIZE}
              />
            </Cell>
          ))}
        </Grid>
      </main>
    </div>
  );
}

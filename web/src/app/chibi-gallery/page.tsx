"use client";

import React, { useEffect, useRef, useState } from "react";

import PixelCharacter from "@/components/stage/pixel/PixelCharacter";
import PixelMap from "@/components/stage/pixel/PixelMap";
import { CHAR, STAGE, type SkyMode } from "@/components/stage/pixel/types";
import type { MapTheme } from "@/components/stage/pixel/mapData";
import type {
  HairStyle,
  Headwear,
  EarType,
  FacialHair,
} from "@/components/stage/pixel/characterSprite";

const ROLES = ["director", "coder", "reviewer", "tester", "writer", "designer", "generic"];
const SPECIES = ["human", "cat", "rabbit", "fox", "owl", "bear", "penguin", "hamster", "dog", "panda", "duck"];
const MOODS = ["happy", "excited", "proud", "inspired", "focused", "determined", "frustrated", "tired"];
const THEMES: MapTheme[] = ["meadow", "forest", "town"];
const SKIES: SkyMode[] = ["dawn", "day", "dusk", "night"];
const SEEDS = ["alice", "bob", "carol", "dave", "eve", "frank", "grace", "heidi", "ivan", "judy", "ken", "luna"];
const HAIR_STYLES: HairStyle[] = [
  "short",
  "spiky",
  "bob",
  "long",
  "ponytail",
  "buzz",
  "messy",
  "bald",
];
const HEADWEARS: Headwear[] = ["none", "cap", "beanie", "wizardHat", "hood"];
const EAR_TYPES: EarType[] = [
  "none",
  "cat",
  "fox",
  "rabbit",
  "bear",
  "owl",
  "dog",
  "hamster",
];
const FACIAL_HAIRS: FacialHair[] = ["none", "mustache", "beard"];

const PIXEL_SCALE = 5;
const CHAR_PX_W = CHAR.width * PIXEL_SCALE;
const CHAR_PX_H = CHAR.height * PIXEL_SCALE;

function useWalkPhase(durationMs = 640): number {
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

// IntersectionObserver-gated render. Each PixelCharacter/PixelMap is a
// small but non-trivial canvas render; the gallery has ~90 cells, so
// mounting them all up-front hit first-paint hard on modest hardware.
// Deferring offscreen cells keeps initial layout cheap while preserving
// the grid shape (the placeholder reserves exact cell height).
function LazyVisible({
  minHeight,
  children,
  rootMargin = "200px",
}: {
  minHeight: number;
  children: React.ReactNode;
  rootMargin?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!ref.current || visible) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin },
    );
    io.observe(ref.current);
    return () => io.disconnect();
  }, [visible, rootMargin]);
  return (
    <div
      ref={ref}
      className="flex items-end justify-center w-full"
      style={{ minHeight }}
    >
      {visible ? children : null}
    </div>
  );
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-end bg-slate-800/50 rounded-lg p-4 border border-slate-700">
      <LazyVisible minHeight={CHAR_PX_H + 8}>{children}</LazyVisible>
      <div className="mt-2 text-xs text-slate-200 font-mono text-center">{label}</div>
    </div>
  );
}

function MapCard({ theme, sky }: { theme: MapTheme; sky: SkyMode }) {
  return (
    <div className="flex flex-col gap-2 bg-slate-800/50 rounded-lg p-3 border border-slate-700">
      <div
        className="w-full overflow-hidden rounded"
        style={{
          aspectRatio: `${STAGE.width} / ${STAGE.height}`,
        }}
      >
        <LazyVisible minHeight={0}>
          <PixelMap theme={theme} sky={sky} />
        </LazyVisible>
      </div>
      <div className="text-xs font-mono text-slate-200 text-center">
        {theme} · {sky}
      </div>
    </div>
  );
}

export default function GalleryPage() {
  const walkPhase = useWalkPhase(640);

  return (
    <div className="min-h-screen bg-slate-900 text-white px-6 py-10">
      <header className="max-w-7xl mx-auto">
        <h1 className="text-5xl font-extrabold tracking-tight">Pixel Gallery</h1>
        <p className="mt-2 text-slate-400">
          Pokemon Gen 3/4 style pixel agents & tile maps.
        </p>
      </header>

      <main className="max-w-7xl mx-auto">
        <SectionHeader title="Map themes × sky" />
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {THEMES.flatMap((t) =>
            SKIES.map((s) => <MapCard key={`${t}-${s}`} theme={t} sky={s} />),
          )}
        </div>

        <SectionHeader title="Roles" />
        <Grid>
          {ROLES.map((role) => (
            <Cell key={role} label={role}>
              <PixelCharacter
                role={role}
                species="human"
                mood="happy"
                seed={`role-${role}`}
                pixelScale={PIXEL_SCALE}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Species" />
        <Grid>
          {SPECIES.map((species) => (
            <Cell key={species} label={species}>
              <PixelCharacter
                role="coder"
                species={species}
                mood="happy"
                seed={`species-${species}`}
                pixelScale={PIXEL_SCALE}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Moods" />
        <Grid>
          {MOODS.map((mood) => (
            <Cell key={mood} label={mood}>
              <PixelCharacter
                role="writer"
                species="human"
                mood={mood}
                seed={`mood-${mood}`}
                pixelScale={PIXEL_SCALE}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Seeds (palette variation)" />
        <Grid>
          {SEEDS.map((seed) => (
            <Cell key={seed} label={seed}>
              <PixelCharacter
                role="coder"
                species="human"
                mood="happy"
                seed={seed}
                pixelScale={PIXEL_SCALE}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Hair styles" />
        <Grid>
          {HAIR_STYLES.map((hair) => (
            <Cell key={hair} label={hair}>
              <PixelCharacter
                role="coder"
                species="human"
                mood="happy"
                seed={`hair-${hair}`}
                featureOverride={{
                  hairStyle: hair,
                  headwear: "none",
                  ears: "none",
                  glasses: false,
                  facialHair: "none",
                }}
                pixelScale={PIXEL_SCALE}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Headwear" />
        <Grid>
          {HEADWEARS.map((hw) => (
            <Cell key={hw} label={hw}>
              <PixelCharacter
                role="coder"
                species="human"
                mood="happy"
                seed={`hw-${hw}`}
                featureOverride={{
                  hairStyle: "short",
                  headwear: hw,
                  ears: "none",
                  glasses: false,
                  facialHair: "none",
                }}
                pixelScale={PIXEL_SCALE}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Species ears" />
        <Grid>
          {EAR_TYPES.map((ear) => (
            <Cell key={ear} label={ear}>
              <PixelCharacter
                role="coder"
                species={ear === "none" ? "human" : ear}
                mood="happy"
                seed={`ear-${ear}`}
                featureOverride={{
                  hairStyle: "short",
                  headwear: "none",
                  ears: ear,
                  glasses: false,
                  facialHair: "none",
                }}
                pixelScale={PIXEL_SCALE}
              />
            </Cell>
          ))}
        </Grid>

        <SectionHeader title="Facial hair + glasses" />
        <Grid>
          {FACIAL_HAIRS.map((fh) => (
            <Cell key={`fh-${fh}`} label={`${fh}`}>
              <PixelCharacter
                role="coder"
                species="human"
                mood="happy"
                seed={`fh-${fh}`}
                featureOverride={{
                  hairStyle: "short",
                  headwear: "none",
                  ears: "none",
                  glasses: false,
                  facialHair: fh,
                }}
                pixelScale={PIXEL_SCALE}
              />
            </Cell>
          ))}
          <Cell label="glasses">
            <PixelCharacter
              role="coder"
              species="human"
              mood="happy"
              seed="glasses"
              featureOverride={{
                hairStyle: "short",
                headwear: "none",
                ears: "none",
                glasses: true,
                facialHair: "none",
              }}
              pixelScale={PIXEL_SCALE}
            />
          </Cell>
          <Cell label="glasses + beard">
            <PixelCharacter
              role="coder"
              species="human"
              mood="happy"
              seed="combo"
              featureOverride={{
                hairStyle: "messy",
                headwear: "none",
                ears: "none",
                glasses: true,
                facialHair: "beard",
              }}
              pixelScale={PIXEL_SCALE}
            />
          </Cell>
        </Grid>

        <SectionHeader title="Walk cycle" />
        <Grid>
          {SEEDS.slice(0, 6).map((seed, i) => (
            <Cell key={seed} label={`${seed} ${i % 2 === 0 ? "→" : "←"}`}>
              <PixelCharacter
                role="coder"
                species="human"
                mood="determined"
                seed={seed}
                walkPhase={walkPhase}
                facingLeft={i % 2 === 1}
                pixelScale={PIXEL_SCALE}
              />
            </Cell>
          ))}
        </Grid>

        <div style={{ width: CHAR_PX_W }} />
      </main>
    </div>
  );
}

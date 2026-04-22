# Autonoma — web frontend

Next.js 16 + React 19 app for Autonoma's pixel stage, VTuber stage,
admin panels, and OBS feed. Talks to the Python backend over REST
(`/api/*`) + a single WebSocket for live swarm events.

Top-level project docs live in [`../README.md`](../README.md); this
readme covers only what you need to work on the web app itself.

## Dev

```bash
npm install
npm run dev          # http://localhost:3000
```

The app expects the Python API at `http://localhost:8000`. Start that
in another terminal (see the root README).

Admin pages live under `/admin/*` and require a cookie session with
`role=admin` — create an admin via the backend CLI before trying them.

## Scripts

| Script | What it does |
| --- | --- |
| `npm run dev` | Next.js dev server with HMR |
| `npm run build` | Production build |
| `npm run start` | Serve the production build |
| `npm run lint` | ESLint across the app |
| `npm run vrm:sync-licenses` | Regenerate `public/vrm/LICENSES.md` and drift-check `vrmCatalog.json` against `public/vrm/` |

## Layout

```text
src/
├── app/
│   ├── page.tsx          # Main dashboard (pixel + VTuber + chat)
│   ├── obs/              # Chromakey-friendly VTuber-only feed
│   ├── chibi-gallery/    # Procedural pixel character gallery
│   └── admin/            # Admin panels (runs, users, memory)
├── components/
│   ├── Stage.tsx         # 2D pixel cyber-HUD room
│   ├── EventLog.tsx      # Live event feed
│   ├── ExecutionTimeline.tsx
│   ├── AuthModal.tsx     # Login / signup / legacy admin
│   ├── vtuber/           # VRM render + gesture/expression engine
│   └── stage/            # Backdrops, particles, minimap, pixel sprites
├── hooks/
│   ├── useSwarm.ts       # WebSocket state machine
│   ├── useAuth.ts        # Cookie-session auth
│   └── useAgentVoice.ts  # Per-agent TTS + lip-sync amplitude
└── lib/
    ├── strings.ts        # Centralized UI strings (i18n seed layer)
    ├── types.ts          # Shared TS types incl. EventPayloadMap
    └── events.ts         # Typed event payload narrowing
```

## Conventions

- **No raw Korean strings in new code.** Shared strings go in
  [`src/lib/strings.ts`](./src/lib/strings.ts); single-use component
  copy may stay inline.
- **Event payloads** narrow through `payloadOf(entry, "event.name")` —
  see [`src/lib/events.ts`](./src/lib/events.ts). Unknown shapes
  warn-once to the console instead of throwing.
- **VRM catalog drift** is checked at `npm run vrm:sync-licenses`; CI
  fails when `vrmCatalog.json` and `public/vrm/*.vrm` disagree.

## Agent notes

This is NOT the Next.js you know from training data. See
[`AGENTS.md`](./AGENTS.md) before writing code — some APIs and
conventions differ from older major versions.

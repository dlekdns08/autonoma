# Autonoma

**Self-organizing agent swarm with a live cast you can watch.**

[한국어 README](./README.ko.md)

```
     ╔═╗╦ ╦╔╦╗╔═╗╔╗╔╔═╗╔╦╗╔═╗
     ╠═╣║ ║ ║ ║ ║║║║║ ║║║║╠═╣
     ╩ ╩╚═╝ ╩ ╚═╝╝╚╝╚═╝╩ ╩╩ ╩
     Self-Organizing Agent Swarm
```

Describe what you want to build. A **Director** agent decomposes the goal,
spawns specialized agents, routes work between them, and celebrates when
everything lands. You watch it all happen — in a terminal dashboard, a
cyber-HUD pixel map, or as 3D VTuber characters with lip-sync, moods, and
gestures driven by the agents' actual behavior.

## Interfaces

| Interface | What it is | When to use |
| --- | --- | --- |
| **Terminal TUI** | Rich-based animated dashboard with ASCII sprites | Local runs, CI, headless demos |
| **Pixel stage** (web) | 2D cyber-HUD room view over WebSocket | Day-to-day browser use |
| **VTuber stage** (web) | 3D VRM spotlight with TTS + lip-sync + VRoid Hub models | Streaming, personality-forward demos |
| **OBS mode** (`/obs`) | Chromakey-friendly clean VTuber feed | Live streaming / compositing |

## Features

- **Autonomous planning** — the Director decomposes goals, assigns tasks, and spawns more agents when the workload demands it.
- **Agents pick their own work** — each agent runs an observe → decide → act loop: `work_on_task`, `create_file`, `send_message`, `request_help`, `spawn_agent`, `complete_task`, `celebrate`.
- **Persistent characters** — SQLite-backed character registry, so recurring agents keep their names, personalities, and levels across sessions.
- **Live VTuber performance** — mood-driven blendshapes, 5-vowel lip-sync, contrapposto weight shift, beat gestures during speech, cross-faded state transitions.
- **Multi-viewer rooms** — host starts a swarm, others join via a short code and watch in sync.
- **Pluggable LLM backend** — Anthropic, OpenAI, or any OpenAI-compatible endpoint (vLLM).
- **Optional TTS** — Azure Neural or OpenAI TTS with per-agent voice assignment and budget caps.
- **Sandboxed execution** — agents run code they write inside a bubblewrap sandbox with CPU / wall-time / memory limits.

## Quick start

### Prerequisites

- Python 3.12+, [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (only for the web UI)
- `ANTHROPIC_API_KEY` *or* `OPENAI_API_KEY` *or* a vLLM endpoint

### Terminal TUI only

```bash
uv sync
export ANTHROPIC_API_KEY=sk-ant-...

# Build something
uv run autonoma build "A REST API for managing bookmarks with tags and search"

# Walk-through mode
uv run autonoma interactive

# Canned demo
uv run autonoma demo
```

### Web UI (backend + frontend)

```bash
# Terminal 1 — API + WebSocket server
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
uv run uvicorn autonoma.api:app --port 8000

# Terminal 2 — Next.js frontend
cd web
npm install
npm run dev          # http://localhost:3000
```

Open the web app and paste your project goal. The Director takes it from there.

## Architecture

```
src/autonoma/
├── cli.py              # Click CLI — build / interactive / demo
├── api.py              # FastAPI + WebSocket bridge for the web UI
├── config.py           # pydantic-settings config (AUTONOMA_* env vars)
├── event_bus.py        # Async pub/sub with wildcard subscriptions
├── models.py           # Core data models (Persona, Task, Message, ...)
├── world.py            # Mood enum, room geometry, world state
├── llm.py              # Provider abstraction (Anthropic / OpenAI / vLLM)
├── tts.py, tts_worker.py
├── agents/
│   ├── base.py         # AutonomousAgent — observe→decide→act loop
│   ├── director.py     # Decomposes goals, spawns specialized agents
│   └── swarm.py        # Lifecycle, routing, fortune cookies, relationships
├── tui/                # Rich-based animated dashboard
├── engine/             # Unified swarm + TUI + workspace runner
├── db/                 # SQLite persistent character registry
└── sandbox.py          # Bubblewrap code-execution sandbox

web/src/
├── app/
│   ├── page.tsx                # Main dashboard (pixel + VTuber + chat)
│   ├── obs/                    # Chromakey-friendly VTuber-only feed
│   └── chibi-gallery/          # Procedural chibi face gallery
├── components/
│   ├── Stage.tsx               # 2D pixel cyber-HUD room
│   ├── vtuber/
│   │   ├── VTuberStage.tsx     # 3D spotlight + gallery
│   │   ├── VRMCharacter.tsx    # VRM render + gesture/expression engine
│   │   ├── vrmCatalog.json     # Single source of truth for VRM models
│   │   └── vrmCredits.ts       # Typed API over the catalog
│   └── stage/                  # Backdrops, particles, minimap
└── hooks/
    ├── useSwarm.ts             # WebSocket state machine
    └── useAgentVoice.ts        # Per-agent TTS playback + lip-sync amplitude
```

## Configuration

Settings are loaded from environment variables (`AUTONOMA_*` prefix) or a
`.env` file next to the process. The most common ones:

| Variable | Purpose | Default |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Provider credentials (bare names accepted) | — |
| `AUTONOMA_PROVIDER` | `anthropic` / `openai` / `vllm` | `anthropic` |
| `AUTONOMA_MODEL` | Model id | `claude-sonnet-4-6` |
| `AUTONOMA_VLLM_BASE_URL` / `AUTONOMA_VLLM_API_KEY` | For self-hosted OpenAI-compatible servers | — |
| `AUTONOMA_ADMIN_PASSWORD` | If set, enables server-key admin login in the web UI | — |
| `AUTONOMA_TTS_ENABLED` / `AUTONOMA_TTS_PROVIDER` | `azure` / `openai` / `none` | `false` / `none` |
| `AUTONOMA_TTS_AZURE_KEY` / `AUTONOMA_TTS_AZURE_REGION` | Azure Neural TTS credentials | — |
| `AUTONOMA_MAX_AGENTS` | Cap on concurrent agents | `8` |
| `AUTONOMA_OUTPUT_DIR` | Where agent-created files land | `./output` |
| `AUTONOMA_DATA_DIR` | SQLite character database location | `./data` |

See [`src/autonoma/config.py`](./src/autonoma/config.py) for the full list.

## Adding VRM models

VRM metadata lives in a single JSON file — add an entry, run the sync
script, done.

1. Drop `yourmodel.vrm` into `web/public/vrm/`.
2. Add an entry to [`web/src/components/vtuber/vrmCatalog.json`](./web/src/components/vtuber/vrmCatalog.json):

   ```json
   "yourmodel.vrm": {
     "character": "Display Name",
     "title": "Optional longer title for LICENSES.md",
     "author": "Author Handle",
     "url": "https://hub.vroid.com/...",
     "uploaded": "2026-04-21",
     "license": {
       "avatarUse": "Allow",
       "violentActs": "Allow",
       "sexualActs": "Allow",
       "corporateUse": "Allow",
       "individualCommercialUse": "Allow",
       "redistribution": "Allow",
       "alterations": "Allow",
       "attribution": "Not required"
     }
   }
   ```

3. `cd web && npm run vrm:sync-licenses` — regenerates `public/vrm/LICENSES.md`.

Agents are assigned to VRMs deterministically via a djb2 hash of their
name, so the same agent keeps the same character across sessions.

## Docker deployment

```bash
docker compose up -d
# API     → http://localhost:3479
# Web UI  → http://localhost:3478
```

An Nginx reverse-proxy config for `autonoma.koala.ai.kr` lives in
[`nginx/`](./nginx/). See [`docker-compose.prod.yml`](./docker-compose.prod.yml)
for the production deployment.

## Tests

```bash
uv run pytest tests/ -v
```

## License

VRM assets are individually licensed under VRoid Hub terms — see
[`web/public/vrm/LICENSES.md`](./web/public/vrm/LICENSES.md). The rest of
the project is unreleased; open an issue if you need a license clarified.

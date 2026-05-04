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
- **Optional TTS** — self-hosted OmniVoice zero-shot cloning (MPS/CUDA/CPU) with per-agent voice assignment and budget caps.
- **Sandboxed execution** — agents run code they write inside a bubblewrap sandbox with CPU / wall-time / memory limits.

### 2026-05 feature pack

A second wave of opt-in features, all behind `AUTONOMA_*_ENABLED` flags
(see [`.env.example`](./.env.example)). Off by default; flip the flag
to enable.

- **Live share & directory** — `/live` lists every public room; `/share/{code}` is the shareable landing page with Open-Graph metadata. Hosts toggle visibility per session.
- **Achievements & memoirs** — XP unlocks persist in `earned_achievements`; long-running characters get periodic LLM-summarised memoirs that compress their journal into the system prompt.
- **Honourable retirement & ghost cameos** — characters who survive `retirement_min_runs` × `retirement_min_level` retire; their compacted memoir lives on as a 5%-per-round dream cameo in future runs.
- **Auto-highlight reel** — server-side detection of clip-worthy moments (boss kills, raid victories, donation spikes); the OBS overlay records the actual MP4 from the buffer.
- **Live quests** — viewers propose / vote on round-objective cards; the winner activates as a swarm-wide buff for one round.
- **Viewer betting** — channel-points style markets; viewers stake 10/50/100 on outcomes, leaderboard tracks lifetime winnings.
- **Multi-viewer cursors + stickers** — spectators see each other's mouse trails on the stage and can fling emoji.
- **Procedural BGM** — Web-Audio synth crossfades between calm/focus/tension layers based on the swarm's mood; one-shot pulses on boss kicks and raid fanfares.
- **Persona breeding** — combine two published persona seeds into a child persona with merged tags and blended `prompt_style`.
- **Goal recommender** — `/api/inspire` reads a GitHub URL or file tree and proposes 5 next-feature goals via the LLM.
- **Sign-language fingerspelling** — Hangul-jamo fallback that turns out-of-vocabulary words into fingerspell pose plans.
- **Voice consent + watermark** — voice profiles require a recorded consent phrase before TTS use; synthesised audio is LSB-watermarked.
- **VMC/OSC bridge** — push live mocap frames to VRChat / VMC4U over OSC UDP. The browser's `/mocap` page becomes a head-tracker for any external 3D receiver.
- **Anomaly detection** — repetition, mood-drift, file-churn, LLM-error-burst rules emit `session.anomaly` events that surface in the A/B compare report.
- **A/B preset comparison** — pick two finished runs (`/admin/ab-compare`), compare tasks-done %, rounds, anomalies, LLM cost.
- **Swarm-vs-swarm coordinator** — invite-based matchmaking + ELO leaderboard. The local instance hosts the coordinator endpoints under `/api/coordinator/*`.
- **MCP server** — exposes `start_swarm_headless`, `fetch_run_summary`, `fetch_diary`, `fetch_world_events` as MCP tools so Claude Code / Cursor can drive Autonoma directly. Off by default; gated by `AUTONOMA_MCP_SERVER_ENABLED`.
- **Auto CI loop** — every file an agent creates is sandbox-checked (ruff for `.py`, `tsc` for `.ts/.tsx`, `json.loads` for `.json`); failures fold back into the agent's inbox as a fix-task.
- **OpenTelemetry + Prometheus** — `setup_otel()` exports traces to OTLP; `/metrics` serves Prometheus scrape format.

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
| `AUTONOMA_TTS_ENABLED` / `AUTONOMA_TTS_PROVIDER` | Toggle + backend: `omnivoice` / `none` | `false` / `none` |
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

## Harness Engineering

Runtime policy for each swarm run — routing, loop limits, decision
strategies, safety levels, mood transitions, and more — lives in the
`harness_policies` table and is resolved per-`start` command. Users
pick a preset in the Idle screen's ⚙ panel and/or override specific
sections; the merged policy is validated before the swarm boots.

- **Presets** — per-user and a system default. CRUD via
  `/api/harness/presets`. The default is read-only; users can save
  tweaks as new presets.
- **Validation** — two orthogonal layers on top of Pydantic:
  dangerous combinations (e.g. `code_execution=disabled` +
  `harness_enforcement=off`) are rejected for everyone, admin-only
  values (e.g. `safety.enforcement_level=off`, `loop.max_rounds>200`)
  are rejected for non-admins. See
  [`src/autonoma/harness/validation.py`](./src/autonoma/harness/validation.py).
- **Observability** — each run records `session_id`, `preset_id`,
  overridden sections, effective policy, and strategy picks. Fetch via
  `GET /api/session/{id}/metadata`; global rollups at
  `GET /api/harness/metrics` (admin). Emitted as `session.metadata`
  over the WS event bus when a run ends.
- **Schema for the UI** — `GET /api/harness/schema` introspects
  `HarnessPolicyContent` at runtime and returns per-field type / default /
  enum options / numeric bounds. Add a new `Literal` value to the
  Pydantic model and the frontend form picks it up with no TS change.

### Policy model and strategy registry

The typed shape of every knob lives in
[`src/autonoma/harness/policy.py`](./src/autonoma/harness/policy.py) —
`HarnessPolicyContent` plus nine Pydantic sub-policies, with `ge`/`le`
bounds on numeric fields and `Literal[...]` enums on every algorithmic
branch. Validation runs in three layers: (1) Pydantic field-level
constraints, (2) cross-field combination checks in
[`harness/validation.py`](./src/autonoma/harness/validation.py), and
(3) the strategy registry in
[`src/autonoma/harness/strategies.py`](./src/autonoma/harness/strategies.py),
which auto-seeds itself by introspecting every `Literal` value in the
policy model and ensures each enum slot resolves to a registered
callable — drift between the schema and runtime dispatch is a startup
error, not a silent no-op.

### Deployment notes

- Migrations are version-gated and apply automatically on startup
  (see [`src/autonoma/db/engine.py`](./src/autonoma/db/engine.py)).
  `harness_policies` is migration 003; the framework uses
  `create_all(checkfirst=True)` plus a `schema_version` counter so
  re-running the process against a populated DB is a safe no-op.
- Set `AUTONOMA_SESSION_SECRET` in production — without it, cookie
  sessions are signed with an ephemeral per-process secret and every
  restart logs every user out.
- Admin-only harness policies require a cookie-session user with
  `role=admin`. The legacy WS admin-password path also grants
  `is_admin` for the connection's `start` command.

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
# Structural parity between English and Korean READMEs:
python scripts/check_readme_drift.py
```

## License

VRM assets are individually licensed under VRoid Hub terms — see
[`web/public/vrm/LICENSES.md`](./web/public/vrm/LICENSES.md). The rest of
the project is unreleased; open an issue if you need a license clarified.

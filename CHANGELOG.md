# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/) once it
ships a tagged release.

Until then, dates mark feature packs and the `Unreleased` section
captures work in flight.

## [Unreleased]

### Added
- MCP server rebuilt on the official `mcp` SDK (`MCPServer`, formerly
  `FastMCP`): the standard streamable-HTTP transport is served at `/mcp`
  (stateless, JSON responses) with the same five tools, typed arguments
  and `X-MCP-Token` gate. The legacy `POST /mcp/jsonrpc` endpoint stays
  and dispatches through the same tool registry. `mcp>=2.3` is now a
  core dependency.
- Native tool-use decision protocol: each permitted action is a strict
  tool (`agents/actions.py`); the model calls exactly one per round and
  large inputs stream via `eager_input_streaming`. `AUTONOMA_DECISION_PROTOCOL`
  keeps the JSON-in-text contract available (`json`, default on vLLM).
- Append-only per-agent conversation history with `tool_result` turns,
  prompt-cache breakpoints, and a bounded reset (`AUTONOMA_AGENT_HISTORY_ROUNDS`).
- Spectator hints reach the worker agents as mid-conversation
  `role: system` operator notes (`AutonomousAgent.receive_operator_note`),
  folded into the user turn on models without that feature.
- Two-tier model/effort config: `AUTONOMA_WORKER_MODEL` (default
  `claude-haiku-5-5` on Anthropic), `AUTONOMA_DIRECTOR_EFFORT` / `AUTONOMA_WORKER_EFFORT`.
- `srt` auto-provisioning: the API lifespan and the CLI runner install the
  pinned `@anthropic-ai/sandbox-runtime` into `data/sandbox-runtime/` when
  it is missing (`AUTONOMA_SANDBOX_AUTO_INSTALL`); the Docker image installs
  it globally with Node.js.
- Lightweight sandboxing: `run_code` prefers Anthropic's sandbox runtime
  (`srt`) when installed — no network, writes confined to the scratch
  dir, credential/dotfile reads denied, memory and process caps via
  `ulimit` inside the sandbox (memory enforced on Linux; macOS ignores
  RLIMIT_AS, the wall-clock timeout is the backstop) —
  falling back to sandbox-exec / bwrap / rlimit (`AUTONOMA_SANDBOX_BACKEND`).
  Deep-work sessions now run bash under Claude Code's OS sandbox with a
  domain allow-list and `acceptEdits` instead of `bypassPermissions`.
- Managed Agents (`autonoma.managed`, opt-in): `worker_backend=managed_agents`
  runs each task as an outcome-driven hosted session (rubric drafted from
  the task, per-character memory store attached, outputs copied back);
  dreams consolidate a run's sessions into the character's memories;
  cron-scheduled deployments and a verified webhook receiver under
  `/api/managed/*` (admin).
- Director interventions: on a stall or overdue task the Director asks
  the planning model for one directive (nudge / reassign / spawn /
  approve review / wait) via tool use; spectator hints reach that call
  as an operator-channel system message. `director.intervention` event.
- Advisor tool (`advisor_20260301`): `AUTONOMA_ADVISOR_MODEL` lets the
  Haiku 5.5 worker executor consult a stronger model mid-turn.
- Deep work extras: Agent SDK file checkpointing, advisory task budget,
  and a predefined `tester` sub-agent.
- Self-authored gestures (`autonoma.gestures`): a motion-family DSL,
  procedural expander, joint-limit/velocity validator, LLM choreographer
  with procedural fallback, `generated_gestures` table (migration 019),
  `/api/gestures` (list / clip / generate / vote) and `gesture.*` events.
  `AUTONOMA_GESTURE_GEN_ENABLED` composes signature moves at spawn.
- Deep work: `AUTONOMA_WORKER_BACKEND=agent_sdk` delegates each task a
  worker picks up to a Claude Agent SDK session in `output/<project>/`
  (`agents/deepwork.py`, optional extra `agent-sdk`); streams
  `agent.tool_use` / `deepwork.*` events, registers touched files as
  artifacts, and completes the task with the session's summary.
- `stop_reason: refusal` handling (`agent.refusal` event, idle turn) and
  server-side `fallbacks: "default"` on models that support it.
- `pytest-cov` coverage reporting + CI summary.
- `[tool.ruff.format]` config and `ruff format --check` step in CI.
- Pre-commit hooks (ruff lint + format, EOF/whitespace, gitleaks secret scan).
- `pyright` static type checking in basic mode.
- Frontend test runner: Vitest + `@testing-library/react` with jsdom.
- Dependabot weekly PRs for `uv`, `npm`, and `github-actions`.
- CodeQL workflow (Python + JS/TS) on push, PR, and weekly schedule.
- Pull request template with test-plan checklist.
- New extracted modules: `_api_ws.py`, `_api_cookies.py`, `_api_headless.py`
  (carved out of the 4,608-line `api.py`; public import paths preserved).
- Test file `tests/test_voice_store_crud.py` (11 tests for the
  `voice/store.py` CRUD surface, including FS+DB consistency on insert
  rollback and migration-007 legacy fallback).

### Changed
- CI: the runner is configurable via the `CI_RUNNER` repository
  variable with a GitHub-hosted fallback (`ubuntu-latest`), and the job
  has a 30-minute `timeout-minutes` ceiling. Pyright stays non-blocking
  until the remaining backlog is cleared; the coverage gate stays at 55.
- `autonoma.api` split into routers: auth/admin users, harness, workspace
  (files, templates, history, agent memory, export), runs/checkpoints,
  health, models and mocap now live under `autonoma.routers.*`; the DB
  persistence helpers moved to `autonoma._api_persist`. Paths, auth
  dependencies, status codes and registration order are unchanged and
  every name previously importable from `autonoma.api` is re-exported.
  `api.py` went from ~4,600 to ~2,400 lines.
- `anthropic` SDK 0.92 → 2.x (API version `2026-10-21`): `temperature`
  is no longer sent to Claude (OpenAI/vLLM still honour it); responses
  are read by block type (5.x models emit `thinking` blocks first);
  `AUTONOMA_MAX_TOKENS` default 8192 → 16384 because thinking counts.
- OpenAI fallback catalog moved off the retiring gpt-4o / o1 ids (live
  `/v1/models` remains authoritative).
- Default model `claude-sonnet-4-6` → `claude-sonnet-5-5`; fallback
  catalog and UI lists moved to the 5.5 generation.
- `api.py` shrank from 4,608 → ~4,300 lines after the three extractions.
- CI workflow gained `ruff check`, `ruff format --check`, `pyright`,
  coverage, and a README-drift warning.

### Fixed
- Missing `llm_config_from_settings` import in `api.py` (NameError on
  scheduled headless runs).
- Missing `Any` import in `tts_worker.py` (NameError at instantiation).
- Duplicate `"determined"` mood key in `tui/renderer.py` (one branch
  was unreachable).
- Bogus `assert (a, b)` always-true assertion in `test_coordinator.py`.
- Ambiguous variable `l` in the world letter-recency check.
- Silent `except Exception: pass` in `tts_vibevoice.py` — now logs.

## 2026-05 Feature Pack

See [README.md](README.md#2026-05-feature-pack) for the full feature
list (12 additive surfaces: betting, achievements, ELO coordinator,
sign-language fingerspell, persona breeding, mocap-live, …).

## Earlier feature packs

Earlier waves of changes are recorded in commit history rather than
backfilled here. Run `git log --oneline --since=2026-01-01` for the
full timeline.

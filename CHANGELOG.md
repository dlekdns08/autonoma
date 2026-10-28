# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/) once it
ships a tagged release.

Until then, dates mark feature packs and the `Unreleased` section
captures work in flight.

## [Unreleased]

### Added
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
- `anthropic` SDK 0.92 → 2.x (API version `2026-10-21`): `temperature`
  is no longer sent to Claude (OpenAI/vLLM still honour it); responses
  are read by block type (5.x models emit `thinking` blocks first);
  `AUTONOMA_MAX_TOKENS` default 8192 → 16384 because thinking counts.
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

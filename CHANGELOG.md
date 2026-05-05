# Changelog

All notable changes to this project are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/) once it
ships a tagged release.

Until then, dates mark feature packs and the `Unreleased` section
captures work in flight.

## [Unreleased]

### Added
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

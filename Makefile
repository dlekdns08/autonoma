# Makefile — common dev tasks.
#
# All targets are .PHONY because nothing produces a literal file named
# "test" or "lint". Run ``make help`` (default) to discover targets.

.DEFAULT_GOAL := help

.PHONY: help install lint format fix typecheck test test-fast test-cov \
        web-test web-lint web-typecheck ci precommit \
        commit amend hooks

help:  ## Show this help.
	@awk 'BEGIN { FS = ":.*##" } /^[a-zA-Z_-]+:.*##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Install Python + web dev dependencies.
	uv sync --dev
	cd web && npm ci

# ── Python ────────────────────────────────────────────────────────────

lint:  ## Ruff lint + format check (no fixes).
	uv run ruff check src tests
	uv run ruff format --check src tests

format: fix  ## Alias for ``fix``.

fix:  ## Auto-format Python and apply ruff autofixes.
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:  ## Pyright (basic mode, non-blocking baseline).
	uv run pyright || true

test:  ## Full pytest with coverage report.
	uv run pytest tests/ --cov=src/autonoma --cov-report=term-missing:skip-covered

test-fast:  ## Pytest without coverage (~30% faster).
	uv run pytest tests/ --no-cov -q

test-cov:  ## Coverage with the CI gate (--cov-fail-under=55).
	uv run pytest tests/ --cov=src/autonoma --cov-report=term --cov-fail-under=55

# ── Frontend ──────────────────────────────────────────────────────────

web-test:  ## Run Vitest suite once.
	cd web && npm test

web-lint:  ## ESLint over the web directory.
	cd web && npm run lint

web-typecheck:  ## TypeScript --noEmit check.
	cd web && npx tsc --noEmit

# ── CI parity ─────────────────────────────────────────────────────────

ci: lint typecheck test web-typecheck web-test  ## Run everything CI runs, locally.
	@echo "✓ CI parity check passed."

precommit:  ## Run all pre-commit hooks against the whole repo.
	uv run pre-commit run --all-files

# ── Commit helpers ────────────────────────────────────────────────────

commit:  ## One-shot stage+format+commit.  Usage: make commit MSG="feat: foo"
	@if [ -z "$(MSG)" ]; then echo "Usage: make commit MSG=\"<message>\"" >&2; exit 2; fi
	scripts/commit.sh "$(MSG)"

amend:  ## Amend HEAD with current changes.  Usage: make amend [MSG="new"]
	@if [ -z "$(MSG)" ]; then scripts/commit.sh -a; else scripts/commit.sh -a "$(MSG)"; fi

hooks:  ## Install pre-commit git hooks (one-time).
	uv run pre-commit install

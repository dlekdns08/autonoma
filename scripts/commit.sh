#!/usr/bin/env bash
# scripts/commit.sh — one-command "stage everything + commit" helper.
#
# Why this exists: the common workflow at the end of a feature is
# ``git add file1 file2 file3 ... && git commit -m "..."``. Five
# tedious lines for a single logical change. This script collapses
# that into:
#
#     ./scripts/commit.sh "feat: my message"
#
# Behaviour:
#   1. Show ``git status -s`` so you can sanity-check what's about to
#      be staged.
#   2. Optionally format-fix Python+TS in one shot (skip with
#      ``--no-fmt``) so we never commit unformatted code.
#   3. ``git add -A`` — stages every modified, deleted, and untracked
#      file under the repo root (respecting .gitignore).
#   4. ``git commit -m "$msg"`` — pre-commit hooks still run if
#      installed; failures abort the commit.
#
# Usage:
#   ./scripts/commit.sh "feat(api): split headless runner into _api_headless"
#   ./scripts/commit.sh --no-fmt "wip: experimenting"
#   ./scripts/commit.sh -a "fix: typo in error message"   # amend HEAD
#
# Flags:
#   --no-fmt   Skip the auto-format pass.
#   -a         Amend the most recent commit instead of creating a new one.
#   -h         Show this help.
#
# Environment overrides:
#   COMMIT_SKIP_FORMAT=1   same as --no-fmt
#   COMMIT_AMEND=1         same as -a

set -euo pipefail

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \?//'
    exit 0
}

amend="${COMMIT_AMEND:-0}"
skip_format="${COMMIT_SKIP_FORMAT:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage ;;
        --no-fmt)  skip_format=1; shift ;;
        -a|--amend) amend=1; shift ;;
        --) shift; break ;;
        -*)
            echo "Unknown flag: $1" >&2
            exit 2
            ;;
        *)  break ;;
    esac
done

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "Not inside a git working tree." >&2
    exit 1
}
cd "$repo_root"

if [[ "$amend" == "0" ]] && [[ $# -lt 1 ]]; then
    echo "Usage: $0 [--no-fmt] [-a] \"<commit message>\"" >&2
    exit 2
fi

msg="${1:-}"

# 1. Show what's about to be committed.
echo "── Working tree ─────────────────────────────────────────────"
git status -s
if [[ -z "$(git status -s)" ]]; then
    echo "Nothing to commit." >&2
    exit 0
fi
echo

# 2. Auto-format. Skipped under --no-fmt to keep WIP commits cheap.
if [[ "$skip_format" == "0" ]]; then
    echo "── Formatting Python + TS ───────────────────────────────────"
    if command -v uv >/dev/null 2>&1 && [[ -f pyproject.toml ]]; then
        uv run ruff format src tests >/dev/null 2>&1 || true
        uv run ruff check --fix --silent src tests >/dev/null 2>&1 || true
    fi
    if [[ -d web ]] && [[ -f web/package.json ]]; then
        # ESLint --fix on web (best-effort; ESLint 9 flat config in
        # this repo doesn't always autofix, so don't fail the commit).
        ( cd web && npx --no-install eslint --fix src 2>/dev/null ) || true
    fi
    echo
fi

# 3. Stage everything (respects .gitignore).
git add -A

# 4. Commit (or amend).
if [[ "$amend" == "1" ]]; then
    if [[ -n "$msg" ]]; then
        git commit --amend -m "$msg"
    else
        git commit --amend --no-edit
    fi
else
    git commit -m "$msg"
fi

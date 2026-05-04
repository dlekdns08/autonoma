"""Goal recommender — Feature #11.

Single endpoint, LLM-backed::

    POST /api/inspire
    {
      "repo_url": "https://github.com/owner/repo",   # optional
      "file_tree": "src/foo.py\nsrc/bar.py\n...",     # optional
      "focus":    "feature"                            # optional
    }
    → {"suggestions": [{"text": "...", "effort": "small"}, ...]}

The Idle screen calls this when the user clicks "Inspire me" so the
LLM can propose 5 concrete next-step goals from a thin slice of repo
context. Either ``repo_url`` (we fetch a cheap summary from GitHub) or
``file_tree`` (caller-supplied, capped) must be present.

In-memory cache keyed by ``(repo_url, focus)`` keeps repeated clicks
within a 10 minute window from re-billing the LLM.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException

from autonoma.auth import User, require_active_user
from autonoma.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["inspire"])


# ── Constants ─────────────────────────────────────────────────────────

# 50 KB cap on the GitHub response — anything bigger is almost certainly
# a tarball or commit blob, not the cheap repo metadata we asked for.
_GITHUB_MAX_BYTES = 50 * 1024

# 8 KB cap on caller-supplied file tree. The LLM does not need the
# whole repo; a top-level listing fits comfortably under this.
_FILE_TREE_MAX_BYTES = 8 * 1024

# 10 minute TTL for the (repo_url, focus) cache.
_CACHE_TTL_SEC = 10 * 60

# Allowed focus values. Anything else → 422.
_ALLOWED_FOCUS = {"feature", "bugfix", "refactor", "test", "docs"}

# Effort tags the prompt asks the model to produce.
_EFFORT_VALUES = ("small", "medium", "large")

_INSPIRE_PROMPT = (
    "You are an experienced engineer. Given this project context, "
    "propose 5 concrete next-feature goals (one sentence each). "
    "Tag each with effort (small/medium/large)."
)


# ── Cache ─────────────────────────────────────────────────────────────

# (repo_url, focus) -> (expires_at_epoch, suggestions_list)
_cache: dict[tuple[str, str], tuple[float, list[dict[str, str]]]] = {}


def _cache_get(key: tuple[str, str]) -> list[dict[str, str]] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    expires_at, suggestions = entry
    if expires_at < time.time():
        _cache.pop(key, None)
        return None
    return suggestions


def _cache_put(key: tuple[str, str], suggestions: list[dict[str, str]]) -> None:
    _cache[key] = (time.time() + _CACHE_TTL_SEC, suggestions)


# ── GitHub fetch ──────────────────────────────────────────────────────


def _parse_repo_url(url: str) -> tuple[str, str]:
    """Return (owner, repo) for a GitHub URL or raise HTTPException 400."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in (
        "github.com",
        "www.github.com",
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "bad_repo_url",
                "message": "repo_url must be a https://github.com/owner/repo URL",
            },
        )
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "bad_repo_url",
                "message": "repo_url must include both owner and repo segments",
            },
        )
    owner, repo = parts[0], parts[1]
    # strip trailing .git if present
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


async def _fetch_github_context(repo_url: str) -> str:
    """Cheap fetch of repo metadata. Returns a short text blob.

    Uses ``api.github.com`` directly. If ``GH_TOKEN`` is in the env we
    hit ``/contents`` (top-level listing); otherwise we fall back to the
    anonymous ``/repos/{owner}/{repo}`` summary endpoint to stay under
    the unauth rate limit.
    """
    owner, repo = _parse_repo_url(repo_url)
    gh_token = os.environ.get("GH_TOKEN", "").strip()

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "autonoma-inspire/1",
    }
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
        url = f"https://api.github.com/repos/{owner}/{repo}/contents"
    else:
        url = f"https://api.github.com/repos/{owner}/{repo}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning(f"[inspire] GitHub fetch failed: {exc}")
        raise HTTPException(
            status_code=502,
            detail={
                "code": "github_fetch_failed",
                "message": f"Could not reach GitHub: {exc}",
            },
        ) from exc

    if resp.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "repo_not_found",
                "message": f"GitHub repo {owner}/{repo} not found",
            },
        )
    if resp.status_code == 403:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "github_rate_limited",
                "message": "GitHub rate limit hit; set GH_TOKEN or retry later",
            },
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "github_fetch_failed",
                "message": f"GitHub returned {resp.status_code}",
            },
        )

    body = resp.text
    if len(body) > _GITHUB_MAX_BYTES:
        body = body[:_GITHUB_MAX_BYTES]
    return body


# ── Response parsing ──────────────────────────────────────────────────

# Bullet markers we accept at line start: "-", "*", "1.", "1)", etc.
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
# Effort tag patterns: "(small)", "[medium]", " - large", "effort: small", etc.
_EFFORT_RE = re.compile(
    r"\b(?:effort\s*[:=]\s*)?[\(\[\-]?\s*(small|medium|large)\s*[\)\]]?",
    re.IGNORECASE,
)


def _parse_suggestions(raw: str) -> list[dict[str, str]]:
    """Split the LLM reply into ``{text, effort}`` rows.

    Strategy: first pass keeps lines that look like bullets; if we got
    fewer than 5 we fall back to non-empty lines so a model that drops
    the dash still gives us something useful. We always cap at 5.
    """
    lines = [ln.rstrip() for ln in raw.splitlines()]
    bullets: list[str] = []
    for ln in lines:
        m = _BULLET_RE.match(ln)
        if m:
            bullets.append(m.group(1).strip())

    if len(bullets) < 5:
        # Fallback: any non-empty, non-trivial line.
        bullets = [
            ln.strip()
            for ln in lines
            if ln.strip() and not ln.strip().startswith("#")
        ]

    out: list[dict[str, str]] = []
    for line in bullets:
        if not line:
            continue
        effort = "medium"
        m = _EFFORT_RE.search(line)
        if m:
            effort = m.group(1).lower()
            # Strip the matched effort tag out of the displayed text.
            line = (line[: m.start()] + line[m.end():]).strip()
        # Trim trailing punctuation orphans left by the strip.
        line = line.strip(" -—–:()[],.").strip()
        if not line:
            continue
        out.append({"text": line, "effort": effort})
        if len(out) >= 5:
            break

    return out


# ── LLM call ──────────────────────────────────────────────────────────


async def _call_llm(context: str, focus: str | None) -> str:
    # Lazy-import so importing the router doesn't pull the LLM stack
    # at app startup (matches how other routers gate it).
    from autonoma.llm import create_llm_client, llm_config_from_settings

    config = llm_config_from_settings()
    client = create_llm_client(config)

    user_msg_parts = [_INSPIRE_PROMPT]
    if focus:
        user_msg_parts.append(f"Focus area: {focus}.")
    user_msg_parts.append("Project context:")
    user_msg_parts.append(context)
    user_msg = "\n\n".join(user_msg_parts)

    response = await client.create(
        model=config.model,
        max_tokens=1024,
        temperature=0.4,
        system=_INSPIRE_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return (response.text or "").strip()


# ── Endpoint ──────────────────────────────────────────────────────────


@router.post("/api/inspire")
async def inspire(
    payload: dict[str, Any],
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    if not settings.inspire_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "inspire_disabled",
                "message": "Goal recommender is disabled by server config",
            },
        )

    repo_url = (payload.get("repo_url") or "").strip()
    file_tree = (payload.get("file_tree") or "").strip()
    focus_raw = payload.get("focus")
    focus = (focus_raw or "").strip().lower() if isinstance(focus_raw, str) else ""

    if not repo_url and not file_tree:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "missing_context",
                "message": "Provide either repo_url or file_tree",
            },
        )

    if focus and focus not in _ALLOWED_FOCUS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "bad_focus",
                "message": (
                    "focus must be one of: "
                    + ", ".join(sorted(_ALLOWED_FOCUS))
                ),
            },
        )

    cache_key = (repo_url, focus)
    if repo_url:
        cached = _cache_get(cache_key)
        if cached is not None:
            return {"suggestions": cached, "cached": True}

    if repo_url:
        context = await _fetch_github_context(repo_url)
    else:
        context = file_tree[:_FILE_TREE_MAX_BYTES]

    if not context.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "code": "empty_context",
                "message": "Project context was empty after trimming",
            },
        )

    try:
        raw = await _call_llm(context, focus or None)
    except HTTPException:
        raise
    except Exception as exc:
        # Log the full exception (with traceback) internally so an
        # operator can debug, but never echo the raw text out to the
        # client — provider responses can include API keys, prompt
        # fragments, or stack frames we don't want leaving the box.
        logger.exception("[inspire] LLM call failed")
        raise HTTPException(
            status_code=502,
            detail={
                "code": "inspire_failed",
                "message": "LLM request failed",
            },
        ) from exc

    suggestions = _parse_suggestions(raw)
    if not suggestions:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "inspire_unparseable",
                "message": "LLM reply did not contain any suggestions",
            },
        )

    if repo_url:
        _cache_put(cache_key, suggestions)

    return {"suggestions": suggestions, "cached": False}

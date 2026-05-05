"""Session cookie + CORS configuration helpers.

Extracted from ``autonoma.api`` so the main module stays focused on
routing. Re-exported from ``autonoma.api`` to preserve the existing
import surface used by handlers.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import Response as FastAPIResponse

from autonoma.auth import SESSION_COOKIE_NAME, issue_session_token
from autonoma.config import settings

logger = logging.getLogger(__name__)


def _resolve_cors_origins() -> list[str]:
    """Compose the CORS allow-list from the deployment environment.

    Dev mode hardcodes the localhost ports so the Next dev server and the
    docker-compose web container both work with zero config. Prod starts
    from an empty baseline and requires an explicit
    ``AUTONOMA_CORS_ALLOW_ORIGINS`` — wildcarding under
    ``allow_credentials=True`` is unsafe, so we never fall back to ``*``.
    """
    origins: list[str] = []
    if settings.environment == "development":
        # Dev origins; production origins should come from env via settings
        # (AUTONOMA_CORS_ALLOW_ORIGINS), never hardcoded here.
        origins.extend(
            [
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:3478",
                "http://127.0.0.1:3478",
            ]
        )
    extra = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
    for origin in extra:
        if origin not in origins:
            origins.append(origin)
    if not origins:
        logger.warning(
            "[cors] No origins configured for environment=%s. "
            "Set AUTONOMA_CORS_ALLOW_ORIGINS to the browser origin(s) "
            "that should be allowed to call this API.",
            settings.environment,
        )
    return origins


def _cookie_is_secure() -> bool:
    """Secure cookies in production, lax in development.

    We can't know whether we're behind HTTPS from Python alone, so treat
    the presence of ``session_secret`` as the signal: if an operator has
    set a durable secret they've configured this for real deployment.
    Tests and local dev that don't set one get non-Secure cookies so
    they work over plain http://localhost.
    """
    return bool(settings.session_secret)


def _cookie_samesite() -> Literal["lax", "strict"]:
    """Lax in development, strict once a session_secret is configured.

    Same signal as ``_cookie_is_secure`` — an operator who has set a durable
    secret has signed up for real deployment semantics. Lax is required for
    typical dev setups where the Next.js dev server (port 3000) and the
    FastAPI process (3479) are different origins but same site.
    """
    return "strict" if settings.session_secret else "lax"


def _set_session_cookie(response: FastAPIResponse, user_id: str) -> None:
    token = issue_session_token(user_id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_cookie_is_secure(),
        samesite=_cookie_samesite(),
        path="/",
    )


def _clear_session_cookie(response: FastAPIResponse) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_cookie_is_secure(),
        samesite=_cookie_samesite(),
    )

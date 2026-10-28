"""``/api/health`` liveness + readiness probe.

Extracted from ``autonoma.api``: paths, methods, auth dependencies,
status codes and behaviour are unchanged. Live session state stays in
``autonoma.api`` and is reached through lazy imports so importing this
router never triggers the module-load cycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    Request,
)

from autonoma.auth import (
    SESSION_COOKIE_NAME,
    read_session_token,
)
from autonoma.config import settings
from autonoma.db.users import (
    get_user_by_id,
)

if TYPE_CHECKING:
    from autonoma._api_ws import ConnectionManager
    from autonoma.api import SessionState

logger = logging.getLogger(__name__)

router = APIRouter()


def _live_sessions() -> dict[int, SessionState]:
    """The live session registry, owned by ``autonoma.api``.

    Imported lazily: ``autonoma.api`` includes this router at module
    load, so a top-level import here would be a cycle.
    """
    from autonoma.api import _sessions

    return _sessions


def _live_manager() -> ConnectionManager:
    """The WebSocket connection manager, owned by ``autonoma.api``."""
    from autonoma.api import manager

    return manager


@router.get("/api/health")
async def health(request: Request):
    """Liveness probe.

    Always returns ``200 {"status": "ok"}`` so Docker's HEALTHCHECK
    and external uptime monitors don't need to authenticate. Internal
    detail (active swarms, TTS provider, sessions count) is exposed
    only to logged-in users — anonymous callers couldn't use that
    info for anything except recon.
    """
    # Probe the cookie inline rather than requiring auth, so Docker
    # HEALTHCHECK + curl probes still get a 200. We just shed the
    # detail payload for unauthenticated callers.
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = read_session_token(cookie_token or "")
    user = await get_user_by_id(user_id) if user_id else None
    if user is None or user.status != "active":
        return {"status": "ok"}

    active_swarms = sum(
        1 for s in _live_sessions().values() if s.task is not None and not s.task.done()
    )
    # TTS status so the /voice page can distinguish "model still warming"
    # from "synthesis really failed". Cheap — no model load triggered.
    tts_info: dict[str, object] = {
        "provider": settings.tts_provider,
        "ready": settings.tts_provider
        not in (
            "omnivoice",
            "omnivoice-mlx",
            "vibevoice",
        ),
        "device": "",
        "dtype": "",
    }
    if settings.tts_provider == "omnivoice":
        try:
            from autonoma.tts_omnivoice import shared_client_status

            status_snap = shared_client_status()
            tts_info["ready"] = bool(status_snap["loaded"])
            tts_info["device"] = status_snap["device"]
            tts_info["dtype"] = status_snap["dtype"]
        except ImportError:
            tts_info["ready"] = False
    elif settings.tts_provider == "omnivoice-mlx":
        try:
            from autonoma.tts_omnivoice_mlx import shared_client_status as mlx_status

            status_snap = mlx_status()
            tts_info["ready"] = bool(status_snap["loaded"])
            tts_info["device"] = status_snap["device"]
            tts_info["dtype"] = status_snap["dtype"]
        except ImportError:
            tts_info["ready"] = False
    elif settings.tts_provider == "vibevoice":
        try:
            from autonoma.tts_vibevoice import shared_client_status as vv_status

            status_snap = vv_status()
            tts_info["ready"] = bool(status_snap["loaded"])
            tts_info["device"] = status_snap["device"]
            tts_info["dtype"] = status_snap["dtype"]
        except ImportError:
            tts_info["ready"] = False
    return {
        "status": "ok",
        "connections": len(_live_manager().connections),
        "sessions": len(_live_sessions()),
        "active_swarms": active_swarms,
        "tts": tts_info,
    }

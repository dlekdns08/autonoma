"""Model discovery routes (``/api/models*``).

Extracted from ``autonoma.api``: paths, methods, auth dependencies,
status codes and behaviour are unchanged. Live session state stays in
``autonoma.api`` and is reached through lazy imports so importing this
router never triggers the module-load cycle.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)

from autonoma.auth import (
    require_active_user,
    require_admin,
)
from autonoma.config import settings
from autonoma.db.users import (
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Model discovery ───────────────────────────────────────────────────────


@router.get("/api/models/{provider}")
async def list_models_server(
    provider: str,
    _admin: User = Depends(require_admin),
):
    """Return models available via the server-configured admin key.

    Used by the Admin tab to show what the server-side key can actually
    reach. Admin-gated: even though we never return the key bytes, the
    presence/absence of model IDs leaks whether a credential is
    configured at all — useful info for an attacker probing the server.
    """
    from autonoma.model_catalog import list_models

    if provider not in ("anthropic", "openai", "vllm"):
        raise HTTPException(status_code=400, detail="unknown provider")

    api_key = ""
    base_url = ""
    if provider == "anthropic":
        api_key = settings.anthropic_api_key
    elif provider == "openai":
        api_key = settings.openai_api_key
    elif provider == "vllm":
        api_key = settings.vllm_api_key
        base_url = settings.vllm_base_url

    models, is_live = await asyncio.to_thread(list_models, provider, api_key, base_url)
    return {"provider": provider, "is_live": is_live, "models": models}


@router.post("/api/models")
async def list_models_with_key(
    payload: dict[str, Any],
    _user: User = Depends(require_active_user),
):
    """Return models for a user-supplied key (used by the 'User API key' tab).

    Request body:
        {"provider": "anthropic"|"openai"|"vllm", "api_key": "...", "base_url": "..."}

    Auth-gated: anonymous callers shouldn't be able to mint requests
    against arbitrary OpenAI/Anthropic keys via this endpoint (it's a
    free SSRF-into-paid-API surface otherwise).
    """
    from autonoma.model_catalog import list_models

    provider = (payload.get("provider") or "").strip()
    api_key = (payload.get("api_key") or "").strip()
    base_url = (payload.get("base_url") or "").strip()

    if provider not in ("anthropic", "openai", "vllm"):
        raise HTTPException(status_code=400, detail="unknown provider")

    models, is_live = await asyncio.to_thread(list_models, provider, api_key, base_url)
    return {"provider": provider, "is_live": is_live, "models": models}

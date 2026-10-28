"""Account routes: ``/api/auth/*`` and ``/api/admin/users/*``.

Extracted from ``autonoma.api``: paths, methods, auth dependencies,
status codes and behaviour are unchanged. Live session state stays in
``autonoma.api`` and is reached through lazy imports so importing this
router never triggers the module-load cycle.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi import (
    Response as FastAPIResponse,
)
from fastapi import (
    status as http_status,
)

from autonoma._api_cookies import (
    _clear_session_cookie,
    _cookie_is_secure,
    _cookie_samesite,
    _set_session_cookie,
)
from autonoma.auth import (
    SESSION_COOKIE_NAME,
    hash_password,
    new_guest_user_id,
    require_active_user,
    require_admin,
    verify_password,
)
from autonoma.config import settings
from autonoma.db.users import (
    User,
    UserStatus,
    create_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user_status,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── FastAPI App ───────────────────────────────────────────────────────────

USERNAME_PATTERN = re.compile(r"^[a-z0-9_-]{3,32}$")


# ── /api/auth/* ───────────────────────────────────────────────────────


@router.post("/api/auth/signup", status_code=http_status.HTTP_201_CREATED)
async def auth_signup(payload: dict[str, Any]) -> dict[str, Any]:
    """Public signup. New users start as ``pending`` until an admin
    approves them. Returns 201 on success, 409 if taken, 400 if the
    input doesn't match the username/password rules."""
    username = str(payload.get("username") or "").strip().lower()
    password = str(payload.get("password") or "")
    if not USERNAME_PATTERN.match(username):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid_username",
        )
    if len(password) < 6:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid_password",
        )
    if await get_user_by_username(username) is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="username_taken",
        )
    try:
        password_hash = hash_password(password)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid_password",
        )
    await create_user(
        username=username,
        password_hash=password_hash,
        role="user",
        status="pending",
    )
    return {"status": "pending"}


@router.post("/api/auth/login")
async def auth_login(payload: dict[str, Any], response: FastAPIResponse) -> dict[str, Any]:
    """Username/password login. 200 + cookie on success."""
    username = str(payload.get("username") or "").strip().lower()
    password = str(payload.get("password") or "")
    if not username or not password:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="bad_credentials",
        )
    user = await get_user_by_username(username)
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="bad_credentials",
        )
    if user.status != "active":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="not_active",
        )
    _set_session_cookie(response, user.id)
    return {"user": user.public_dict()}


@router.post("/api/auth/logout", status_code=http_status.HTTP_204_NO_CONTENT)
async def auth_logout(response: FastAPIResponse) -> FastAPIResponse:
    """Unconditionally clear the session cookie. 204 even if no cookie
    was present (idempotent logout)."""
    _clear_session_cookie(response)
    response.status_code = http_status.HTTP_204_NO_CONTENT
    return response


async def _validate_guest_credentials(
    *,
    provider: str,
    api_key: str,
    base_url: str,
) -> tuple[bool, str | None]:
    """Ping the provider's ``/v1/models`` endpoint to verify the key works.

    We hit the cheapest authoritative endpoint each provider exposes:

    - **anthropic**: ``GET https://api.anthropic.com/v1/models`` with
      ``x-api-key`` header. Free, no token cost.
    - **openai**: ``GET https://api.openai.com/v1/models`` with
      ``Authorization: Bearer``. Free.
    - **vllm**: ``GET <base_url>/v1/models``. Validates that the
      self-hosted server is reachable; api_key is sent only if provided.

    We deliberately do NOT validate the model id against the listing —
    Anthropic in particular doesn't always surface every available model
    via ``/v1/models``, and an unknown model surfaces a clear error at
    swarm-start time anyway. Validating *just* the key keeps this from
    being a UX trap of its own.

    Returns ``(True, None)`` on success, ``(False, message)`` on failure
    where ``message`` is a Korean string suitable for the modal.
    """
    import httpx

    timeout = httpx.Timeout(10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as ac:
            if provider == "anthropic":
                r = await ac.get(
                    "https://api.anthropic.com/v1/models",
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
            elif provider == "openai":
                r = await ac.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            elif provider == "vllm":
                if not base_url:
                    return False, "vLLM 서버 URL이 필요합니다."
                headers: dict[str, str] = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                r = await ac.get(
                    f"{base_url.rstrip('/')}/v1/models",
                    headers=headers,
                )
            else:
                return False, f"지원하지 않는 프로바이더입니다: {provider}"
    except httpx.TimeoutException:
        return False, "프로바이더 응답 시간 초과 (10초). 네트워크를 확인해주세요."
    except httpx.HTTPError as exc:
        # Don't leak the underlying message verbatim — it can include
        # the URL with a leading "Bearer ..." in some httpx variants.
        logger.warning("[guest] credential validation network error: %s", type(exc).__name__)
        return False, "프로바이더에 연결할 수 없습니다."

    if r.status_code == 200:
        return True, None
    if r.status_code in (401, 403):
        return False, "API 키가 올바르지 않거나 권한이 없습니다."
    if r.status_code == 404 and provider == "vllm":
        return False, "vLLM 서버에서 /v1/models 경로를 찾을 수 없습니다. URL을 확인해주세요."
    return False, f"인증 확인에 실패했습니다 (HTTP {r.status_code})."


@router.post("/api/auth/guest")
async def auth_guest(
    payload: dict[str, Any],
    response: FastAPIResponse,
) -> dict[str, Any]:
    """Issue a guest session — no signup, no admin approval.

    Validates the user-supplied LLM credentials against the provider
    BEFORE issuing the cookie, so a bad key fails loudly here instead of
    silently turning into a confusing WebSocket error after the user has
    already entered the dashboard.

    On success, the cookie carries a synthetic ``user_id`` of the form
    ``guest:<uuid>`` that ``require_active_user`` recognises and inflates
    into an in-memory ``User`` (``role="user"``, ``status="active"``).
    Guests still authenticate the WebSocket separately via the existing
    ``type=user`` ``authenticate`` command — this endpoint never hands
    out the server's API key.
    """
    from datetime import UTC, datetime

    provider = str(payload.get("provider") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    model = str(payload.get("model") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()

    # Mirror the WS ``type=user`` validation rules so behaviour is
    # symmetric across the two entry points.
    if provider not in ("anthropic", "openai", "vllm"):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "invalid_input",
                "message": f"지원하지 않는 프로바이더입니다: {provider or '(없음)'}",
            },
        )
    if not model:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"reason": "invalid_input", "message": "모델명을 입력해주세요."},
        )
    if provider != "vllm" and not api_key:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"reason": "invalid_input", "message": "API 키를 입력해주세요."},
        )
    if provider == "vllm" and not base_url:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"reason": "invalid_input", "message": "vLLM 서버 URL을 입력해주세요."},
        )

    ok, err_msg = await _validate_guest_credentials(
        provider=provider, api_key=api_key, base_url=base_url
    )
    if not ok:
        # 401 — bad credentials. The detail body carries a Korean message
        # that the frontend surfaces as-is, plus a stable ``reason`` slug
        # for any future programmatic handling.
        logger.info(
            "[guest] credential validation failed (provider=%s, model=%s, key_len=%d): %s",
            provider,
            model,
            len(api_key),
            err_msg,
        )
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "invalid_credentials", "message": err_msg or "인증 실패"},
        )

    user_id = new_guest_user_id()
    _set_session_cookie(response, user_id)
    short = user_id.removeprefix("guest:").split("-", 1)[0]
    now = datetime.now(UTC).isoformat()
    logger.info(
        "[guest] session issued (user_id=%s, provider=%s, model=%s)",
        user_id,
        provider,
        model,
    )
    return {
        "user": {
            "id": user_id,
            "username": f"guest-{short}",
            "role": "user",
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "is_guest": True,
        }
    }


@router.get("/api/auth/me")
async def auth_me(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the current active user or 401/403."""
    return {"user": user.public_dict()}


@router.get("/api/debug/auth")
async def debug_auth(request: Request) -> dict[str, Any]:
    """Dev-only sanity check for auth wiring.

    Returns 404 when ``session_secret`` is set — the presence of a durable
    secret is our "this is a real deployment" signal, and a probe endpoint
    that enumerates bootstrap state has no business on a prod server.
    """
    if settings.session_secret:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND)
    admin_row = await get_user_by_username("admin")
    return {
        "admin_password_configured": bool(settings.admin_password),
        "session_secret_configured": False,
        "admin_user_exists": admin_row is not None,
        "admin_user_active": (admin_row is not None and admin_row.status == "active"),
        "cookie_received": SESSION_COOKIE_NAME in request.cookies,
        "cookie_samesite": _cookie_samesite(),
        "cookie_secure": _cookie_is_secure(),
    }


# ── /api/admin/users/* ────────────────────────────────────────────────


@router.get("/api/admin/users")
async def admin_list_users(
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    users = await list_users()
    return {"users": [u.public_dict() for u in users]}


async def _transition_user(
    user_id: str,
    *,
    required_status: set[str] | None,
    new_status: UserStatus,
) -> None:
    """Apply a status transition, 404 if missing, 409 if current status
    isn't in ``required_status`` (when specified)."""
    user = await get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="user_not_found",
        )
    if required_status is not None and user.status not in required_status:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="invalid_state_transition",
        )
    await update_user_status(user_id, new_status)


@router.post(
    "/api/admin/users/{user_id}/approve",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def admin_approve_user(
    user_id: str,
    _admin: User = Depends(require_admin),
) -> FastAPIResponse:
    await _transition_user(user_id, required_status={"pending"}, new_status="active")
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/admin/users/{user_id}/deny",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def admin_deny_user(
    user_id: str,
    _admin: User = Depends(require_admin),
) -> FastAPIResponse:
    await _transition_user(user_id, required_status={"pending"}, new_status="disabled")
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/admin/users/{user_id}/disable",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def admin_disable_user(
    user_id: str,
    _admin: User = Depends(require_admin),
) -> FastAPIResponse:
    await _transition_user(user_id, required_status={"active"}, new_status="disabled")
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/admin/users/{user_id}/reactivate",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def admin_reactivate_user(
    user_id: str,
    _admin: User = Depends(require_admin),
) -> FastAPIResponse:
    await _transition_user(user_id, required_status={"disabled"}, new_status="active")
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)

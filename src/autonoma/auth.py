"""Cookie-based session authentication for the FastAPI API.

Design
──────
- Cookie name: ``autonoma_session``. HttpOnly, Secure, SameSite=Strict.
- Payload: an ``itsdangerous.URLSafeSerializer`` token carrying
  ``{"user_id": <uuid>}``. Tampering → verification raises ``BadSignature``
  and we treat the request as unauthenticated.
- Password hashing: ``passlib[bcrypt]``.
- Secret: ``settings.session_secret`` (env ``AUTONOMA_SESSION_SECRET``).
  If unset, we mint a random dev-only secret at import time and log a
  loud warning so no production deployment silently falls back.

FastAPI deps:
- ``current_user`` — best-effort lookup; returns ``None`` when the cookie
  is missing or invalid. Does not raise.
- ``require_active_user`` — 401 on no cookie / invalid user, 403 when the
  user exists but status != active.
- ``require_admin`` — everything ``require_active_user`` does, plus a
  403 if the user's role is not ``admin``.
"""

from __future__ import annotations

import logging
import secrets
from typing import Final

import bcrypt
from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer

from autonoma.config import settings
from autonoma.db.users import User, get_user_by_id

logger = logging.getLogger(__name__)


SESSION_COOKIE_NAME: Final[str] = "autonoma_session"
_SESSION_SALT: Final[str] = "autonoma.auth.session.v1"


def _resolve_session_secret() -> str:
    """Return the configured secret, or fall back to a random dev one.

    In production, leaving ``AUTONOMA_SESSION_SECRET`` unset means every
    process restart invalidates all existing cookies — noisy, but safe.
    We log at WARNING so ops notice immediately in logs.
    """
    configured = settings.session_secret
    if configured:
        return configured
    generated = secrets.token_urlsafe(32)
    logger.warning(
        "AUTONOMA_SESSION_SECRET is not set; using an ephemeral dev secret. "
        "All sessions will be invalidated on restart. Set a stable value "
        "via the AUTONOMA_SESSION_SECRET environment variable in production."
    )
    return generated


_SESSION_SECRET: Final[str] = _resolve_session_secret()
_serializer: Final[URLSafeSerializer] = URLSafeSerializer(
    _SESSION_SECRET, salt=_SESSION_SALT
)


# ── Password helpers ──────────────────────────────────────────────────
# We use the ``bcrypt`` library directly rather than going through
# ``passlib`` because current passlib releases don't play cleanly with
# bcrypt >= 4 (they try to introspect ``bcrypt.__about__`` which was
# removed upstream). A thin wrapper here keeps the call sites ergonomic
# while avoiding that compatibility trap.


# bcrypt's hashpw has a hard 72-byte limit on the password. We encode
# with UTF-8 and truncate just in case — still 72 bytes of entropy is
# well over what bcrypt's security model needs.
_BCRYPT_MAX_BYTES: Final[int] = 72


def _encode_password(password: str) -> bytes:
    pw = password.encode("utf-8")
    return pw[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Return a bcrypt hash of ``password`` (ascii string).

    Raises ValueError if the password is empty — bcrypt accepts empty
    input but we reject it here as a policy decision.
    """
    if not password:
        raise ValueError("password must not be empty")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(_encode_password(password), salt)
    return hashed.decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Return True iff ``password`` matches the stored hash."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            _encode_password(password), password_hash.encode("ascii")
        )
    except (ValueError, TypeError):
        # Malformed hash → treat as mismatch rather than 500ing.
        return False


# ── Cookie sign / verify ──────────────────────────────────────────────


def issue_session_token(user_id: str) -> str:
    """Return a signed token suitable for the session cookie value."""
    return _serializer.dumps({"user_id": user_id})


def read_session_token(token: str) -> str | None:
    """Validate a signed token and return the embedded user_id, or None."""
    if not token:
        return None
    try:
        payload = _serializer.loads(token)
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    user_id = payload.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        return None
    return user_id


# ── FastAPI deps ──────────────────────────────────────────────────────


async def current_user(request: Request) -> User | None:
    """Return the authenticated user, or None. Never raises.

    Reads the ``autonoma_session`` cookie from ``request`` rather than
    taking it as a parameter so it can be dropped into endpoints that
    don't care about auth (e.g. ``/api/auth/me`` which returns 401 on
    None itself).
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    user_id = read_session_token(token)
    if not user_id:
        return None
    return await get_user_by_id(user_id)


async def require_active_user(
    request: Request,
    autonoma_session: str | None = Cookie(default=None),
) -> User:
    """401 if no valid session, 403 if the user's status isn't ``active``."""
    # We read from Cookie(...) above so the cookie name is part of the
    # OpenAPI schema, but fall through to the request cookie jar so the
    # dep works identically when called manually in tests.
    token = autonoma_session or request.cookies.get(SESSION_COOKIE_NAME)
    user_id = read_session_token(token or "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication_required",
        )
    user = await get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication_required",
        )
    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not_active",
        )
    return user


async def require_admin(
    request: Request,
    autonoma_session: str | None = Cookie(default=None),
) -> User:
    """Active user whose role is admin. Same 401/403 semantics as
    ``require_active_user``, plus 403 when role != admin."""
    user = await require_active_user(request, autonoma_session)
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin_required",
        )
    return user

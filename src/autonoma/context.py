"""Process-wide ContextVars shared across autonoma modules.

Kept in a separate module so that ``api.py``, ``tts_worker.py``, and
agent code can all import it without creating circular dependencies.
"""
from __future__ import annotations

import contextvars
from typing import Callable, Optional

# Carries the current *room* (session) id down through every awaitable
# that runs inside a swarm task.  Bus handlers and TTS workers read this
# so they can route events / audio to the correct WebSocket session rather
# than broadcasting to every connected client.
#
# The value is set once per swarm run in ``api.py:_run_swarm_task`` and
# is then automatically inherited by every ``asyncio.Task`` spawned
# within that call tree.
current_session_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "autonoma_current_session_id", default=None
)


# ── Session → owner resolver ─────────────────────────────────────────
#
# Some bus listeners (cutscene trigger tap, scheduler fire dispatch)
# need to know *who* owns the currently-active session so they can
# scope their reaction to that user's resources. ``api.py`` is the only
# place that knows about the ``_sessions`` dict, but the listeners live
# in routers that import each other freely. To avoid a circular import
# we expose a process-wide indirection: ``api.py`` registers a resolver
# at startup, and any caller can look up the owner without importing
# ``api`` directly.

_owner_resolver: Optional[Callable[[int], Optional[str]]] = None


def set_session_owner_resolver(
    fn: Optional[Callable[[int], Optional[str]]],
) -> None:
    """Install (or clear) the session→owner lookup callable.

    Called from ``api.py`` once at module load. The function should
    accept a session id and return the owner's user id, or ``None`` if
    the session is anonymous / unknown.
    """
    global _owner_resolver
    _owner_resolver = fn


def lookup_session_owner(session_id: int | None) -> str | None:
    """Resolve ``session_id`` to its owner. Returns ``None`` if the
    session has no resolver registered or no owner attached."""
    if session_id is None or _owner_resolver is None:
        return None
    try:
        return _owner_resolver(session_id)
    except Exception:
        return None

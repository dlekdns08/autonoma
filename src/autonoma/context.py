"""Process-wide ContextVars shared across autonoma modules.

Kept in a separate module so that ``api.py``, ``tts_worker.py``, and
agent code can all import it without creating circular dependencies.
"""
from __future__ import annotations

import contextvars

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

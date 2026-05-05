"""Headless swarm launcher — runs swarms without a connected WebSocket.

Extracted from ``autonoma.api`` so the main module can stay focused on
HTTP/WS routing. The two functions that depend on ``SessionState`` /
``_sessions`` / ``_run_swarm`` lazy-import them at call time to avoid a
circular import (``api.py`` re-exports the symbols below at module load).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from typing import Any

from autonoma.event_bus import bus
from autonoma.harness.policy import HarnessPolicyContent
from autonoma.llm import llm_config_from_settings

logger = logging.getLogger(__name__)


class _HeadlessWebSocket:
    """Drop-everything WebSocket stub for backend-only runs.

    A *write-only* sink — code that awaits ``receive_text`` on it will
    hang forever, but the headless launcher never goes near the inbound
    loop, so that path is never exercised.
    """

    client_state: int = 1  # CONNECTED — keeps starlette guards happy

    async def accept(self, *_a: Any, **_kw: Any) -> None:
        return None

    async def send_text(self, _msg: str) -> None:
        return None

    async def send_json(self, _payload: Any) -> None:
        return None

    async def close(self, *_a: Any, **_kw: Any) -> None:
        return None


# Synthetic session ids for headless runs come from a negative-ranged
# counter so they can never collide with the positive ids issued by
# ``_next_session_id`` for real WebSocket connections. We use
# ``itertools.count`` (which is atomic in CPython) so concurrent
# headless launches can't race the read-modify-write of a plain
# ``int -= 1`` global. Step is -1 starting from -1.
_headless_id_counter: itertools.count[int] = itertools.count(-1, -1)


def _next_headless_session_id() -> int:
    return next(_headless_id_counter)


async def _run_swarm_headless(
    *,
    goal: str,
    owner_user_id: str,
    preset_id: str = "",
    max_rounds: int = 30,
    label: str = "",
) -> int:
    """Run a swarm without a connected WebSocket session.

    Used by the cron scheduler (and any future backend trigger) so
    "rebuild the docs every night" doesn't require a tab to be open.
    The run goes through the same ``_run_swarm`` loop as a foreground
    job — checkpoints, run summary, replay data, and observability
    rollups all populate the same tables as a normal run.

    Returns the synthetic session id (always negative) so the caller
    can correlate logs / replay URLs.
    """
    # Lazy import: ``SessionState``/``_sessions``/``_run_swarm`` live in
    # ``autonoma.api`` which itself imports from this module. Resolving
    # them at call time breaks the cycle.
    from autonoma.api import SessionState, _run_swarm, _sessions
    from autonoma.context import current_session_id as _current_session_id

    sid = _next_headless_session_id()
    sess = SessionState(
        ws=_HeadlessWebSocket(),  # type: ignore[arg-type]
        session_id=sid,
        owner_user_id=owner_user_id,
        room_id=sid,
    )
    _sessions[sid] = sess

    # Resolve the policy from the preset if one was named, otherwise
    # leave it None so the swarm picks up the system default. The
    # admin-only flag is False — scheduled runs don't get to flip
    # admin-only knobs even if the operator marked the preset admin-y.
    policy: HarnessPolicyContent | None = None
    overrides: dict[str, Any] | None = None
    if preset_id:
        try:
            from autonoma.db.harness_policies import get_policy_by_id

            preset = await get_policy_by_id(preset_id)
            if preset is not None:
                policy = HarnessPolicyContent.model_validate(preset.content)
        except Exception as exc:
            logger.warning(
                "[headless] preset %s lookup failed (%s) — using defaults",
                preset_id,
                exc,
            )

    # Use whatever provider config the operator has in settings.
    llm_config = llm_config_from_settings()

    logger.info(
        "[headless] launching session=%s owner=%s preset=%s label=%s goal=%r",
        sid,
        owner_user_id,
        preset_id or "default",
        label or "-",
        goal[:80],
    )

    # Tag the event loop's contextvar so bus emits originating in this
    # run get routed to the right session — exactly mirroring the
    # foreground ``start`` command's setup.
    import contextvars as _cv

    ctx = _cv.copy_context()
    ctx.run(_current_session_id.set, sid)

    async def _runner() -> None:
        try:
            await _run_swarm(
                session_id=sid,
                goal=goal,
                max_rounds=max_rounds,
                llm_config=llm_config,
                policy=policy,
                preset_id=preset_id or None,
                overrides=overrides,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "[headless:%s] unhandled error during _run_swarm "
                "(preset=%s, max_rounds=%s, goal=%r)",
                sid,
                preset_id or None,
                max_rounds,
                (goal[:80] + "...") if len(goal) > 80 else goal,
            )
        finally:
            _sessions.pop(sid, None)

    asyncio.create_task(_runner(), context=ctx, name=f"headless-swarm-{sid}")
    return sid


async def _on_schedule_fire_requested(**data: Any) -> None:
    """Bus handler: a schedule fired → kick off a headless swarm run.

    Dispatches through ``autonoma.api._run_swarm_headless`` (the
    re-export) rather than the local symbol so tests that
    ``monkeypatch.setattr(api_module, "_run_swarm_headless", ...)`` can
    still substitute the runner — the substitution rebinds the
    attribute on ``autonoma.api``, not on this module.
    """
    goal = str(data.get("goal") or "").strip()
    owner = str(data.get("owner") or "").strip()
    preset_id = str(data.get("preset_id") or "").strip()
    if not goal or not owner:
        logger.warning(
            "[headless] dropping schedule.fire_requested with empty goal/owner"
        )
        return
    from autonoma import api as _api

    sid = await _api._run_swarm_headless(
        goal=goal,
        owner_user_id=owner,
        preset_id=preset_id,
        label=f"schedule:{data.get('schedule_id')}",
    )
    await bus.emit(
        "schedule.fire_dispatched",
        schedule_id=data.get("schedule_id"),
        session_id=sid,
        owner=owner,
    )

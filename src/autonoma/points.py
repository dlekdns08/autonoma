"""Channel-points economy — viewer wallet (MVP).

Viewers earn points by watching (``/api/points/heartbeat`` every 60s
grants 5 pts) and by voting on live quests (+2 pts per successful vote,
wired in ``routers.quests``). They spend points on world actions —
currently only ``/api/points/spend/cookie`` (cost: 50 pts) which drops a
fortune cookie on a named agent's tile via the same ``fortune.given`` bus
event the WS ``/cookie`` debug command uses.

Persistence is a single tiny table (``viewer_points``) keyed by the
session/user id. We don't keep a transaction log here — for the MVP a
running balance is enough; an audit trail can land in a follow-up if the
operator wants to chase fraud or balance bugs.

Heartbeat rate-limit
────────────────────
The endpoint is generous about being called too often — Page Visibility
API teardown can fire dupes — so we keep a per-process ``last_heartbeat``
dict that swallows any tick faster than 50 seconds since the last
successful credit. (A bit under the 60s nominal interval so a slightly
fast client clock doesn't starve out a legit user.)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import viewer_points

logger = logging.getLogger(__name__)


# ── Economy constants ────────────────────────────────────────────────
HEARTBEAT_REWARD: int = 5
HEARTBEAT_INTERVAL_S: float = 60.0
# Soft floor: ignore heartbeats fired within this window of the last
# credited one. Slightly under HEARTBEAT_INTERVAL_S so a fast client
# clock doesn't lose the legit credit, but still high enough that a
# rogue script can't farm at 100hz.
HEARTBEAT_MIN_GAP_S: float = 50.0

VOTE_REWARD: int = 2

COOKIE_COST: int = 50


class InsufficientBalance(Exception):
    """Raised by :func:`spend` when the viewer can't afford the action."""

    code = "insufficient_balance"


# ── Heartbeat anti-farm ───────────────────────────────────────────────
# Per-process. ``viewer_id`` → last monotonic timestamp we credited.
# Lives here (not on ``viewer_points``) because we don't want to bump
# ``updated_at`` on every ignored tick; the table only mutates when we
# actually grant points.
_last_heartbeat: dict[str, float] = {}


async def _get_balance_for_update(conn, viewer_id: str) -> int:
    """Read the balance row, locking it implicitly for the in-flight tx.

    SQLite's serialized writer model means the ``UPDATE`` we'll run
    afterwards already blocks any other writer, so this is really just
    a read — but we phrase it as "for update" semantically so a future
    Postgres port keeps working.
    """
    row = (
        await conn.execute(
            select(viewer_points.c.balance).where(
                viewer_points.c.viewer_id == str(viewer_id)
            )
        )
    ).first()
    if row is None:
        return 0
    return int(row[0] or 0)


async def get_balance(viewer_id: str) -> int:
    """Return the viewer's current points balance, 0 if no row yet."""
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        return await _get_balance_for_update(conn, viewer_id)


async def _upsert_delta(viewer_id: str, delta: int) -> int:
    """Add ``delta`` (may be negative) to ``viewer_id``'s balance.

    Returns the new balance. Inserts a row at ``delta`` if one doesn't
    exist yet (floored at 0 — we don't persist a negative balance).
    Caller is responsible for refusing the spend up front when funds are
    insufficient; this function will floor at 0 defensively.
    """
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        current = await _get_balance_for_update(conn, viewer_id)
        new_balance = max(0, current + int(delta))
        if current == 0 and not (
            await conn.execute(
                select(viewer_points.c.viewer_id).where(
                    viewer_points.c.viewer_id == str(viewer_id)
                )
            )
        ).first():
            try:
                await conn.execute(
                    insert(viewer_points).values(
                        viewer_id=str(viewer_id),
                        balance=new_balance,
                    )
                )
            except IntegrityError:
                # Race: another tx inserted the row between our select
                # and insert. Fall through to the UPDATE branch.
                await conn.execute(
                    update(viewer_points)
                    .where(viewer_points.c.viewer_id == str(viewer_id))
                    .values(balance=new_balance)
                )
        else:
            await conn.execute(
                update(viewer_points)
                .where(viewer_points.c.viewer_id == str(viewer_id))
                .values(balance=new_balance)
            )
    return new_balance


async def credit(viewer_id: str, amount: int) -> int:
    """Grant ``amount`` points to ``viewer_id``. Returns new balance."""
    if amount <= 0:
        return await get_balance(viewer_id)
    return await _upsert_delta(viewer_id, int(amount))


async def credit_heartbeat(viewer_id: str) -> tuple[int, bool]:
    """Maybe grant a heartbeat reward.

    Returns ``(balance, granted)``. ``granted`` is False when the
    request was rate-limited (too soon after the last credit) — the
    caller can still return the unchanged balance to the client.
    """
    now = time.monotonic()
    last = _last_heartbeat.get(viewer_id)
    if last is not None and (now - last) < HEARTBEAT_MIN_GAP_S:
        return await get_balance(viewer_id), False
    _last_heartbeat[viewer_id] = now
    new_balance = await credit(viewer_id, HEARTBEAT_REWARD)
    return new_balance, True


async def spend(viewer_id: str, cost: int) -> int:
    """Deduct ``cost`` from ``viewer_id``. Raises :class:`InsufficientBalance`
    when the viewer can't afford it (the row is left untouched).

    Returns the new balance on success.
    """
    if cost <= 0:
        return await get_balance(viewer_id)
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        current = await _get_balance_for_update(conn, viewer_id)
        if current < cost:
            raise InsufficientBalance(
                f"need {cost} pts, have {current}"
            )
        new_balance = current - int(cost)
        await conn.execute(
            update(viewer_points)
            .where(viewer_points.c.viewer_id == str(viewer_id))
            .values(balance=new_balance)
        )
    return new_balance


def reset_heartbeat_cache() -> None:
    """Test hook: drop the per-process heartbeat rate-limit memory."""
    _last_heartbeat.clear()


__all__ = [
    "InsufficientBalance",
    "HEARTBEAT_REWARD",
    "HEARTBEAT_INTERVAL_S",
    "VOTE_REWARD",
    "COOKIE_COST",
    "get_balance",
    "credit",
    "credit_heartbeat",
    "spend",
    "reset_heartbeat_cache",
]

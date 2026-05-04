"""Viewer betting — Feature #4.

Channel-points-style markets where viewers stake play-money on
swarm-related yes/no (or n-of-many) questions. The host opens a market,
viewers place bets while the round is in flight, the host locks the
market just before resolution, and a final ``resolve_market`` call pays
the winners.

State machine
─────────────
::

    open ──► locked ──► resolved
                │
                └──► cancelled    (terminal, not implemented in this
                                   module — schema reserves the slot)

Persistence lives in the ``viewer_bets`` (markets) and
``viewer_bet_entries`` (per-viewer bets) tables, both created in
migration 012. Uniqueness is enforced at the DB layer:

  * ``uq_viewer_bet_market`` — one market per ``(session_id, market_id)``.
  * ``uq_bet_entry``         — one entry per
    ``(market_id, session_id, viewer_id)`` so a viewer can't double-down
    on the same market.

Bus events emitted from this module:

  * ``betting.market_opened``    on :func:`open_market`
  * ``betting.bet_placed``       on :func:`place_bet`
  * ``betting.market_locked``    on :func:`lock_market`
  * ``betting.market_resolved``  on :func:`resolve_market` (carries the
    payout summary so subscribers don't need a follow-up DB hit)

The only randomness in this file is the shuffle of insert order; the
payout function is fully deterministic given the entries table and the
``winning_option`` argument. Tests must be able to reproduce a settled
round bit-for-bit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import viewer_bet_entries, viewer_bets
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────

# Every viewer starts each session with this much play-money. The
# balance helper subtracts stakes and adds payouts on top of this.
STARTING_BALANCE: int = 1000

# Winners take their stake back times this multiplier; losers get 0.
# A flat multiplier keeps the math obvious and the UI honest — no need
# to display dynamic odds based on the pool, which would make the
# leaderboard (net = sum(payout) - sum(stake)) hard to read.
PAYOUT_MULTIPLIER: int = 3

# The only stake values the API accepts. Keeping this small caps the
# blast radius of a runaway script — there's no "all in", no "1000",
# just three preset chips.
ALLOWED_STAKES: frozenset[int] = frozenset({10, 50, 100})

# Market lifecycle states. Stored as plain strings on
# ``viewer_bets.status`` (VARCHAR(16)).
STATUS_OPEN: str = "open"
STATUS_LOCKED: str = "locked"
STATUS_RESOLVED: str = "resolved"
STATUS_CANCELLED: str = "cancelled"


# ── Public dataclasses ────────────────────────────────────────────────


@dataclass
class Market:
    """Plain-data view of a ``viewer_bets`` row.

    ``id`` is the autoincrement DB primary key; ``market_id`` is the
    caller-supplied stable handle that the API uses everywhere else.
    Both are exposed because the router needs ``market_id`` for routing
    and ``id`` for joins.
    """

    id: str
    session_id: int
    market_id: str
    question: str
    status: str
    closes_at_round: int
    opened_at: datetime
    resolved_at: datetime | None
    winning_option: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "market_id": self.market_id,
            "question": self.question,
            "status": self.status,
            "closes_at_round": self.closes_at_round,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "resolved_at": (
                self.resolved_at.isoformat() if self.resolved_at else None
            ),
            "winning_option": self.winning_option,
        }


@dataclass
class Entry:
    """Plain-data view of a ``viewer_bet_entries`` row.

    ``payout`` is 0 until the market is resolved; on resolution we
    rewrite the column on each row so callers can build a leaderboard
    with a single ``SUM(payout) - SUM(stake)`` aggregate.
    """

    id: int
    market_id: str
    session_id: int
    viewer_id: str
    display_name: str
    option: str
    stake: int
    placed_at: datetime
    payout: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "market_id": self.market_id,
            "session_id": self.session_id,
            "viewer_id": self.viewer_id,
            "display_name": self.display_name,
            "option": self.option,
            "stake": self.stake,
            "placed_at": self.placed_at.isoformat() if self.placed_at else None,
            "payout": self.payout,
        }


# ── Internal helpers ──────────────────────────────────────────────────


def _market_from_row(row: Any) -> Market:
    m = row._mapping if hasattr(row, "_mapping") else row
    return Market(
        id=str(m["id"]),
        session_id=int(m["session_id"]),
        market_id=str(m["market_id"]),
        question=str(m["question"]),
        status=str(m["status"]),
        closes_at_round=int(m["closes_at_round"]),
        opened_at=m["opened_at"],
        resolved_at=m["resolved_at"],
        winning_option=str(m["winning_option"] or ""),
    )


def _entry_from_row(row: Any) -> Entry:
    m = row._mapping if hasattr(row, "_mapping") else row
    return Entry(
        id=int(m["id"]),
        market_id=str(m["market_id"]),
        session_id=int(m["session_id"]),
        viewer_id=str(m["viewer_id"]),
        display_name=str(m["display_name"] or ""),
        option=str(m["option"]),
        stake=int(m["stake"]),
        placed_at=m["placed_at"],
        payout=int(m["payout"]),
    )


async def _fetch_market(session_id: int, market_id: str) -> Market | None:
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(viewer_bets)
                .where(viewer_bets.c.session_id == int(session_id))
                .where(viewer_bets.c.market_id == str(market_id))
            )
        ).first()
    return _market_from_row(row) if row else None


# ── Public API ────────────────────────────────────────────────────────


async def open_market(
    session_id: int,
    market_id: str,
    question: str,
    closes_at_round: int,
) -> Market:
    """Create a new ``open`` market and broadcast the open event.

    The ``(session_id, market_id)`` unique constraint means re-opening
    the same handle in the same session raises ``IntegrityError``. We
    surface that as ``ValueError("market_already_exists")`` so the
    router can map it to a 409 without leaking SQLAlchemy classes.
    """
    await init_db()
    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(
                insert(viewer_bets).values(
                    session_id=int(session_id),
                    market_id=str(market_id),
                    question=str(question or ""),
                    closes_at_round=int(closes_at_round),
                    status=STATUS_OPEN,
                    winning_option="",
                )
            )
    except IntegrityError as exc:
        raise ValueError("market_already_exists") from exc

    market = await _fetch_market(session_id, market_id)
    if market is None:
        # Should be impossible: we just inserted under a transaction.
        raise RuntimeError("market vanished after insert")

    await bus.emit(
        "betting.market_opened",
        session_id=int(session_id),
        market_id=str(market_id),
        question=market.question,
        closes_at_round=int(closes_at_round),
    )
    logger.info(
        "[betting] opened market_id=%s session_id=%d closes_at_round=%d",
        market_id, session_id, closes_at_round,
    )
    return market


async def list_open_markets(session_id: int) -> list[Market]:
    """Return every market in this session whose status is ``open``.

    Ordered by ``opened_at DESC`` so the most-recently-opened market
    leads the widget — viewers see the freshest action first. Locked
    and resolved markets are deliberately excluded; the host is expected
    to keep an "archive" view server-side if they ever want history.
    """
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(viewer_bets)
                .where(viewer_bets.c.session_id == int(session_id))
                .where(viewer_bets.c.status == STATUS_OPEN)
                .order_by(viewer_bets.c.opened_at.desc())
            )
        ).all()
    return [_market_from_row(r) for r in rows]


async def place_bet(
    session_id: int,
    market_id: str,
    viewer_id: str,
    display_name: str,
    option: str,
    stake: int,
) -> Entry:
    """Insert a single viewer's wager on a market.

    Validation order matters:

      1. ``stake`` must be one of :data:`ALLOWED_STAKES` — cheap check.
      2. The market must exist and be ``status="open"`` — DB hit.
      3. The unique constraint enforces "one bet per viewer per market";
         if it fires we raise ``ValueError("already_bet")``.

    Emits ``betting.bet_placed`` on success so the OBS overlay / TTS
    commentator can react in real time without polling.
    """
    if int(stake) not in ALLOWED_STAKES:
        raise ValueError("invalid_stake")

    market = await _fetch_market(session_id, market_id)
    if market is None or market.status != STATUS_OPEN:
        raise ValueError("market_not_open")

    await init_db()
    engine = get_engine()
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                insert(viewer_bet_entries).values(
                    market_id=str(market_id),
                    session_id=int(session_id),
                    viewer_id=str(viewer_id),
                    display_name=str(display_name or ""),
                    option=str(option),
                    stake=int(stake),
                    payout=0,
                )
            )
            entry_id = int(result.inserted_primary_key[0])
    except IntegrityError as exc:
        # ``uq_bet_entry`` (market_id, session_id, viewer_id).
        raise ValueError("already_bet") from exc

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(viewer_bet_entries).where(
                    viewer_bet_entries.c.id == entry_id
                )
            )
        ).first()
    if row is None:
        raise RuntimeError("bet entry vanished after insert")
    entry = _entry_from_row(row)

    await bus.emit(
        "betting.bet_placed",
        session_id=int(session_id),
        market_id=str(market_id),
        viewer_id=str(viewer_id),
        display_name=entry.display_name,
        option=entry.option,
        stake=entry.stake,
    )
    return entry


async def lock_market(session_id: int, market_id: str) -> bool:
    """Flip the market from ``open`` to ``locked``.

    Returns ``True`` if a row was actually updated. The transition is
    guarded by ``status="open"`` so a second lock call (or a lock after
    resolve) is a silent no-op and returns ``False``.

    Emits ``betting.market_locked`` on success.
    """
    await init_db()
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            update(viewer_bets)
            .where(viewer_bets.c.session_id == int(session_id))
            .where(viewer_bets.c.market_id == str(market_id))
            .where(viewer_bets.c.status == STATUS_OPEN)
            .values(status=STATUS_LOCKED)
        )
    if result.rowcount == 0:
        return False

    await bus.emit(
        "betting.market_locked",
        session_id=int(session_id),
        market_id=str(market_id),
    )
    logger.info(
        "[betting] locked market_id=%s session_id=%d", market_id, session_id,
    )
    return True


async def resolve_market(
    session_id: int,
    market_id: str,
    winning_option: str,
) -> dict[str, Any]:
    """Settle every entry on the market and return a summary dict.

    Behaviour:

      * Each entry whose ``option == winning_option`` has its
        ``payout`` column set to ``stake * PAYOUT_MULTIPLIER``.
      * Losing entries get ``payout = 0`` (explicit write so a re-resolve
        with a different ``winning_option`` settles correctly).
      * The market row flips to ``status="resolved"``,
        ``winning_option`` and ``resolved_at`` are written.

    The summary dict has the shape the bus event and the HTTP response
    both consume::

        {
          "market_id":     str,
          "winning_option": str,
          "total_stake":   int,   # sum of every entry's stake
          "total_payout":  int,   # sum of post-resolution payouts
          "winners":       int,   # number of entries that won
          "losers":        int,   # number of entries that did not
        }
    """
    await init_db()
    engine = get_engine()
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        # Pull all entries for the market in one shot so we can compute
        # the summary without a second roundtrip per entry.
        rows = (
            await conn.execute(
                select(viewer_bet_entries)
                .where(viewer_bet_entries.c.session_id == int(session_id))
                .where(viewer_bet_entries.c.market_id == str(market_id))
            )
        ).all()

        winners = 0
        losers = 0
        total_stake = 0
        total_payout = 0
        for row in rows:
            m = row._mapping
            stake = int(m["stake"])
            total_stake += stake
            if str(m["option"]) == str(winning_option):
                payout = stake * PAYOUT_MULTIPLIER
                winners += 1
            else:
                payout = 0
                losers += 1
            total_payout += payout
            await conn.execute(
                update(viewer_bet_entries)
                .where(viewer_bet_entries.c.id == int(m["id"]))
                .values(payout=payout)
            )

        await conn.execute(
            update(viewer_bets)
            .where(viewer_bets.c.session_id == int(session_id))
            .where(viewer_bets.c.market_id == str(market_id))
            .values(
                status=STATUS_RESOLVED,
                winning_option=str(winning_option),
                resolved_at=now,
            )
        )

    summary: dict[str, Any] = {
        "market_id": str(market_id),
        "winning_option": str(winning_option),
        "total_stake": total_stake,
        "total_payout": total_payout,
        "winners": winners,
        "losers": losers,
    }

    await bus.emit(
        "betting.market_resolved",
        session_id=int(session_id),
        **summary,
    )
    logger.info(
        "[betting] resolved market_id=%s winning_option=%s winners=%d losers=%d",
        market_id, winning_option, winners, losers,
    )
    return summary


async def leaderboard(
    session_id: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Top viewers by *net* winnings in the given session.

    Net is ``sum(payout) - sum(stake)`` across every entry the viewer
    has placed in the session, regardless of which market they came
    from. We keep the latest non-empty ``display_name`` per viewer so a
    rename mid-session shows the freshest label.

    Sorted ``net DESC, bets DESC, viewer_id ASC``; ties between two
    equally-profitable viewers go to the more active bettor first.
    """
    safe_limit = max(1, min(int(limit), 200))
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(
                    viewer_bet_entries.c.viewer_id,
                    func.sum(viewer_bet_entries.c.payout).label("total_payout"),
                    func.sum(viewer_bet_entries.c.stake).label("total_stake"),
                    func.count(viewer_bet_entries.c.id).label("bets"),
                    func.max(viewer_bet_entries.c.display_name).label(
                        "display_name"
                    ),
                )
                .where(viewer_bet_entries.c.session_id == int(session_id))
                .group_by(viewer_bet_entries.c.viewer_id)
            )
        ).all()

    out: list[dict[str, Any]] = []
    for row in rows:
        m = row._mapping
        payout = int(m["total_payout"] or 0)
        stake = int(m["total_stake"] or 0)
        out.append(
            {
                "viewer_id": str(m["viewer_id"]),
                "display_name": str(m["display_name"] or ""),
                "net": payout - stake,
                "bets": int(m["bets"] or 0),
            }
        )

    out.sort(key=lambda r: (-r["net"], -r["bets"], r["viewer_id"]))
    return out[:safe_limit]


async def balance(session_id: int, viewer_id: str) -> int:
    """Compute a viewer's running play-money balance.

    Formula::

        balance = STARTING_BALANCE + sum(payout) - sum(stake)

    floored at 0 so the UI never has to render a negative number. A
    viewer with no entries simply gets :data:`STARTING_BALANCE` back.
    """
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    func.coalesce(
                        func.sum(viewer_bet_entries.c.payout), 0
                    ).label("total_payout"),
                    func.coalesce(
                        func.sum(viewer_bet_entries.c.stake), 0
                    ).label("total_stake"),
                )
                .where(viewer_bet_entries.c.session_id == int(session_id))
                .where(viewer_bet_entries.c.viewer_id == str(viewer_id))
            )
        ).first()

    if row is None:
        return STARTING_BALANCE

    m = row._mapping
    total_payout = int(m["total_payout"] or 0)
    total_stake = int(m["total_stake"] or 0)
    return max(0, STARTING_BALANCE + total_payout - total_stake)


__all__ = [
    "ALLOWED_STAKES",
    "Entry",
    "Market",
    "PAYOUT_MULTIPLIER",
    "STARTING_BALANCE",
    "STATUS_CANCELLED",
    "STATUS_LOCKED",
    "STATUS_OPEN",
    "STATUS_RESOLVED",
    "balance",
    "leaderboard",
    "list_open_markets",
    "lock_market",
    "open_market",
    "place_bet",
    "resolve_market",
]

"""Tests for the live quest designer (feature #14).

We exercise the persistence + state-machine functions in
``autonoma.quests`` directly with the ``fresh_db`` fixture, and the
HTTP-layer dedup rule by calling the router's coroutines with a
hand-crafted ``User`` so we don't need the full FastAPI app to be
mounted with the new router (the integration wiring lives in
``api.py``, which is intentionally outside this feature's edit
surface).

Three behaviors covered:

* ``activate_top_quest`` picks the highest-voted ``proposed`` quest.
* The router's vote dedup rejects a second vote from the same user
  on the same quest with HTTP 409.
* ``complete_quest`` flips status to ``completed`` and stamps
  ``completed_round``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from autonoma.db.users import User
from autonoma.event_bus import bus
from autonoma.quests import (
    STATUS_ACTIVE,
    STATUS_COMPLETED,
    STATUS_PROPOSED,
    QuestTextEmpty,
    QuestTextTooLong,
    activate_top_quest,
    complete_quest,
    list_quests,
    propose_quest,
    vote_quest,
)


def _fake_user(uid: str = "u-test", role: str = "user") -> User:
    """Build a synthetic ``User`` for direct router-coroutine calls.

    Matches the shape ``require_active_user`` / ``require_admin``
    return so the router functions accept it without hitting the DB.
    """
    now = datetime.now(UTC)
    return User(
        id=uid,
        username=f"user-{uid}",
        password_hash="",
        role=role,  # type: ignore[arg-type]
        status="active",
        created_at=now,
        updated_at=now,
    )


# ── DB-layer behavior ─────────────────────────────────────────────────


async def test_activate_top_quest_picks_highest_voted(fresh_db) -> None:
    """Three proposals + asymmetric votes → top-voted card wins."""
    session_id = 1001

    q_low = await propose_quest(session_id, "build a cozy hut")
    q_mid = await propose_quest(session_id, "tame a wandering slime")
    q_high = await propose_quest(session_id, "summon the ancient archer")

    # Vote totals: low=1, mid=2, high=4
    await vote_quest(q_low)
    await vote_quest(q_mid)
    await vote_quest(q_mid)
    for _ in range(4):
        await vote_quest(q_high)

    # Sanity check: every row is still ``proposed`` before activation.
    proposed = await list_quests(session_id, status=STATUS_PROPOSED)
    assert {p.id for p in proposed} == {q_low, q_mid, q_high}
    # Sorted by votes desc.
    assert [p.votes for p in proposed] == [4, 2, 1]

    activated = await activate_top_quest(session_id, round_number=7)
    assert activated is not None
    assert activated.id == q_high
    assert activated.status == STATUS_ACTIVE
    assert activated.activated_round == 7
    assert activated.votes == 4

    # The other two remain proposed.
    still_proposed = await list_quests(session_id, status=STATUS_PROPOSED)
    assert {p.id for p in still_proposed} == {q_low, q_mid}


async def test_activate_top_quest_returns_none_when_no_proposals(fresh_db) -> None:
    """Empty pool ⇒ no winner, no exception."""
    activated = await activate_top_quest(session_id=2002, round_number=1)
    assert activated is None


async def test_complete_quest_marks_status_and_round(fresh_db) -> None:
    session_id = 3003
    qid = await propose_quest(session_id, "befriend the village blacksmith")
    await vote_quest(qid)
    activated = await activate_top_quest(session_id, round_number=2)
    assert activated is not None and activated.id == qid

    ok = await complete_quest(qid, round_number=3)
    assert ok is True

    rows = await list_quests(session_id)
    assert len(rows) == 1
    only = rows[0]
    assert only.status == STATUS_COMPLETED
    assert only.activated_round == 2
    assert only.completed_round == 3


async def test_complete_quest_returns_false_for_unknown_id(fresh_db) -> None:
    assert await complete_quest(quest_id=99_999, round_number=1) is False


async def test_propose_quest_rejects_empty_and_overlong(fresh_db) -> None:
    with pytest.raises(QuestTextEmpty):
        await propose_quest(session_id=4004, text="   ")
    with pytest.raises(QuestTextTooLong):
        await propose_quest(session_id=4004, text="x" * 257)


async def test_vote_quest_returns_zero_for_missing_row(fresh_db) -> None:
    """Voting a non-existent id is benign — returns 0 instead of raising."""
    assert await vote_quest(quest_id=12_345) == 0


async def test_propose_emits_bus_event(fresh_db) -> None:
    """``quest.proposed`` carries session_id + quest_id so subscribers
    can latch onto a single proposal without polling."""
    seen: list[dict] = []

    async def _handler(**data):
        seen.append(data)

    bus.on("quest.proposed", _handler)
    qid = await propose_quest(session_id=5005, text="raise the festival lanterns")
    assert seen and seen[-1]["quest_id"] == qid
    assert seen[-1]["session_id"] == 5005


async def test_activate_emits_bus_event(fresh_db) -> None:
    seen: list[dict] = []

    async def _handler(**data):
        seen.append(data)

    bus.on("quest.activated", _handler)
    qid = await propose_quest(session_id=5006, text="open the eastern gate")
    await vote_quest(qid)
    activated = await activate_top_quest(5006, round_number=11)
    assert activated is not None and activated.id == qid
    assert seen and seen[-1]["quest_id"] == qid
    assert seen[-1]["round_number"] == 11


# ── Router-layer behavior (vote dedup) ────────────────────────────────


async def test_router_vote_dedup_rejects_second_vote(fresh_db) -> None:
    """A second vote from the same user on the same quest is 409.

    We invoke the router coroutines directly (with synthetic ``User``
    objects) so this test is independent of api.py's include_router
    wiring.
    """
    from autonoma.routers import quests as quests_router

    quests_router._voted_pairs.clear()

    user = _fake_user("voter-1")

    propose_resp = await quests_router.propose(
        body=quests_router.ProposeBody(session_id=7007, text="discover the lost recipe"),
        user=user,
    )
    qid = propose_resp["quest_id"]

    first = await quests_router.vote(quest_id=qid, user=user)
    assert first["votes"] == 1

    with pytest.raises(HTTPException) as excinfo:
        await quests_router.vote(quest_id=qid, user=user)
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["code"] == "already_voted"

    # The DB still shows a single recorded vote — the duplicate was
    # rejected before the increment.
    rows = await list_quests(7007)
    assert rows[0].votes == 1


async def test_router_dedup_allows_different_users_to_vote(fresh_db) -> None:
    """Dedup is keyed on (user_id, quest_id), so distinct users each get one
    vote on the same proposal."""
    from autonoma.routers import quests as quests_router

    quests_router._voted_pairs.clear()

    proposer = _fake_user("voter-a")
    other = _fake_user("voter-b")

    propose_resp = await quests_router.propose(
        body=quests_router.ProposeBody(session_id=7008, text="gather the herbalist supplies"),
        user=proposer,
    )
    qid = propose_resp["quest_id"]

    await quests_router.vote(quest_id=qid, user=proposer)
    second = await quests_router.vote(quest_id=qid, user=other)
    assert second["votes"] == 2


async def test_router_activation_clears_dedup_for_winning_quest(
    fresh_db,
) -> None:
    """After activation, voters who supported the winning card can vote
    on a fresh proposal without their old entry blocking them."""
    from autonoma.routers import quests as quests_router

    quests_router._voted_pairs.clear()

    voter = _fake_user("voter-act")
    admin = _fake_user("host-act", role="admin")

    propose_resp = await quests_router.propose(
        body=quests_router.ProposeBody(session_id=8008, text="calm the storm"),
        user=voter,
    )
    qid = propose_resp["quest_id"]

    await quests_router.vote(quest_id=qid, user=voter)
    assert ("voter-act", qid) in quests_router._voted_pairs

    result = await quests_router.activate(
        quest_id=qid,
        body=quests_router.ActivateBody(round_number=5),
        _user=admin,
    )
    assert result["quest"]["status"] == STATUS_ACTIVE
    # Dedup entry tied to the activated quest is cleared.
    assert ("voter-act", qid) not in quests_router._voted_pairs


async def test_router_propose_rejects_empty_text(fresh_db) -> None:
    from autonoma.routers import quests as quests_router

    user = _fake_user("voter-empty")
    with pytest.raises(HTTPException) as excinfo:
        await quests_router.propose(
            body=quests_router.ProposeBody(session_id=9009, text="   "),
            user=user,
        )
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail["code"] == "quest_text_empty"

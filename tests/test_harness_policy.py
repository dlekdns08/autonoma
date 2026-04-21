"""Tests for the HarnessPolicy model + harness_policies DB layer.

Covers:
- Pydantic model defaults and bounds.
- Cross-field validation (positive > negative thresholds).
- DB CRUD round-trip.
- Default preset seeding (``ensure_default_policy``).
- Default preset is read-only: update/delete refuse.
- Per-user listing returns user's own presets plus the default.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Scratch SQLite for each test; identical pattern to test_auth.py."""
    from autonoma import config as config_module
    from autonoma.db import engine as engine_module

    monkeypatch.setattr(config_module.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config_module.settings, "db_filename", "harness_test.db")
    engine_module._engine = None
    engine_module._initialized = False
    yield tmp_path


async def _make_user(username: str) -> str:
    """Insert a stub user row (so FK-backed preset inserts succeed) and
    return its id. Password hash is a placeholder — auth isn't under
    test here."""
    from autonoma.db.users import create_user

    user = await create_user(
        username=username,
        password_hash="not-a-real-hash",
        role="user",
        status="active",
    )
    return user.id


# ── Model-layer tests ─────────────────────────────────────────────────


def test_default_policy_content_builds_without_errors() -> None:
    from autonoma.harness.policy import HarnessPolicyContent, default_policy_content

    content = default_policy_content()
    assert isinstance(content, HarnessPolicyContent)
    # Sanity-check a handful of expected defaults mirroring current code.
    assert content.loop.max_rounds == 40
    assert content.action.sandbox_wall_time_s == 8
    assert content.spawn.max_agents == 8
    assert content.routing.strategy == "priority"
    assert content.safety.code_execution == "sandbox"


def test_numeric_bounds_reject_out_of_range() -> None:
    from autonoma.harness.policy import LoopPolicy

    with pytest.raises(ValidationError):
        LoopPolicy(max_rounds=1)  # below ge=10
    with pytest.raises(ValidationError):
        LoopPolicy(max_rounds=10_000)  # above le=500


def test_enum_fields_reject_unknown_value() -> None:
    from autonoma.harness.policy import RoutingPolicy

    with pytest.raises(ValidationError):
        RoutingPolicy(strategy="lottery")  # type: ignore[arg-type]


def test_mood_positive_must_exceed_negative() -> None:
    from autonoma.harness.policy import MoodPolicy

    with pytest.raises(ValidationError):
        MoodPolicy(
            sentiment_positive_threshold=0.3,
            sentiment_negative_threshold=0.5,
        )


def test_social_friend_must_exceed_rival() -> None:
    from autonoma.harness.policy import SocialPolicy

    with pytest.raises(ValidationError):
        SocialPolicy(friend_trust_threshold=0.2, rival_trust_threshold=0.4)


def test_extra_fields_rejected() -> None:
    from autonoma.harness.policy import LoopPolicy

    with pytest.raises(ValidationError):
        LoopPolicy(max_rounds=40, mystery_knob=123)  # type: ignore[call-arg]


# ── DB-layer tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_round_trip_preset(fresh_db) -> None:
    from autonoma.db.harness_policies import create_policy, get_policy_by_id
    from autonoma.harness.policy import default_policy_content

    uid = await _make_user("alice")
    content = default_policy_content()
    content.loop.max_rounds = 75
    created = await create_policy(
        owner_user_id=uid,
        name="faster",
        content=content,
    )
    assert created.name == "faster"
    fetched = await get_policy_by_id(created.id)
    assert fetched is not None
    assert fetched.content.loop.max_rounds == 75
    assert fetched.is_default is False


@pytest.mark.asyncio
async def test_ensure_default_policy_is_idempotent(fresh_db) -> None:
    from autonoma.db.harness_policies import (
        ensure_default_policy,
        get_default_policy,
    )

    first = await ensure_default_policy()
    again = await ensure_default_policy()
    assert first.id == again.id
    assert first.is_default is True
    got = await get_default_policy()
    assert got is not None and got.id == first.id


@pytest.mark.asyncio
async def test_default_policy_refuses_update_and_delete(fresh_db) -> None:
    from autonoma.db.harness_policies import (
        delete_policy,
        ensure_default_policy,
        update_policy,
    )
    from autonoma.harness.policy import default_policy_content

    default = await ensure_default_policy()
    with pytest.raises(ValueError):
        await update_policy(default.id, content=default_policy_content())
    with pytest.raises(ValueError):
        await delete_policy(default.id)


@pytest.mark.asyncio
async def test_update_policy_bumps_updated_at(fresh_db) -> None:
    import asyncio

    from autonoma.db.harness_policies import create_policy, update_policy

    uid = await _make_user("bob")
    created = await create_policy(
        owner_user_id=uid,
        name="orig",
    )
    # Sleep a hair so the timestamps differ even on fast machines.
    await asyncio.sleep(0.01)
    updated = await update_policy(created.id, name="renamed")
    assert updated is not None
    assert updated.name == "renamed"
    assert updated.updated_at >= created.updated_at


@pytest.mark.asyncio
async def test_delete_policy_removes_row(fresh_db) -> None:
    from autonoma.db.harness_policies import (
        create_policy,
        delete_policy,
        get_policy_by_id,
    )

    uid = await _make_user("carol")
    created = await create_policy(owner_user_id=uid, name="to_delete")
    assert await delete_policy(created.id) is True
    assert await get_policy_by_id(created.id) is None


@pytest.mark.asyncio
async def test_list_for_user_returns_own_plus_default(fresh_db) -> None:
    from autonoma.db.harness_policies import (
        create_policy,
        ensure_default_policy,
        list_policies_for_user,
    )

    await ensure_default_policy()
    await create_policy(owner_user_id="alice", name="alice-one")
    await create_policy(owner_user_id="alice", name="alice-two")
    await create_policy(owner_user_id="bob", name="bob-private")

    alice_list = await list_policies_for_user("alice")
    names = [p.name for p in alice_list]
    # default is sorted first (is_default DESC), then by created_at.
    assert names[0] == "default"
    assert set(names[1:]) == {"alice-one", "alice-two"}
    assert "bob-private" not in names


@pytest.mark.asyncio
async def test_content_survives_json_round_trip(fresh_db) -> None:
    from autonoma.db.harness_policies import create_policy, get_policy_by_id
    from autonoma.harness.policy import default_policy_content

    content = default_policy_content()
    content.routing.strategy = "round_robin"
    content.mood.weather_affect_probability = 0.75
    content.social.trading_post_interval = 9

    created = await create_policy(owner_user_id="u", name="tweaked", content=content)
    fetched = await get_policy_by_id(created.id)
    assert fetched is not None
    assert fetched.content.routing.strategy == "round_robin"
    assert fetched.content.mood.weather_affect_probability == 0.75
    assert fetched.content.social.trading_post_interval == 9

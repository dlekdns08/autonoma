"""CRUD tests for ``autonoma.mocap.store``.

Uses the shared ``fresh_db`` fixture (see tests/conftest.py) so each
test gets a scratch SQLite file with the full migration stack applied.
User rows are created with ``create_user`` from ``autonoma.db.users``
to satisfy the ``owner_user_id`` FK.
"""

from __future__ import annotations

import base64
import gzip
import json

import pytest

from autonoma.mocap.validator import ValidatedClip


# ── Helpers ──────────────────────────────────────────────────────────


def _make_validated(name: str = "clip", source_vrm: str = "midori.vrm") -> ValidatedClip:
    """Build a ValidatedClip with a real gzipped JSON payload.

    We sidestep ``validate_payload`` here — the store tests don't care
    about validator semantics, only about byte round-tripping through
    the DB.
    """
    clip = {
        "version": 1,
        "fps": 30,
        "durationS": 1.0,
        "frameCount": 31,
        "bones": {"hips": {"data": [0.0, 0.0, 0.0, 1.0] * 31}},
        "expressions": {"happy": {"data": [0.0] * 31}},
    }
    raw = json.dumps(clip).encode("utf-8")
    payload_gz = gzip.compress(raw)
    return ValidatedClip(
        payload_gz=payload_gz,
        decoded=clip,
        size_bytes=len(raw),
        duration_s=1.0,
        fps=30,
        frame_count=31,
        name=name,
        source_vrm=source_vrm,
    )


async def _make_user(username: str = "alice") -> str:
    """Create a user row via the real CRUD helper; return id."""
    from autonoma.db.users import create_user

    user = await create_user(
        username=username,
        password_hash="x" * 60,  # hash shape doesn't matter for FK
        status="active",
    )
    return user.id


# ── create / list / get ──────────────────────────────────────────────


async def test_create_clip_persists(fresh_db) -> None:
    from autonoma.mocap.store import create_clip, get_clip_summary

    owner = await _make_user()
    vc = _make_validated(name="dance")
    summary = await create_clip(owner_user_id=owner, validated=vc)

    assert summary.owner_user_id == owner
    assert summary.name == "dance"
    assert summary.source_vrm == "midori.vrm"
    assert summary.duration_s == 1.0
    assert summary.fps == 30
    assert summary.frame_count == 31
    assert summary.size_bytes == vc.size_bytes

    # Round-trips via get_clip_summary.
    again = await get_clip_summary(summary.id)
    assert again is not None
    assert again.to_dict() == summary.to_dict()


async def test_get_clip_summary_returns_none_for_missing(fresh_db) -> None:
    from autonoma.db.engine import init_db
    from autonoma.mocap.store import get_clip_summary

    await init_db()
    assert await get_clip_summary("00000000-0000-0000-0000-000000000000") is None


async def test_list_clips_for_user_returns_owners_clips(fresh_db) -> None:
    from autonoma.mocap.store import create_clip, list_clips_for_user

    alice = await _make_user("alice")
    bob = await _make_user("bob")

    c1 = await create_clip(owner_user_id=alice, validated=_make_validated("a1"))
    await create_clip(owner_user_id=bob, validated=_make_validated("b1"))

    alice_clips = await list_clips_for_user(alice)
    ids = {c.id for c in alice_clips}
    assert c1.id in ids
    assert all(c.owner_user_id == alice for c in alice_clips)


async def test_list_clips_for_user_surfaces_bound_clips_owned_by_others(
    fresh_db,
) -> None:
    """A binding pointing at another user's clip should surface it
    in the requester's library even though they don't own it."""
    from autonoma.mocap.store import (
        create_clip,
        list_clips_for_user,
        upsert_binding,
    )

    alice = await _make_user("alice")
    bob = await _make_user("bob")

    bob_clip = await create_clip(
        owner_user_id=bob, validated=_make_validated("bob-clip")
    )
    # Alice has no clips of her own yet.
    assert await list_clips_for_user(alice) == []

    # Bind bob's clip to a VRM/trigger.
    await upsert_binding(
        vrm_file="midori.vrm",
        trigger_kind="mood",
        trigger_value="happy",
        clip_id=bob_clip.id,
        updated_by=alice,
    )

    alice_view = await list_clips_for_user(alice)
    assert any(c.id == bob_clip.id for c in alice_view)


# ── get_clip_payload ─────────────────────────────────────────────────


async def test_get_clip_payload_returns_base64_gzip_round_trip(fresh_db) -> None:
    from autonoma.mocap.store import create_clip, get_clip_payload

    owner = await _make_user()
    vc = _make_validated()
    summary = await create_clip(owner_user_id=owner, validated=vc)

    fetched = await get_clip_payload(summary.id)
    assert fetched is not None
    fetched_summary, b64 = fetched
    assert fetched_summary.id == summary.id
    # Decoded base64 → gzip → json equals what we started with.
    raw = gzip.decompress(base64.b64decode(b64))
    assert json.loads(raw) == vc.decoded


async def test_get_clip_payload_missing_returns_none(fresh_db) -> None:
    from autonoma.db.engine import init_db
    from autonoma.mocap.store import get_clip_payload

    await init_db()
    assert await get_clip_payload("deadbeef-0000-0000-0000-000000000000") is None


# ── rename / delete ──────────────────────────────────────────────────


async def test_rename_clip(fresh_db) -> None:
    from autonoma.mocap.store import create_clip, get_clip_payload, rename_clip

    owner = await _make_user()
    summary = await create_clip(
        owner_user_id=owner, validated=_make_validated("old-name")
    )
    renamed = await rename_clip(summary.id, "new-name")
    assert renamed is not None
    assert renamed.name == "new-name"

    # The embedded ``name`` field in the payload must also be rewritten
    # so clients that decode the payload (rather than reading the summary)
    # see the new name. Regression test for a bug where only the
    # ``mocap_clips.name`` column was updated.
    fetched = await get_clip_payload(summary.id)
    assert fetched is not None
    fetched_summary, b64 = fetched
    assert fetched_summary.name == "new-name"
    decoded = json.loads(gzip.decompress(base64.b64decode(b64)))
    assert decoded["name"] == "new-name"
    # size_bytes should match the new uncompressed payload size.
    assert fetched_summary.size_bytes == len(
        json.dumps(decoded).encode("utf-8")
    )


async def test_rename_clip_missing_returns_none(fresh_db) -> None:
    from autonoma.db.engine import init_db
    from autonoma.mocap.store import rename_clip

    await init_db()
    assert await rename_clip("missing-id", "nope") is None


async def test_delete_clip_success(fresh_db) -> None:
    from autonoma.mocap.store import create_clip, delete_clip, get_clip_summary

    owner = await _make_user()
    summary = await create_clip(owner_user_id=owner, validated=_make_validated())

    assert await delete_clip(summary.id) is True
    assert await get_clip_summary(summary.id) is None


async def test_delete_clip_raises_integrity_error_when_bound(fresh_db) -> None:
    from sqlalchemy.exc import IntegrityError

    from autonoma.mocap.store import create_clip, delete_clip, upsert_binding

    owner = await _make_user()
    summary = await create_clip(owner_user_id=owner, validated=_make_validated())
    await upsert_binding(
        vrm_file="midori.vrm",
        trigger_kind="mood",
        trigger_value="happy",
        clip_id=summary.id,
        updated_by=owner,
    )
    with pytest.raises(IntegrityError):
        await delete_clip(summary.id)


# ── clip_is_bound ────────────────────────────────────────────────────


async def test_clip_is_bound_false_by_default(fresh_db) -> None:
    from autonoma.mocap.store import clip_is_bound, create_clip

    owner = await _make_user()
    summary = await create_clip(owner_user_id=owner, validated=_make_validated())
    assert await clip_is_bound(summary.id) is False


async def test_clip_is_bound_true_after_bind(fresh_db) -> None:
    from autonoma.mocap.store import clip_is_bound, create_clip, upsert_binding

    owner = await _make_user()
    summary = await create_clip(owner_user_id=owner, validated=_make_validated())
    await upsert_binding(
        vrm_file="midori.vrm",
        trigger_kind="state",
        trigger_value="working",
        clip_id=summary.id,
        updated_by=owner,
    )
    assert await clip_is_bound(summary.id) is True


# ── bindings: upsert / delete / list ─────────────────────────────────


async def test_upsert_binding_inserts_then_updates_same_key(fresh_db) -> None:
    from autonoma.mocap.store import create_clip, list_bindings, upsert_binding

    owner = await _make_user()
    c1 = await create_clip(owner_user_id=owner, validated=_make_validated("c1"))
    c2 = await create_clip(owner_user_id=owner, validated=_make_validated("c2"))

    b1 = await upsert_binding(
        vrm_file="midori.vrm",
        trigger_kind="mood",
        trigger_value="happy",
        clip_id=c1.id,
        updated_by=owner,
    )
    assert b1.clip_id == c1.id

    # Same key, different clip — should UPDATE not INSERT.
    b2 = await upsert_binding(
        vrm_file="midori.vrm",
        trigger_kind="mood",
        trigger_value="happy",
        clip_id=c2.id,
        updated_by=owner,
    )
    assert b2.clip_id == c2.id

    rows = await list_bindings()
    matching = [
        r
        for r in rows
        if r.vrm_file == "midori.vrm"
        and r.trigger_kind == "mood"
        and r.trigger_value == "happy"
    ]
    assert len(matching) == 1
    assert matching[0].clip_id == c2.id


async def test_delete_binding_returns_true_when_present(fresh_db) -> None:
    from autonoma.mocap.store import (
        create_clip,
        delete_binding,
        upsert_binding,
    )

    owner = await _make_user()
    clip = await create_clip(owner_user_id=owner, validated=_make_validated())
    await upsert_binding(
        vrm_file="midori.vrm",
        trigger_kind="emote",
        trigger_value="✦",
        clip_id=clip.id,
        updated_by=owner,
    )
    assert (
        await delete_binding(
            vrm_file="midori.vrm", trigger_kind="emote", trigger_value="✦"
        )
        is True
    )


async def test_delete_binding_returns_false_when_missing(fresh_db) -> None:
    from autonoma.db.engine import init_db
    from autonoma.mocap.store import delete_binding

    await init_db()
    assert (
        await delete_binding(
            vrm_file="midori.vrm",
            trigger_kind="mood",
            trigger_value="happy",
        )
        is False
    )


async def test_list_bindings_returns_all(fresh_db) -> None:
    from autonoma.mocap.store import create_clip, list_bindings, upsert_binding

    owner = await _make_user()
    c1 = await create_clip(owner_user_id=owner, validated=_make_validated("c1"))
    c2 = await create_clip(owner_user_id=owner, validated=_make_validated("c2"))

    await upsert_binding(
        vrm_file="midori.vrm",
        trigger_kind="mood",
        trigger_value="happy",
        clip_id=c1.id,
        updated_by=owner,
    )
    await upsert_binding(
        vrm_file="midori.vrm",
        trigger_kind="state",
        trigger_value="working",
        clip_id=c2.id,
        updated_by=owner,
    )

    rows = await list_bindings()
    keys = {(r.trigger_kind, r.trigger_value) for r in rows}
    assert {("mood", "happy"), ("state", "working")}.issubset(keys)

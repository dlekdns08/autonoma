"""Tests for persona breeding (feature #13).

We exercise ``autonoma.personas_breed`` directly rather than going
through the router — that keeps the unit pure (no auth, no app
lifecycle) and the assertions sharper.

Two parent rows are inserted directly via the ``personas`` table.
The codebase doesn't expose a ``save_persona`` helper in
``autonoma.db.registry`` (registry.py is character-focused; persona
inserts live in ``routers/personas.py`` and ``routers/persona_breed.py``
inline), so we mirror that pattern here.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import insert, select

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import personas
from autonoma.personas_breed import (
    breed_personas,
    breed_seed_strings,
    merge_tags,
)


OWNER_ID = "owner-test-uid"


async def _insert_persona(
    *,
    name: str,
    seed_string: str,
    tags: list[str],
    prompt_style: str = "",
    owner_id: str | None = OWNER_ID,
    is_public: bool = False,
) -> str:
    pid = str(uuid.uuid4())
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            insert(personas).values(
                id=pid,
                owner_user_id=owner_id,
                name=name,
                role="coder",
                seed_string=seed_string,
                voice_profile_id=None,
                vrm_file="",
                prompt_style=prompt_style,
                tags_json=json.dumps(tags),
                is_public=1 if is_public else 0,
                parent_persona_ids="[]",
            )
        )
    return pid


# ── Pure helpers ──────────────────────────────────────────────────────


def test_breed_seed_strings_is_deterministic():
    a = "coder:midori-bear:v1"
    b = "coder:azure-fox:v1"
    out_1 = breed_seed_strings(a, b, salt="x")
    out_2 = breed_seed_strings(a, b, salt="x")
    assert out_1 == out_2
    # Different salt → different child.
    assert breed_seed_strings(a, b, salt="y") != out_1


def test_breed_seed_strings_drops_duplicate_segments():
    out = breed_seed_strings("coder:fox", "coder:bear")
    parts = out.split(":")
    assert parts.count("coder") == 1
    assert "fox" in parts
    assert "bear" in parts


def test_merge_tags_prefers_common_then_unique():
    a = ["fox", "calm", "lyrical"]
    b = ["bear", "calm", "stoic"]
    merged = merge_tags(a, b)
    assert merged[0] == "calm"
    assert set(merged) == {"calm", "fox", "lyrical", "bear", "stoic"}


def test_merge_tags_caps_length():
    merged = merge_tags(
        ["a", "b", "c", "d"], ["e", "f", "g", "h"], max_tags=3
    )
    assert len(merged) == 3


# ── Async breeding integration ────────────────────────────────────────


async def test_breed_creates_child_with_lineage(fresh_db):
    await init_db()
    parent_a = await _insert_persona(
        name="Midori",
        seed_string="coder:midori:v1",
        tags=["fox", "calm"],
        prompt_style="Speaks softly, with care.",
    )
    parent_b = await _insert_persona(
        name="Azure",
        seed_string="coder:azure:v1",
        tags=["bear", "calm"],
        prompt_style="Bold and direct.",
    )

    child = await breed_personas(
        parent_a_id=parent_a,
        parent_b_id=parent_b,
        child_name="MidoriAzure",
        owner_user_id=OWNER_ID,
        llm_client=None,
    )

    # Lineage carries both parents.
    assert child["parent_persona_ids"] == [parent_a, parent_b]
    # Distinct seed string.
    assert child["seed_string"] not in {"coder:midori:v1", "coder:azure:v1"}
    # Merged tags include the shared "calm" first.
    assert "calm" in child["tags"]
    assert "fox" in child["tags"]
    assert "bear" in child["tags"]
    # Owner is the caller, child starts private.
    assert child["is_public"] is False

    # Confirm the row really hit the DB with parent_persona_ids set.
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(select(personas).where(personas.c.id == child["id"]))
        ).first()
    assert row is not None
    assert json.loads(row._mapping["parent_persona_ids"]) == [parent_a, parent_b]


async def test_breed_offline_blends_prompt_style(fresh_db):
    await init_db()
    parent_a = await _insert_persona(
        name="A", seed_string="coder:a", tags=[], prompt_style="Voice A."
    )
    parent_b = await _insert_persona(
        name="B", seed_string="coder:b", tags=[], prompt_style="Voice B."
    )
    child = await breed_personas(
        parent_a_id=parent_a,
        parent_b_id=parent_b,
        child_name="Child",
        owner_user_id=OWNER_ID,
        llm_client=None,
    )
    assert "Voice A." in child["prompt_style"]
    assert "Voice B." in child["prompt_style"]
    assert "---" in child["prompt_style"]


async def test_breed_same_parent_rejected(fresh_db):
    await init_db()
    parent = await _insert_persona(
        name="Solo", seed_string="coder:solo", tags=["alone"]
    )
    with pytest.raises(ValueError):
        await breed_personas(
            parent_a_id=parent,
            parent_b_id=parent,
            child_name="Clone",
            owner_user_id=OWNER_ID,
            llm_client=None,
        )


async def test_breed_router_same_id_returns_400(fresh_db):
    """Round-trip the same-parent case through the HTTP router so we
    confirm 400 (not 500) is what callers see."""
    from collections.abc import AsyncIterator

    from httpx import ASGITransport, AsyncClient

    from autonoma.api import app
    from autonoma.routers import persona_breed as _breed_router

    # Mount the new router on the existing app for the duration of the
    # test. ``app.include_router`` is idempotent w.r.t. duplicate
    # routes — FastAPI will pick the first match, which is fine here
    # because no other route owns ``/api/personas/breed``.
    if not any(
        getattr(r, "path", None) == "/api/personas/breed" for r in app.routes
    ):
        app.include_router(_breed_router.router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            # Sign up + activate + login.
            r = await client.post(
                "/api/auth/signup",
                json={"username": "breeder", "password": "password123"},
            )
            assert r.status_code == 201, r.text
            from autonoma.db.users import (
                get_user_by_username,
                update_user_status,
            )
            u = await get_user_by_username("breeder")
            assert u is not None
            await update_user_status(u.id, "active")
            r = await client.post(
                "/api/auth/login",
                json={"username": "breeder", "password": "password123"},
            )
            assert r.status_code == 200

            # Seed one persona owned by the user.
            parent = await _insert_persona(
                name="Solo",
                seed_string="coder:solo",
                tags=[],
                owner_id=u.id,
            )

            r = await client.post(
                "/api/personas/breed",
                json={
                    "parent_a_id": parent,
                    "parent_b_id": parent,
                    "name": "Clone",
                },
            )
            assert r.status_code == 400, r.text
            assert r.json()["detail"]["code"] == "same_parent"

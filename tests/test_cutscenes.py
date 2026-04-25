"""Tests for the cutscene composer (Phase 3-#3) — model + FS store."""

from __future__ import annotations

import pytest

from autonoma.cutscenes import (
    Cutscene,
    CutsceneNotFound,
    CutsceneStep,
    CutsceneStepKind,
    CutsceneStore,
)
from autonoma.cutscenes.model import CutsceneTrigger


@pytest.fixture
def store(tmp_path):
    return CutsceneStore(root=tmp_path / "cutscenes")


def _basic_cutscene(owner: str = "u1") -> Cutscene:
    return Cutscene(
        owner_user_id=owner,
        name="Victory Lap",
        steps=[
            CutsceneStep(
                at_ms=0,
                kind=CutsceneStepKind.SFX,
                label="fanfare",
                payload={"sfx_name": "complete"},
            ),
            CutsceneStep(
                at_ms=500,
                kind=CutsceneStepKind.SPEECH,
                label="director cheers",
                payload={"agent": "Director", "text": "We did it!"},
            ),
        ],
        trigger=CutsceneTrigger(kind="project_complete"),
    )


def test_step_sorting_normalises_order():
    cs = Cutscene(
        owner_user_id="u1",
        steps=[
            CutsceneStep(at_ms=2000, kind=CutsceneStepKind.SFX, payload={}),
            CutsceneStep(at_ms=0, kind=CutsceneStepKind.SFX, payload={}),
        ],
    )
    assert [s.at_ms for s in cs.steps] == [0, 2000]


def test_total_duration_uses_last_step():
    cs = _basic_cutscene()
    # Last step at 500ms + 2s editor budget for non-delay steps.
    assert cs.total_duration_ms() == 2_500


def test_save_then_get_round_trip(store):
    cs = _basic_cutscene()
    saved = store.save(cs)
    loaded = store.get("u1", saved.id)
    assert loaded.id == saved.id
    assert loaded.name == "Victory Lap"
    assert len(loaded.steps) == 2
    assert loaded.steps[1].payload["text"] == "We did it!"


def test_get_missing_raises(store):
    with pytest.raises(CutsceneNotFound):
        store.get("u1", "nonexistent")


def test_list_for_owner_orders_by_updated_desc(store):
    a = store.save(Cutscene(owner_user_id="u1", name="first"))
    b = store.save(Cutscene(owner_user_id="u1", name="second"))
    items = store.list_for_owner("u1")
    assert [c.name for c in items[:2]] == ["second", "first"]
    # Other users don't leak in.
    assert store.list_for_owner("u2") == []
    assert {a.id, b.id} == {c.id for c in items}


def test_delete_removes_file(store):
    cs = store.save(_basic_cutscene())
    assert store.delete("u1", cs.id)
    assert not store.delete("u1", cs.id)  # idempotent
    with pytest.raises(CutsceneNotFound):
        store.get("u1", cs.id)


def test_iter_all_walks_every_owner(store):
    store.save(Cutscene(owner_user_id="u1", name="a"))
    store.save(Cutscene(owner_user_id="u2", name="b"))
    names = sorted(c.name for c in store.iter_all())
    assert names == ["a", "b"]


def test_unsafe_id_rejected(store):
    cs = Cutscene(owner_user_id="u1", name="x")
    cs.id = "../../etc/passwd"
    with pytest.raises(ValueError):
        store.save(cs)

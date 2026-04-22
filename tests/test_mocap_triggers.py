"""Unit tests for ``autonoma.mocap.triggers``.

Covers the trigger whitelist validator, the manual-slug regex, the
``trigger_catalog`` serializer, and the ``is_known_vrm`` fall-open
behavior when ``_VRM_FILES`` is ``None``.
"""

from __future__ import annotations

import pytest

from autonoma.mocap import triggers as triggers_module
from autonoma.mocap.triggers import (
    MANUAL_SLUG_RE,
    is_known_vrm,
    trigger_catalog,
    validate_trigger,
)


# ── validate_trigger: happy paths ────────────────────────────────────


def test_validate_trigger_mood_valid() -> None:
    assert validate_trigger("mood", "happy") is None


def test_validate_trigger_emote_valid() -> None:
    assert validate_trigger("emote", "✦") is None


def test_validate_trigger_state_valid() -> None:
    assert validate_trigger("state", "working") is None


def test_validate_trigger_manual_valid() -> None:
    assert validate_trigger("manual", "my-custom-slug") is None


# ── validate_trigger: rejection paths ────────────────────────────────


def test_validate_trigger_unknown_kind() -> None:
    assert validate_trigger("gesture", "wave") == "invalid_kind"


def test_validate_trigger_non_string_value() -> None:
    # Non-string value — validate_trigger's type check fires.
    assert validate_trigger("mood", 42) == "invalid_value"  # type: ignore[arg-type]


def test_validate_trigger_empty_value() -> None:
    assert validate_trigger("mood", "") == "invalid_value"


def test_validate_trigger_unknown_mood() -> None:
    assert validate_trigger("mood", "euphoric") == "unknown_mood"


def test_validate_trigger_unknown_emote() -> None:
    assert validate_trigger("emote", "🚀") == "unknown_emote"


def test_validate_trigger_unknown_state() -> None:
    assert validate_trigger("state", "dancing") == "unknown_state"


def test_validate_trigger_invalid_manual_slug() -> None:
    assert validate_trigger("manual", "Has Spaces!") == "invalid_manual_slug"


# ── MANUAL_SLUG_RE ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "slug",
    [
        "my-slug",
        "ab-cd-ef",
        "123",
        "a" * 32,  # upper length bound
        "snake_case",
        "a",  # lower length bound
    ],
)
def test_manual_slug_re_accepts(slug: str) -> None:
    assert MANUAL_SLUG_RE.match(slug) is not None


@pytest.mark.parametrize(
    "slug",
    [
        "Caps",
        "has space",
        "",
        "a" * 33,  # above length cap
        "bang!",
        "dot.separated",
    ],
)
def test_manual_slug_re_rejects(slug: str) -> None:
    assert MANUAL_SLUG_RE.match(slug) is None


# ── trigger_catalog ──────────────────────────────────────────────────


def test_trigger_catalog_shape_and_duration() -> None:
    cat = trigger_catalog()
    assert set(cat.keys()) == {"mood", "emote", "state", "max_clip_duration_s"}
    assert isinstance(cat["mood"], list) and cat["mood"]
    assert isinstance(cat["emote"], list) and cat["emote"]
    assert isinstance(cat["state"], list) and cat["state"]
    max_dur = cat["max_clip_duration_s"]
    assert isinstance(max_dur, (int, float))
    assert max_dur > 0


# ── is_known_vrm ─────────────────────────────────────────────────────


def test_is_known_vrm_accepts_catalog_entry() -> None:
    assert is_known_vrm("midori.vrm") is True


def test_is_known_vrm_rejects_unknown() -> None:
    # Only meaningful when a catalog actually loaded; otherwise the
    # fall-open branch would (correctly) accept anything ending in
    # ``.vrm``. In this repo the catalog is present.
    if triggers_module._VRM_FILES is not None:
        assert is_known_vrm("notreal.vrm") is False


def test_is_known_vrm_fall_open_when_catalog_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(triggers_module, "_VRM_FILES", None)
    # Anything ending in .vrm is accepted.
    assert is_known_vrm("whatever.vrm") is True
    # Non-.vrm still rejected.
    assert is_known_vrm("whatever.txt") is False
    # Non-string rejected.
    assert is_known_vrm(None) is False  # type: ignore[arg-type]

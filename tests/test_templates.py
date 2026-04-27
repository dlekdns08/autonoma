"""Schema sanity checks for ``SCAFFOLD_TEMPLATES``.

The module is a static dict so there is no logic to test, but we still
guard the shape so a future edit can't silently break the consumers
(``api`` exposes templates over the wire).
"""

from __future__ import annotations

import pytest

from autonoma.templates import SCAFFOLD_TEMPLATES


def test_known_template_keys_present() -> None:
    expected = {"python_cli", "fastapi_service", "next_app", "data_pipeline"}
    assert expected <= set(SCAFFOLD_TEMPLATES.keys())


@pytest.mark.parametrize("key", list(SCAFFOLD_TEMPLATES.keys()))
def test_each_template_has_required_fields(key: str) -> None:
    tmpl = SCAFFOLD_TEMPLATES[key]
    assert isinstance(tmpl["name"], str) and tmpl["name"]
    assert isinstance(tmpl["description"], str) and tmpl["description"]
    files = tmpl["files"]
    assert isinstance(files, list) and files
    for f in files:
        assert isinstance(f["path"], str) and f["path"]
        assert isinstance(f["description"], str) and f["description"]


def test_paths_within_each_template_are_unique() -> None:
    for key, tmpl in SCAFFOLD_TEMPLATES.items():
        paths = [f["path"] for f in tmpl["files"]]
        assert len(paths) == len(set(paths)), f"duplicate path in {key}"

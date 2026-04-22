from __future__ import annotations

from pathlib import Path

import pytest

from autonoma.event_bus import bus


@pytest.fixture(autouse=True)
def _reset():
    bus._handlers.clear()
    yield
    bus._handlers.clear()


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Scratch SQLite per test; shared by every test that touches the DB.

    The engine is a module-level singleton — without clearing
    ``_engine`` / ``_initialized`` the second test inherits the first
    test's file handle and starts asserting against stale data. We
    point ``settings.data_dir`` at ``tmp_path`` so each test gets a
    fresh DB on disk, and reset the singleton both pre- and
    post-yield so:

      pre-yield reset: migrations re-run against the new file
      post-yield reset: the next test's fixture can't see our engine

    The DB filename is a constant because isolation is handled by the
    per-test ``tmp_path``, not by the filename. Tests that used to name
    their own file (``auth_test.db``, ``harness_test.db`` etc.) worked
    because of the ``tmp_path`` — the names were cosmetic.
    """
    from autonoma import config as config_module
    from autonoma.db import engine as engine_module

    monkeypatch.setattr(config_module.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config_module.settings, "db_filename", "test.db")
    engine_module._engine = None
    engine_module._initialized = False

    yield tmp_path

    engine_module._engine = None
    engine_module._initialized = False

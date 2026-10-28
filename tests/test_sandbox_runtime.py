"""srt auto-provisioning: lookup order, install path, failure handling."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from autonoma import sandbox_runtime as rt


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rt.settings, "data_dir", tmp_path)
    monkeypatch.setattr(rt.settings, "srt_binary", "")
    monkeypatch.setattr(rt.settings, "sandbox_backend", "auto")
    monkeypatch.setattr(rt.settings, "sandbox_auto_install", True)
    monkeypatch.setattr(rt.settings, "srt_version", "0.0.81")
    monkeypatch.setattr(rt, "_lock", None)


def _make_local(tmp_path: Path) -> Path:
    b = rt.local_binary()
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text("#!/bin/sh\nexit 0\n")
    b.chmod(0o755)
    return b


def test_lookup_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)
    assert rt.find_srt() is None
    local = _make_local(tmp_path)
    assert rt.find_srt() == str(local)
    monkeypatch.setattr(
        rt.shutil, "which", lambda name: "/usr/local/bin/srt" if name == "srt" else None
    )
    assert rt.find_srt() == "/usr/local/bin/srt"  # PATH beats the local copy
    explicit = tmp_path / "custom-srt"
    explicit.write_text("")
    monkeypatch.setattr(rt.settings, "srt_binary", str(explicit))
    assert rt.find_srt() == str(explicit)  # explicit beats everything


@pytest.mark.asyncio
async def test_ensure_installs_with_npm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(rt.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    class Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            _make_local(tmp_path)  # "npm" produced the binary
            return b"", b""

        def kill(self) -> None:
            pass

    async def fake_exec(*argv: str, **kw: Any) -> Proc:
        calls.append(list(argv))
        return Proc()

    monkeypatch.setattr(rt.asyncio, "create_subprocess_exec", fake_exec)
    path = await rt.ensure_srt()
    assert path == str(rt.local_binary())
    assert calls and calls[0][:4] == [
        "/usr/bin/npm",
        "install",
        "--prefix",
        str(rt.local_install_dir()),
    ]
    assert calls[0][-1] == "@anthropic-ai/sandbox-runtime@0.0.81"
    # Second call is a no-op (found).
    assert await rt.ensure_srt() == path
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_ensure_without_npm_or_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)
    assert await rt.ensure_srt() is None
    monkeypatch.setattr(rt.settings, "sandbox_auto_install", False)
    monkeypatch.setattr(rt.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    assert await rt.ensure_srt() is None
    monkeypatch.setattr(rt.settings, "sandbox_auto_install", True)
    monkeypatch.setattr(rt.settings, "sandbox_backend", "bwrap")
    assert await rt.ensure_srt() is None  # not wanted for a forced native backend


@pytest.mark.asyncio
async def test_ensure_reports_failed_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    class Proc:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"npm ERR! network", b""

        def kill(self) -> None:
            pass

    async def fake_exec(*argv: str, **kw: Any) -> Proc:
        return Proc()

    monkeypatch.setattr(rt.asyncio, "create_subprocess_exec", fake_exec)
    assert await rt.ensure_srt() is None


def test_schedule_outside_loop_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)
    assert rt.schedule_ensure_srt() is None


@pytest.mark.asyncio
async def test_schedule_returns_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt.shutil, "which", lambda name: None)
    calls = []

    async def fake_ensure(**kw: Any) -> None:
        calls.append(1)

    monkeypatch.setattr(rt, "ensure_srt", fake_ensure)
    task = rt.schedule_ensure_srt()
    assert task is not None
    await task
    assert calls == [1]
    assert os.name == "posix"

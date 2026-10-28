"""Anthropic sandbox runtime (srt) backend + deep-work OS sandbox settings."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from autonoma import sandbox as sbx
from autonoma.sandbox import CodeSandbox, Language, SandboxLimits


@pytest.fixture
def fake_srt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    srt = tmp_path / "srt"
    srt.write_text("#!/bin/sh\nexit 0\n")
    srt.chmod(0o755)
    monkeypatch.setattr(sbx, "_srt_binary", lambda: str(srt))
    monkeypatch.setattr(sbx, "_configured_backend", lambda: "auto")
    monkeypatch.setattr(sbx.platform, "system", lambda: "Darwin")
    return srt


def test_auto_prefers_srt_when_installed(fake_srt: Path) -> None:
    assert sbx._detect_backend() == "srt"
    assert sbx.backend_info()["srt"] == str(fake_srt)


def test_explicit_backend_wins(monkeypatch: pytest.MonkeyPatch, fake_srt: Path) -> None:
    monkeypatch.setattr(sbx, "_configured_backend", lambda: "rlimit")
    assert sbx._detect_backend() == "rlimit"
    monkeypatch.setattr(sbx, "_configured_backend", lambda: "srt")
    monkeypatch.setattr(sbx, "_srt_binary", lambda: None)
    assert sbx._detect_backend() in ("seatbelt", "rlimit")  # falls back to native


def test_srt_argv_policy_confines_fs_and_network(fake_srt: Path, tmp_path: Path) -> None:
    box = CodeSandbox(limits=SandboxLimits(memory_mb=128))
    assert box.backend == "srt"
    workdir = tmp_path / "work"
    workdir.mkdir()
    script = workdir / "program.py"
    script.write_text("print(1)")
    argv = box._build_argv(Language.PYTHON, script, workdir)

    assert argv[0] == str(fake_srt) and argv[1] == "--settings"
    policy = json.loads(Path(argv[2]).read_text())
    assert policy["network"] == {"allowedDomains": [], "deniedDomains": []}
    fs = policy["filesystem"]
    assert fs["allowWrite"] == [str(workdir.resolve())]
    home = Path.home().resolve()
    assert str(home / ".ssh") in fs["denyRead"] and str(home / ".aws") in fs["denyRead"]
    assert fs["allowRead"] == []  # default-allow reads; deny-all-except breaks venv interpreters
    # srt runs a -c shell string: memory/process caps via ulimit, then exec.
    assert argv[3] == "-c" and "ulimit -v 131072" in argv[4] and "ulimit -u" in argv[4]
    assert argv[4].endswith(f"exec {sys.executable} -I -B {script}")
    assert len(argv) == 5


def test_preexec_skips_memory_limits_for_srt() -> None:
    import resource

    limits = SandboxLimits(memory_mb=64)
    full = sbx._make_preexec(limits)
    light = sbx._make_preexec(limits, light=True)
    assert full is not None and light is not None
    # Introspect the closures' rlimit lists.
    consts_full = {c for c, _ in full.__closure__[1].cell_contents}  # type: ignore[index]
    consts_light = {c for c, _ in light.__closure__[1].cell_contents}  # type: ignore[index]
    assert resource.RLIMIT_AS in consts_full
    assert resource.RLIMIT_AS not in consts_light
    assert resource.RLIMIT_NPROC not in consts_light
    assert resource.RLIMIT_CPU in consts_light


@pytest.mark.asyncio
async def test_srt_run_end_to_end_with_fake_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The fake srt just execs its command, proving the argv shape runs."""
    srt = tmp_path / "srt"
    # Drop ``--settings <file>`` and run the ``-c <string>`` like srt would.
    srt.write_text('#!/bin/sh\nshift 2\nexec /bin/sh "$@"\n')
    srt.chmod(0o755)
    monkeypatch.setattr(sbx, "_srt_binary", lambda: str(srt))
    monkeypatch.setattr(sbx, "_configured_backend", lambda: "srt")
    box = CodeSandbox(limits=SandboxLimits(memory_mb=512))
    result = await box.run("print('sandboxed')", Language.PYTHON)
    assert result.backend == "srt"
    assert result.ok, result.stderr
    assert result.stdout.strip() == "sandboxed"


def test_deepwork_sandbox_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from autonoma.agents import deepwork

    class Opts:
        def __init__(self, **kw: Any) -> None:
            self.kwargs = kw

    sdk = type("SDK", (), {"ClaudeAgentOptions": Opts})
    monkeypatch.setattr(deepwork.settings, "deep_work_subagents", False)
    monkeypatch.setattr(deepwork.settings, "deep_work_sandbox", True)
    monkeypatch.setattr(deepwork.settings, "deep_work_sandbox_allowed_domains", ["pypi.org"])
    session = deepwork.DeepWorkSession(
        agent_name="Ada",
        role="coder",
        task_id="t",
        task_title="x",
        task_description="",
        project_description="",
        project_dir=tmp_path,
    )
    kw = session._options(sdk).kwargs
    assert kw["permission_mode"] == "acceptEdits"
    assert kw["sandbox"]["enabled"] is True
    assert kw["sandbox"]["allowUnsandboxedCommands"] is False
    assert kw["sandbox"]["network"]["allowedDomains"] == ["pypi.org"]

    monkeypatch.setattr(deepwork.settings, "deep_work_sandbox", False)
    kw = session._options(sdk).kwargs
    assert kw["permission_mode"] == "bypassPermissions" and "sandbox" not in kw

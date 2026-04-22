"""Tests for the CodeSandbox module.

These tests run real subprocesses through the sandbox; nothing is mocked.
On macOS the seatbelt backend is used, on Linux bwrap (or rlimit fallback).
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from autonoma.sandbox import (
    CodeSandbox,
    Language,
    SandboxLimits,
    backend_info,
    configure_concurrency,
)


async def test_python_happy_path():
    sandbox = CodeSandbox()
    result = await sandbox.run("print(2+2)", Language.PYTHON)
    assert result.ok
    assert result.exit_code == 0
    assert "4" in result.stdout


async def test_bash_happy_path():
    sandbox = CodeSandbox()
    result = await sandbox.run("echo hello", Language.BASH)
    assert result.ok
    assert "hello" in result.stdout


async def test_non_zero_exit_code():
    sandbox = CodeSandbox()
    result = await sandbox.run("import sys; sys.exit(3)", Language.PYTHON)
    assert not result.ok
    assert result.exit_code == 3
    assert not result.timed_out


async def test_stderr_capture():
    sandbox = CodeSandbox()
    code = "import sys; sys.stderr.write('boom!\\n')"
    result = await sandbox.run(code, Language.PYTHON)
    assert "boom!" in result.stderr
    assert result.stdout == ""


async def test_wall_time_timeout():
    limits = SandboxLimits(wall_time_sec=1.0, cpu_time_sec=2)
    sandbox = CodeSandbox(limits=limits)
    started = time.monotonic()
    result = await sandbox.run("while True: pass", Language.PYTHON)
    elapsed = time.monotonic() - started
    assert result.timed_out
    assert result.killed_reason == "wall_timeout"
    assert not result.ok
    assert elapsed < 5.0


async def test_output_truncation():
    limits = SandboxLimits(max_output_bytes=2048)
    sandbox = CodeSandbox(limits=limits)
    result = await sandbox.run('print("x" * 500_000)', Language.PYTHON)
    assert result.truncated is True
    assert len(result.stdout) <= 2048 + 64


async def test_env_scrubbing(monkeypatch):
    secret = "sk-test-deadbeef"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    sandbox = CodeSandbox()
    code = 'import os; print(os.environ.get("ANTHROPIC_API_KEY", "ABSENT"))'
    result = await sandbox.run(code, Language.PYTHON)
    assert "ABSENT" in result.stdout
    assert secret not in result.stdout
    assert secret not in result.stderr


async def test_network_denied_on_seatbelt():
    if backend_info()["backend"] != "seatbelt":
        pytest.skip("network deny test only meaningful on seatbelt backend")
    sandbox = CodeSandbox()
    code = (
        "import socket\n"
        "s = socket.socket()\n"
        "s.settimeout(1)\n"
        "s.connect(('1.1.1.1', 80))\n"
    )
    result = await sandbox.run(code, Language.PYTHON)
    assert not result.ok


async def test_sensitive_path_denied_on_seatbelt():
    if backend_info()["backend"] != "seatbelt":
        pytest.skip("sensitive path deny test only meaningful on seatbelt backend")
    sandbox = CodeSandbox()
    code = (
        "import os\n"
        'open(os.path.expanduser("~/.ssh/id_rsa")).read()\n'
    )
    result = await sandbox.run(code, Language.PYTHON)
    assert not result.ok


async def test_workdir_isolation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sandbox = CodeSandbox()
    code = (
        "import os\n"
        'open("out.txt", "w").write("hi")\n'
        'print(os.listdir("."))\n'
    )
    result = await sandbox.run(code, Language.PYTHON)
    assert result.ok
    assert "out.txt" in result.stdout
    assert not (tmp_path / "out.txt").exists()


async def test_concurrency_semaphore():
    configure_concurrency(2)
    try:
        sandbox = CodeSandbox()
        code = 'import time; time.sleep(0.6); print("done")'

        async def _run():
            return await sandbox.run(code, Language.PYTHON)

        started = time.monotonic()
        results = await asyncio.gather(*[_run() for _ in range(4)])
        elapsed = time.monotonic() - started

        assert all(r.ok for r in results)
        assert elapsed >= 1.1
        assert elapsed < 4.5
    finally:
        configure_concurrency(2)


async def test_language_string_coercion():
    sandbox = CodeSandbox()
    result = await sandbox.run("echo hi", "bash")
    assert result.ok
    assert "hi" in result.stdout


async def test_summarize_format():
    sandbox = CodeSandbox()
    result = await sandbox.run("print('hi')", Language.PYTHON)
    summary = result.summarize(50)
    assert isinstance(summary, str)
    assert summary.startswith("[")
    assert "OK" in summary


def test_backend_info_shape():
    info = backend_info()
    assert isinstance(info, dict)
    for key in ("system", "backend", "sandbox_exec", "bwrap", "max_concurrent"):
        assert key in info


# ── Regression tests ─────────────────────────────────────────────────
# The sandbox has three independent ways to kill a runaway process
# (wall clock, CPU time, memory). Each is wired in a different code path
# (asyncio timeout vs. RLIMIT_CPU vs. RLIMIT_AS), so losing any one
# without the others failing produces silent degradation — the process
# just runs longer or burns more RAM than configured. These tests pin
# each path independently.


async def test_cpu_time_limit_kills_busy_loop():
    """Tight CPU loop should be killed by RLIMIT_CPU before wall clock.

    We set a CPU cap of 1s with a much larger wall cap (8s) so a pure
    wall-clock kill would be ambiguous. A busy loop saturates CPU ≈ 1s
    of userland per wall second, so the signal should arrive within a
    couple of seconds of real time. Not applicable to backends that
    don't pre-exec the rlimit (none of ours skip it today, but the
    check is here in case a future backend does).
    """
    if backend_info()["system"] == "Windows":
        pytest.skip("rlimits unavailable on Windows")
    limits = SandboxLimits(wall_time_sec=8.0, cpu_time_sec=1)
    sandbox = CodeSandbox(limits=limits)
    started = time.monotonic()
    # Bash loop is a reliable CPU burner across interpreters.
    result = await sandbox.run("while :; do :; done", Language.BASH)
    elapsed = time.monotonic() - started
    assert not result.ok
    # We should kill via CPU, not wall — wall would only trip after 8s.
    # The process may be reported as timed_out or not depending on which
    # signal lands first; the key invariant is the wall clock didn't
    # have to wait for the 8s cap.
    assert elapsed < 5.0, (
        f"CPU-bound code ran {elapsed:.2f}s — RLIMIT_CPU didn't kill it"
    )


async def test_memory_limit_kills_allocator():
    """Allocating far beyond memory_mb should fail the process.

    We try to allocate 2 GB when the cap is 64 MB. Behavior differs by
    platform:
      - Linux: RLIMIT_AS enforces — the allocator raises
        MemoryError and the script exits non-zero.
      - macOS: RLIMIT_AS is advisory (see sandbox.backend_info()
        rlimit_memory_enforced flag). The allocation may succeed. We
        skip on macOS rather than pretending the test is meaningful.
    """
    info = backend_info()
    if not info.get("rlimit_memory_enforced", False):
        pytest.skip(
            "memory rlimit is not kernel-enforced on this platform "
            f"(backend={info['backend']}, system={info['system']})"
        )
    limits = SandboxLimits(memory_mb=64, wall_time_sec=6.0, cpu_time_sec=3)
    sandbox = CodeSandbox(limits=limits)
    code = (
        "buf = bytearray(2 * 1024 * 1024 * 1024)\n"
        "print('LEAKED', len(buf))\n"
    )
    result = await sandbox.run(code, Language.PYTHON)
    assert not result.ok, "oversized allocation succeeded — rlimit not enforced"
    assert "LEAKED" not in result.stdout


async def test_backend_info_reports_memory_enforcement_honestly():
    """The diagnostic flag should match the running system.

    This exists specifically to catch the case where someone copies the
    Linux branch to another Unix without auditing whether RLIMIT_AS
    actually caps RSS there. Flag value is load-bearing — callers
    (including the memory regression test above) use it to decide
    whether to skip.
    """
    info = backend_info()
    assert isinstance(info.get("rlimit_memory_enforced"), bool)
    # Linux: kernel enforces. macOS: known not-enforced (documented
    # in sandbox._make_preexec).
    if info["system"] == "Linux":
        assert info["rlimit_memory_enforced"] is True
    elif info["system"] == "Darwin":
        assert info["rlimit_memory_enforced"] is False

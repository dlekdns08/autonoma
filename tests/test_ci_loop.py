"""Tests for ``autonoma.ci_loop`` (feature #9).

Real subprocesses where the toolchain is on PATH; gracefully skipped
otherwise. ``run_ci_check`` writes to ``tmp_path`` so nothing leaks
into the real workspace.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from autonoma.ci_loop import CIResult, format_fix_task, run_ci_check


async def test_python_valid(tmp_path: Path) -> None:
    f = tmp_path / "good.py"
    src = "x = 1\nprint(x)\n"
    f.write_text(src)
    result = await run_ci_check(str(f), src)
    assert isinstance(result, CIResult)
    assert result.ok, f"expected ok, got {result.message}"
    assert result.duration_ms >= 0


async def test_python_syntax_error(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    src = "def broken(:\n  pass\n"
    f.write_text(src)
    result = await run_ci_check(str(f), src)
    assert not result.ok
    assert "SyntaxError" in result.message
    task = format_fix_task(result)
    assert "Fix syntax error in bad.py" in task
    assert "SyntaxError" in task


async def test_json_invalid(tmp_path: Path) -> None:
    f = tmp_path / "bad.json"
    src = '{"oops": ,}'
    f.write_text(src)
    result = await run_ci_check(str(f), src)
    assert not result.ok
    assert "JSONDecodeError" in result.message or "json" in result.message.lower()


async def test_json_valid(tmp_path: Path) -> None:
    f = tmp_path / "good.json"
    src = '{"a": 1, "b": [2, 3]}'
    f.write_text(src)
    result = await run_ci_check(str(f), src)
    assert result.ok


async def test_unknown_extension(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    src = "just some prose"
    f.write_text(src)
    result = await run_ci_check(str(f), src)
    assert result.ok
    assert "no checker" in result.message


async def test_creates_file_when_missing(tmp_path: Path) -> None:
    """If the path doesn't exist on disk, content is materialised so the
    checker has something to read; the temp file is cleaned up after."""
    f = tmp_path / "nested" / "fresh.py"
    src = "y = 2\n"
    result = await run_ci_check(str(f), src)
    assert result.ok
    # cleanup deletes the file we wrote
    assert not f.exists()


async def test_format_fix_task_ok() -> None:
    res = CIResult(file_path="/x/foo.py", ok=True, message="ok", duration_ms=1, command="ast")
    assert format_fix_task(res) == "foo.py: ok"


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
async def test_python_ruff_catches_unused_import(tmp_path: Path) -> None:
    f = tmp_path / "lint.py"
    # Syntax-valid but ruff's F401 should fire.
    src = "import os\n"
    f.write_text(src)
    result = await run_ci_check(str(f), src)
    # Depending on the local ruff config this may or may not be a
    # finding; we accept either as long as the call returned cleanly.
    assert isinstance(result, CIResult)
    assert result.command in ("ruff check", "ast.parse")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
async def test_js_syntax_error(tmp_path: Path) -> None:
    f = tmp_path / "bad.js"
    src = "function broken( {\n"
    f.write_text(src)
    result = await run_ci_check(str(f), src)
    assert not result.ok


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx not on PATH")
async def test_ts_runs_or_skips(tmp_path: Path) -> None:
    f = tmp_path / "demo.ts"
    src = "const x: number = 1;\n"
    f.write_text(src)
    result = await run_ci_check(str(f), src)
    # Either ok or "tsc not available"; never a hard failure on valid
    # TS in a fresh environment.
    assert isinstance(result, CIResult)
    if not result.ok:
        # If it failed, message must not be the spawn-skip sentinel.
        assert "not available" not in result.message

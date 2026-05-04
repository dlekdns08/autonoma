"""CI loop — auto lint/typecheck/parse for agent-created files (feature #9).

Defensive contract: ``run_ci_check`` NEVER raises. Missing tools, spawn
failures, and binary stderr all funnel into a ``CIResult`` so callers can
route the message into the next-round inbox without try/except.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from autonoma.config import settings


__all__ = ["CIResult", "run_ci_check", "format_fix_task"]

_STDERR_TRIM = 500


@dataclass
class CIResult:
    file_path: str
    ok: bool
    message: str
    duration_ms: int
    command: str


def _trim(text: str, limit: int = _STDERR_TRIM) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _ext(file_path: str, language_hint: str | None) -> str:
    if language_hint:
        h = language_hint.lower().lstrip(".")
        return {"python": "py", "typescript": "ts", "javascript": "js"}.get(h, h)
    return Path(file_path).suffix.lstrip(".").lower()


def _result(file_path: str, ok: bool, message: str, started: float, command: str) -> CIResult:
    return CIResult(
        file_path=file_path,
        ok=ok,
        message=message,
        duration_ms=int((time.monotonic() - started) * 1000),
        command=command,
    )


async def _run(argv: list[str], *, timeout: float) -> tuple[int, str, str]:
    """Run a subprocess; capture (rc, stdout, stderr). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError, OSError) as e:
        return -1, "", f"failed to spawn {argv[0]}: {e}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return -1, "", f"timeout after {timeout:.1f}s"
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def run_ci_check(
    file_path: str,
    content: str,
    language_hint: str | None = None,
) -> CIResult:
    """Lint / typecheck / parse a single file. Never raises."""
    timeout = float(settings.ci_loop_timeout_sec or 30.0)
    started = time.monotonic()
    p = Path(file_path)

    # Materialise content on disk if the path doesn't exist yet — the
    # checkers all read from a real file. Track ``cleanup`` so we don't
    # litter the workspace when we wrote it ourselves.
    cleanup: Path | None = None
    if p.exists():
        target = p
    else:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            target, cleanup = p, p
        except OSError:
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=p.suffix, prefix="ci_loop_")
            os.close(fd)
            target = Path(tmp)
            target.write_text(content, encoding="utf-8")
            cleanup = target

    try:
        override = (settings.ci_loop_command or "").strip()
        if override:
            cmd_str = override.format(file=str(target))
            rc, out, err = await _run(shlex.split(cmd_str), timeout=timeout)
            ok = rc == 0
            return _result(file_path, ok, "ok" if ok else _trim(err or out), started, cmd_str)

        ext = _ext(file_path, language_hint)
        if ext == "py":
            return await _check_python(file_path, target, started, timeout)
        if ext in ("ts", "tsx"):
            return await _check_ts(file_path, target, started, timeout)
        if ext == "js":
            return await _check_js(file_path, target, started, timeout)
        if ext == "json":
            return await _check_json(file_path, target, started)
        return _result(
            file_path,
            True,
            f"no checker for extension '.{ext}'" if ext else "no checker for extension",
            started,
            "",
        )
    finally:
        if cleanup is not None:
            try:
                cleanup.unlink()
            except OSError:
                pass


async def _check_python(file_path: str, target: Path, started: float, timeout: float) -> CIResult:
    # Cheap syntax pass first — covers the common "agent dropped broken
    # python" case without paying for a ruff spawn.
    src = target.read_text(encoding="utf-8", errors="replace")
    try:
        compile(src, str(target), "exec")
    except SyntaxError as e:
        return _result(file_path, False, f"SyntaxError: {e.msg} at line {e.lineno}", started, "ast.parse")
    if shutil.which("ruff"):
        rc, out, err = await _run(["ruff", "check", str(target)], timeout=timeout)
        if rc == 0:
            return _result(file_path, True, "ok", started, "ruff check")
        return _result(file_path, False, _trim(out or err), started, "ruff check")
    return _result(file_path, True, "ok (ruff not installed)", started, "ast.parse")


async def _check_ts(file_path: str, target: Path, started: float, timeout: float) -> CIResult:
    if not shutil.which("npx"):
        return _result(file_path, True, "tsc not available", started, "")
    cmd = ["npx", "--no-install", "tsc", "--noEmit",
           "--target", "es2022", "--module", "esnext", str(target)]
    rc, out, err = await _run(cmd, timeout=timeout)
    cmd_str = " ".join(cmd)
    # ``--no-install`` reports "could not determine executable" when tsc
    # is missing locally — treat that as skip, not failure.
    combined = ((out or "") + "\n" + (err or "")).lower()
    if rc != 0 and ("could not determine executable" in combined or "not found" in combined):
        return _result(file_path, True, "tsc not available", started, cmd_str)
    if rc == 0:
        return _result(file_path, True, "ok", started, cmd_str)
    return _result(file_path, False, _trim(out or err), started, cmd_str)


async def _check_js(file_path: str, target: Path, started: float, timeout: float) -> CIResult:
    if not shutil.which("node"):
        return _result(file_path, True, "node not available", started, "")
    cmd = ["node", "--check", str(target)]
    rc, out, err = await _run(cmd, timeout=timeout)
    if rc == 0:
        return _result(file_path, True, "ok", started, " ".join(cmd))
    return _result(file_path, False, _trim(err or out), started, " ".join(cmd))


async def _check_json(file_path: str, target: Path, started: float) -> CIResult:
    import json
    try:
        json.loads(target.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return _result(file_path, False, f"JSONDecodeError: {e.msg} at line {e.lineno}", started, "json.load")
    except OSError as e:
        return _result(file_path, False, f"OSError: {e}", started, "json.load")
    return _result(file_path, True, "ok", started, "json.load")


def format_fix_task(result: CIResult) -> str:
    """Render a CIResult as a short, agent-friendly task description."""
    name = Path(result.file_path).name or result.file_path
    if result.ok:
        return f"{name}: {result.message}"
    msg = (result.message or "unknown failure").splitlines()[0].strip()
    if len(msg) > 200:
        msg = msg[:197] + "..."
    return f"Fix syntax error in {name}: {msg}"

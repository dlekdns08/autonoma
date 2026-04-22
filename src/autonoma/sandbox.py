"""Sandboxed code execution for Autonoma agents.

Lets agents actually run code they write and observe the result, without
letting that code touch the host system. Port of Claude Code's sandbox
philosophy (see PoC/claude-code-source-code/src/utils/sandbox/) adapted for:
- multi-agent concurrency (global semaphore)
- tight resource limits (rlimit via preexec_fn on POSIX)
- platform-aware backend: macOS sandbox-exec, Linux bwrap, rlimit-only fallback.

Only the coder/tester harnesses get the run_code capability by default.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import shutil
import stat
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Language(str, Enum):
    PYTHON = "python"
    BASH = "bash"
    NODE = "node"


LANG_EXTENSIONS: dict[Language, str] = {
    Language.PYTHON: ".py",
    Language.BASH: ".sh",
    Language.NODE: ".js",
}

LANG_INTERPRETERS: dict[Language, list[str]] = {
    Language.PYTHON: [sys.executable, "-I", "-B"],
    Language.BASH: ["/bin/bash", "--noprofile", "--norc"],
    Language.NODE: ["node", "--no-warnings"],
}


@dataclass(frozen=True)
class SandboxLimits:
    """Per-execution resource caps. Defaults sized for an 8-core / 12 GB host
    with ~2 concurrent agents running code at the same time."""

    wall_time_sec: float = 8.0
    cpu_time_sec: int = 5
    memory_mb: int = 384
    max_processes: int = 32
    max_output_bytes: int = 64 * 1024
    max_file_bytes: int = 4 * 1024 * 1024


@dataclass
class SandboxResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    truncated: bool
    backend: str
    language: str
    timed_out: bool = False
    killed_reason: Optional[str] = None
    error: Optional[str] = None

    def summarize(self, max_chars: int = 400) -> str:
        """Short human-readable summary for agent memory / event log."""
        status = "OK" if self.ok else ("TIMEOUT" if self.timed_out else "FAIL")
        head = f"[{self.backend}:{self.language}] {status} exit={self.exit_code} in {self.duration_sec:.2f}s"
        tail = (self.stdout or self.stderr or "").strip()
        if len(tail) > max_chars:
            tail = tail[:max_chars] + "…"
        return f"{head}\n{tail}" if tail else head


# ── concurrency ─────────────────────────────────────────────────────────────

_SEM: Optional[asyncio.Semaphore] = None
_SEM_CAPACITY = 2


def configure_concurrency(max_concurrent: int) -> None:
    """Override the global concurrency cap. Must be called before first use."""
    global _SEM, _SEM_CAPACITY
    _SEM_CAPACITY = max(1, int(max_concurrent))
    _SEM = None


def _semaphore() -> asyncio.Semaphore:
    global _SEM
    if _SEM is None:
        _SEM = asyncio.Semaphore(_SEM_CAPACITY)
    return _SEM


# ── env hygiene ─────────────────────────────────────────────────────────────

_BLOCKED_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "HUGGINGFACE_TOKEN",
    "SSH_AUTH_SOCK",
    "KUBECONFIG",
    "VAULT_TOKEN",
    "NPM_TOKEN",
    "PYPI_TOKEN",
}


def _sanitized_env(workdir: Path) -> dict[str, str]:
    safe_keys = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM")
    env: dict[str, str] = {k: os.environ[k] for k in safe_keys if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["HOME"] = str(workdir)
    env["TMPDIR"] = str(workdir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    for k in _BLOCKED_ENV_KEYS:
        env.pop(k, None)
    return env


# ── rlimit preexec (POSIX) ──────────────────────────────────────────────────

def _make_preexec(limits: SandboxLimits):
    """Build the ``preexec_fn`` that applies rlimits to the sandboxed child.

    macOS note: seatbelt profiles express filesystem + network rules but
    CANNOT express CPU/memory/process caps. Those must come from rlimit,
    which is why this preexec runs under both ``seatbelt`` and ``rlimit``
    backends. The kernel enforces ``RLIMIT_CPU``, ``RLIMIT_FSIZE``,
    ``RLIMIT_NPROC``, ``RLIMIT_NOFILE`` and ``RLIMIT_CORE`` reliably on
    Darwin; ``RLIMIT_AS``/``RLIMIT_DATA`` are best-effort (the Darwin
    kernel historically ignores them for user processes). We also set
    ``RLIMIT_RSS`` where available — also best-effort on mac, real on
    Linux. The wall-clock ``asyncio.wait_for`` timeout in ``_run_locked``
    is the belt-and-suspenders catch for any case where a memory limit
    doesn't actually kill the process.
    """
    if os.name != "posix":
        return None
    import resource

    mem_bytes = limits.memory_mb * 1024 * 1024
    fsize_bytes = limits.max_file_bytes

    rlimits: list[tuple[int, tuple[int, int]]] = []

    def _add(name: str, value: tuple[int, int]) -> None:
        const = getattr(resource, name, None)
        if const is not None:
            rlimits.append((const, value))

    _add("RLIMIT_CPU", (limits.cpu_time_sec, limits.cpu_time_sec))
    _add("RLIMIT_AS", (mem_bytes, mem_bytes))
    _add("RLIMIT_DATA", (mem_bytes, mem_bytes))
    _add("RLIMIT_RSS", (mem_bytes, mem_bytes))
    _add("RLIMIT_STACK", (64 * 1024 * 1024, 64 * 1024 * 1024))
    _add("RLIMIT_FSIZE", (fsize_bytes, fsize_bytes))
    _add("RLIMIT_NOFILE", (256, 256))
    _add("RLIMIT_NPROC", (limits.max_processes, limits.max_processes))
    _add("RLIMIT_CORE", (0, 0))

    def _apply() -> None:
        for const, value in rlimits:
            with contextlib.suppress(Exception):
                resource.setrlimit(const, value)

    return _apply


# ── seatbelt profile (macOS) ────────────────────────────────────────────────

def _macos_profile(workdir: Path) -> str:
    # Seatbelt subpath rules compare against the resolved path, so we must
    # use realpath — /var -> /private/var aliasing otherwise denies our writes.
    real_workdir = os.path.realpath(str(workdir))
    home = os.path.realpath(os.path.expanduser("~"))
    return f"""(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
(allow signal (target self))
(allow sysctl-read)
(allow mach-lookup)
(allow ipc-posix-shm)
(allow file-read*)
(allow file-write* (subpath "{real_workdir}"))
(allow file-write* (subpath "/private/tmp"))
(allow file-write* (subpath "/tmp"))
(allow file-write* (literal "/dev/null"))
(allow file-write* (literal "/dev/dtracehelper"))
(deny file-read* (subpath "{home}/.ssh"))
(deny file-read* (subpath "{home}/.aws"))
(deny file-read* (subpath "{home}/.anthropic"))
(deny file-read* (subpath "{home}/.config/anthropic"))
(deny file-read* (subpath "{home}/.config/claude"))
(deny file-read* (subpath "{home}/.gnupg"))
(deny file-read* (subpath "{home}/Library/Keychains"))
(deny file-read* (subpath "{home}/Library/Application Support/Google"))
(deny network*)
"""


# ── backend detection ───────────────────────────────────────────────────────

def _detect_backend() -> str:
    system = platform.system()
    if system == "Darwin" and shutil.which("sandbox-exec"):
        return "seatbelt"
    if system == "Linux" and shutil.which("bwrap"):
        return "bwrap"
    return "rlimit"


def backend_info() -> dict[str, object]:
    """Diagnostic: what backend would be used and what's installed.

    ``rlimit_memory_enforced`` reflects kernel-level confidence that
    ``RLIMIT_AS``/``RLIMIT_DATA`` will actually bound memory. True on
    Linux; on Darwin the kernel historically ignores them, so operators
    should rely on the wall-clock timeout and the seatbelt sandbox's
    process-exec rules rather than memory rlimits.
    """
    system = platform.system()
    return {
        "system": system,
        "backend": _detect_backend(),
        "sandbox_exec": shutil.which("sandbox-exec"),
        "bwrap": shutil.which("bwrap"),
        "max_concurrent": _SEM_CAPACITY,
        "rlimit_memory_enforced": system == "Linux",
    }


# ── core sandbox ────────────────────────────────────────────────────────────

class CodeSandbox:
    """Run short, agent-authored programs with filesystem + network isolation.

    Usage:
        result = await CodeSandbox().run("print(2+2)", Language.PYTHON)
    """

    def __init__(self, limits: Optional[SandboxLimits] = None) -> None:
        self.limits = limits or SandboxLimits()
        self.backend = _detect_backend()

    async def run(self, code: str, language: Language = Language.PYTHON) -> SandboxResult:
        if not isinstance(language, Language):
            language = Language(str(language).lower())
        async with _semaphore():
            return await self._run_locked(code, language)

    async def _run_locked(self, code: str, language: Language) -> SandboxResult:
        if language == Language.NODE and not shutil.which("node"):
            return SandboxResult(
                ok=False, exit_code=127, stdout="", stderr="node not installed",
                duration_sec=0.0, truncated=False, backend=self.backend,
                language=language.value, error="interpreter_missing",
            )

        workdir = Path(tempfile.mkdtemp(prefix=f"autonoma-sbx-{uuid.uuid4().hex[:6]}-"))
        try:
            try:
                os.chmod(workdir, stat.S_IRWXU)
            except OSError:
                pass

            script_path = workdir / f"program{LANG_EXTENSIONS[language]}"
            script_path.write_text(code, encoding="utf-8")
            script_path.chmod(0o600)

            argv = self._build_argv(language, script_path, workdir)
            env = _sanitized_env(workdir)
            preexec = _make_preexec(self.limits)
            start = time.monotonic()

            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workdir),
                    env=env,
                    preexec_fn=preexec,
                    start_new_session=True,
                )
            except FileNotFoundError as exc:
                return SandboxResult(
                    ok=False, exit_code=127, stdout="", stderr=str(exc),
                    duration_sec=0.0, truncated=False, backend=self.backend,
                    language=language.value, error="spawn_failed",
                )

            stdout_bytes, stderr_bytes, timed_out, truncated = await self._collect_output(proc)
            duration = time.monotonic() - start
            exit_code = proc.returncode if proc.returncode is not None else -1

            return SandboxResult(
                ok=(exit_code == 0 and not timed_out),
                exit_code=exit_code,
                stdout=_decode(stdout_bytes),
                stderr=_decode(stderr_bytes),
                duration_sec=round(duration, 4),
                truncated=truncated,
                backend=self.backend,
                language=language.value,
                timed_out=timed_out,
                killed_reason="wall_timeout" if timed_out else None,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _build_argv(self, language: Language, script: Path, workdir: Path) -> list[str]:
        interp = LANG_INTERPRETERS[language]
        inner = [*interp, str(script)]

        if self.backend == "seatbelt":
            profile = _macos_profile(workdir)
            return ["/usr/bin/sandbox-exec", "-p", profile, *inner]

        if self.backend == "bwrap":
            return self._bwrap_argv(inner, workdir)

        return inner

    def _bwrap_argv(self, inner: list[str], workdir: Path) -> list[str]:
        args: list[str] = [
            "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--tmpfs", "/run",
            "--tmpfs", "/var",
        ]
        for ro in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/alternatives", "/etc/ssl"):
            if Path(ro).exists():
                args += ["--ro-bind", ro, ro]
        for passthrough in ("/etc/resolv.conf", "/etc/nsswitch.conf", "/etc/hosts"):
            if Path(passthrough).exists():
                args += ["--ro-bind-try", passthrough, passthrough]
        args += ["--bind", str(workdir), str(workdir), "--chdir", str(workdir)]
        args += ["--setenv", "HOME", str(workdir), "--setenv", "TMPDIR", str(workdir)]
        args += ["--cap-drop", "ALL"]
        args += inner
        return args

    async def _collect_output(
        self, proc: asyncio.subprocess.Process
    ) -> tuple[bytes, bytes, bool, bool]:
        cap = self.limits.max_output_bytes
        stdout_buf = bytearray()
        stderr_buf = bytearray()
        truncated = False

        async def _drain(stream: asyncio.StreamReader | None, buf: bytearray) -> None:
            nonlocal truncated
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                remaining = cap - len(buf)
                if remaining <= 0:
                    truncated = True
                    return
                buf.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
                    return

        drain_out = asyncio.create_task(_drain(proc.stdout, stdout_buf))
        drain_err = asyncio.create_task(_drain(proc.stderr, stderr_buf))

        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), timeout=self.limits.wall_time_sec)
        except asyncio.TimeoutError:
            timed_out = True
            _kill_group(proc)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=1.5)

        # Proc exited, but the drain tasks may still be reading buffered
        # pipe data that the kernel hasn't surfaced yet. Give them a
        # bounded grace period to reach EOF before cancelling — otherwise
        # fast commands like `echo hi` race the cancel and we lose their
        # output on a loaded CI runner.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.gather(drain_out, drain_err, return_exceptions=True),
                timeout=2.0,
            )

        for task in (drain_out, drain_err):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        return bytes(stdout_buf), bytes(stderr_buf), timed_out, truncated


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    import signal
    try:
        pgid = os.getpgid(proc.pid)
    except (OSError, AttributeError, ProcessLookupError):
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")

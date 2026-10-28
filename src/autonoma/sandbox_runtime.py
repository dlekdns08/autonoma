"""Auto-provisioning of Anthropic's sandbox runtime (``srt``).

``srt`` is an npm CLI, so a fresh checkout would otherwise need a manual
``npm i -g @anthropic-ai/sandbox-runtime`` before ``run_code`` gets OS
isolation. Instead, on startup (API lifespan, CLI runner) we look for it
and, when it is missing and ``npm`` is available, install a pinned
version into ``data_dir/sandbox-runtime/`` — no global install, no root.

Lookup order (``find_srt``): ``AUTONOMA_SRT_BINARY`` → ``srt`` on PATH →
the local install. The Docker image installs it globally instead.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from autonoma.config import settings

logger = logging.getLogger(__name__)

PACKAGE = "@anthropic-ai/sandbox-runtime"
_lock: asyncio.Lock | None = None


def local_install_dir() -> Path:
    return Path(settings.data_dir) / "sandbox-runtime"


def local_binary() -> Path:
    return local_install_dir() / "node_modules" / ".bin" / "srt"


def find_srt() -> str | None:
    """Path to a usable ``srt`` binary, or ``None``."""
    configured = (getattr(settings, "srt_binary", "") or "").strip()
    if configured:
        if Path(configured).exists():
            return configured
        return shutil.which(configured)
    on_path = shutil.which("srt")
    if on_path:
        return on_path
    local = local_binary()
    if local.exists() and os.access(local, os.X_OK):
        return str(local)
    return None


def _wanted() -> bool:
    return settings.sandbox_backend in ("auto", "srt")


async def ensure_srt(*, timeout_s: float = 240.0) -> str | None:
    """Return the ``srt`` path, installing it locally first if needed.

    Never raises: a missing ``npm``, an offline machine, or a failed
    install just logs and returns ``None`` (the sandbox then falls back to
    the OS-native backend). Concurrent callers share one install.
    """
    found = find_srt()
    if found or not _wanted() or not settings.sandbox_auto_install:
        return found
    if os.name != "posix":
        return None
    npm = shutil.which("npm")
    if not npm:
        logger.warning(
            "[sandbox] srt not found and npm is unavailable; run_code will use the "
            "OS-native backend. Install Node.js or `npm i -g %s`.",
            PACKAGE,
        )
        return None

    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        found = find_srt()
        if found:
            return found
        target = local_install_dir()
        target.mkdir(parents=True, exist_ok=True)
        spec = f"{PACKAGE}@{settings.srt_version}" if settings.srt_version else PACKAGE
        logger.info("[sandbox] installing %s into %s …", spec, target)
        argv = [
            npm,
            "install",
            "--prefix",
            str(target),
            "--no-audit",
            "--no-fund",
            "--loglevel=error",
            spec,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(target),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("[sandbox] srt install timed out after %.0fs", timeout_s)
                return None
        except (OSError, FileNotFoundError) as exc:
            logger.warning("[sandbox] srt install could not start: %s", exc)
            return None
        if proc.returncode != 0:
            logger.warning(
                "[sandbox] srt install failed (exit %s): %s",
                proc.returncode,
                (out or b"").decode("utf-8", "replace")[-400:].strip(),
            )
            return None
        found = find_srt()
        if found:
            logger.info("[sandbox] srt ready at %s", found)
        else:
            logger.warning("[sandbox] srt install finished but the binary was not found")
        return found


def schedule_ensure_srt() -> asyncio.Task[str | None] | None:
    """Fire-and-forget variant for server startup."""
    if find_srt() or not _wanted() or not settings.sandbox_auto_install:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    return loop.create_task(ensure_srt(), name="ensure-srt")


def status() -> dict[str, object]:
    return {
        "srt": find_srt(),
        "local_install_dir": str(local_install_dir()),
        "auto_install": settings.sandbox_auto_install,
        "backend_setting": settings.sandbox_backend,
        "npm": shutil.which("npm"),
    }

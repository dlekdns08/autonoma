"""Filesystem storage for voice reference audio.

Replaces the in-column BLOB approach so the DB stays small (SQLite
backup time and file-system images no longer balloon with multi-MB
audio blobs) and the audio preview endpoint can eventually be served
by nginx sendfile.

Layout: ``{settings.data_dir}/voice_refs/{profile_id}.{ext}``

The extension is derived from the canonical MIME type the upload
endpoint already sniffs (``audio/wav`` → ``.wav``, ``audio/ogg`` →
``.ogg``, …). The DB stores the *basename* only, not the full path —
so the deployment root can move without a migration.

Back-compat: old rows still have bytes in ``voice_profiles.ref_audio``
with ``ref_audio_path`` NULL. The store reads the BLOB as a fallback
when the path is absent, so no data migration is required on upgrade.
"""

from __future__ import annotations

import logging
from pathlib import Path

from autonoma.config import settings

logger = logging.getLogger(__name__)


_MIME_TO_EXT: dict[str, str] = {
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
}


def _voice_refs_dir() -> Path:
    """Resolve (and create) the voice refs directory. Cheap — idempotent."""
    root = Path(settings.data_dir) / "voice_refs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _extension_for_mime(mime: str) -> str:
    """Pick a stable file extension from the sniffed MIME."""
    return _MIME_TO_EXT.get(mime.lower(), ".bin")


def basename_for(profile_id: str, mime: str) -> str:
    """Compute the basename stored in ``voice_profiles.ref_audio_path``.

    Same ID → same basename (deterministic), so writes to existing
    profiles overwrite in place instead of leaking stale files.
    """
    return f"{profile_id}{_extension_for_mime(mime)}"


def write_ref_audio(profile_id: str, data: bytes, mime: str) -> str:
    """Persist ``data`` under ``{refs_dir}/{profile_id}.{ext}`` and return
    the basename that should be stored in the DB.
    """
    name = basename_for(profile_id, mime)
    path = _voice_refs_dir() / name
    path.write_bytes(data)
    return name


def read_ref_audio(basename: str) -> bytes | None:
    """Load the bytes for a stored ref audio, or ``None`` if the file is
    missing (operator moved the data dir, disk cleanup gone wrong, …).

    Returning ``None`` instead of raising lets the store caller fall
    back to the legacy BLOB column when present.
    """
    if not basename:
        return None
    path = _voice_refs_dir() / basename
    try:
        return path.read_bytes()
    except FileNotFoundError:
        logger.warning("[voice] ref audio missing on disk: %s", path)
        return None


def delete_ref_audio(basename: str) -> None:
    """Remove the on-disk file. Silently ignores missing files so the
    store's delete path is idempotent on partially-migrated rows."""
    if not basename:
        return
    path = _voice_refs_dir() / basename
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover — filesystem errors
        logger.warning("[voice] failed to delete %s: %s", path, exc)


__all__ = [
    "basename_for",
    "delete_ref_audio",
    "read_ref_audio",
    "write_ref_audio",
]

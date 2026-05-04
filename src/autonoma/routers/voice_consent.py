"""Voice-consent endpoints — Feature #15.

Adds a small ownership-gated layer on top of ``/api/voice-profiles``:

  POST /api/voice-profiles/{profile_id}/consent
  GET  /api/voice-profiles/{profile_id}/consent-status

The consent record itself lives on disk under
``{settings.data_dir}/voice_consent/{profile_id}.json`` rather than in
the SQL store — the spec calls this an in-memory consent map persisted
to JSON, and we don't need cross-process atomicity for what is a single
user-action upload.

Routes are intentionally additive: the existing voice router
(``routers/voice.py``) is untouched, so this module can be imported and
``include_router``ed on its own.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi import status as http_status

from autonoma import voice as voice_service
from autonoma.auth import User, require_active_user
from autonoma.config import settings
from autonoma.voice.consent import ConsentResult, verify_consent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice", "consent"])


# Same upload ceiling as the main profile uploader — the consent clip
# is the same kind of short utterance, so reusing the cap keeps the
# rejection messages consistent.
_MAX_CONSENT_BYTES = 4 * 1024 * 1024
_ALLOWED_LANGS = {"ko", "en"}


def _consent_dir() -> Path:
    """Where per-profile consent JSON files live. Created on demand."""
    d = settings.data_dir / "voice_consent"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _consent_path(profile_id: str) -> Path:
    return _consent_dir() / f"{profile_id}.json"


def _save_consent(profile_id: str, result: ConsentResult) -> None:
    """Atomic-ish write — temp file + rename so a crash mid-write can't
    leave a half-truncated JSON document the GET endpoint then chokes on.
    """
    path = _consent_path(profile_id)
    tmp = path.with_suffix(".json.tmp")
    payload = result.to_dict()
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_consent(profile_id: str) -> dict[str, Any] | None:
    path = _consent_path(profile_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Corrupt / partial file — surface as "no consent on record".
        # We don't delete it: an operator inspecting the data dir should
        # see that something went wrong rather than have the evidence
        # silently swept away.
        logger.warning(
            "[consent] failed to read consent file for profile_id=%s", profile_id,
            exc_info=True,
        )
        return None


def _consent_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


async def _require_owned_profile(profile_id: str, user: User) -> Any:
    """Return the profile summary if the caller owns it; otherwise 404.

    I2 fix: both the "no such profile" branch and the "exists but not
    yours" branch return the same ``404 profile_not_found`` shape so a
    hostile client can't enumerate which profile_ids exist on the box
    by watching for 403 vs 404 responses. The non-owner attempt is
    still audit-logged so an operator can see the probe.
    """
    summary = await voice_service.get_profile_summary(profile_id)
    if summary is None:
        raise _consent_error(
            http_status.HTTP_404_NOT_FOUND,
            "profile_not_found",
            "해당 프로필을 찾을 수 없습니다.",
        )
    if summary.owner_user_id != user.id and getattr(user, "role", "") != "admin":
        logger.warning(
            "[consent] non-owner attempted access profile_id=%s user_id=%s",
            profile_id,
            getattr(user, "id", None),
        )
        raise _consent_error(
            http_status.HTTP_404_NOT_FOUND,
            "profile_not_found",
            "해당 프로필을 찾을 수 없습니다.",
        )
    return summary


@router.post("/api/voice-profiles/{profile_id}/consent")
async def voice_profile_consent(
    profile_id: str,
    consent_audio: UploadFile = File(...),
    language: str = Form(...),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Verify a recorded consent phrase and, on success, persist the record.

    The ``ConsentResult`` is returned regardless of outcome so the UI
    can show the recognised transcript + similarity score and prompt
    the user to retry. Only ``ok=True`` results are persisted on disk —
    we don't want a botched read to mark a profile as consented.
    """
    lang = (language or "").strip().lower()
    if lang not in _ALLOWED_LANGS:
        raise _consent_error(
            http_status.HTTP_400_BAD_REQUEST,
            "invalid_language",
            "language 필드는 'ko' 또는 'en' 이어야 합니다.",
        )

    await _require_owned_profile(profile_id, user)

    data = await consent_audio.read()
    if not data:
        raise _consent_error(
            http_status.HTTP_400_BAD_REQUEST,
            "empty_audio",
            "오디오 파일이 비어 있습니다.",
        )
    if len(data) > _MAX_CONSENT_BYTES:
        raise _consent_error(
            http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "audio_too_large",
            f"오디오 파일이 너무 큽니다 (최대 {_MAX_CONSENT_BYTES // (1024 * 1024)} MB).",
        )

    result = await verify_consent(data, language=lang)

    if result.ok:
        try:
            _save_consent(profile_id, result)
        except OSError as exc:
            # Disk problems (full, read-only, perms) — don't pretend the
            # consent stuck. Caller should retry once the operator has
            # cleaned up the data volume.
            logger.exception("[consent] persist failed for profile_id=%s", profile_id)
            raise _consent_error(
                http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                "consent_persist_failed",
                f"동의 결과를 저장하지 못했습니다: {exc}",
            )

    return {"profile_id": profile_id, "consent": result.to_dict()}


@router.get("/api/voice-profiles/{profile_id}/consent-status")
async def voice_profile_consent_status(
    profile_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the persisted consent record (if any) for a profile.

    Shape:
        {
          "profile_id": "...",
          "required": <settings.voice_consent_required>,
          "consented": <bool>,
          "consent": {ConsentResult fields...} | null
        }
    """
    await _require_owned_profile(profile_id, user)

    record = _load_consent(profile_id)
    return {
        "profile_id": profile_id,
        "required": settings.voice_consent_required,
        "consented": bool(record and record.get("ok")),
        "consent": record,
    }

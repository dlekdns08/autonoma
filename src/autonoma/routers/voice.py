"""/api/voice-profiles + /api/voice-bindings endpoints.

OmniVoice reference-audio profiles + per-VRM voice bindings. Each
profile is a short reference clip (WAV preferred) + its transcript;
the TTS worker reads the binding at speech time and feeds the profile
to the zero-shot synthesizer.

Binding mutations emit ``voice.bindings.updated`` on the shared bus
so every connected viewer re-resolves the character's voice.
"""

from __future__ import annotations

import io
import logging
import wave
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi import status as http_status
from fastapi.responses import Response as FastAPIResponse

from autonoma import voice as voice_service
from autonoma.auth import User, require_active_user
from autonoma.event_bus import bus
from autonoma.mocap import is_known_vrm
from autonoma.voice.store import IntegrityError as _VoiceIntegrityError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])


# ── Validation constants ──────────────────────────────────────────────

MAX_VOICE_REF_BYTES = 4 * 1024 * 1024  # 4 MB — plenty for 30s PCM16 WAV
# OmniVoice is trained on short cloning samples. 30s is an empirical
# ceiling where quality stays consistent; longer refs either slow
# inference or produce prosody drift. 1s floor rejects near-empty uploads.
MIN_VOICE_REF_DURATION_S = 1.0
MAX_VOICE_REF_DURATION_S = 30.0
ALLOWED_VOICE_MIMES = {
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
    "audio/webm",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp3",
}


# ── Helpers ───────────────────────────────────────────────────────────


def _voice_error(
    status_code: int, code: str, message: str, **extra: Any
) -> HTTPException:
    """Structured error response for the /voice UI.

    ``detail`` is a dict so the frontend can branch on ``code`` (stable
    machine key) while falling back to ``message`` (user-facing Korean
    string) for display.
    """
    payload: dict[str, Any] = {"code": code, "message": message}
    payload.update(extra)
    return HTTPException(status_code=status_code, detail=payload)


def _sniff_audio_mime(data: bytes) -> str | None:
    """Identify the audio container from the first bytes.

    Client-declared ``Content-Type`` is trivially forgeable, so we cross-
    check the actual magic bytes. Returns a canonical mime or ``None``
    if unrecognized.
    """
    if len(data) < 12:
        return None
    head = data[:16]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    if head[:4] == b"OggS":
        return "audio/ogg"
    if head[:3] == b"ID3":
        return "audio/mpeg"
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"mp42", b"isom", b"M4B ", b"M4P "):
            return "audio/mp4"
    if head[:4] == b"fLaC":
        return "audio/flac"
    return None


def _infer_duration_from_wav(data: bytes) -> float:
    """Best-effort duration estimate for WAV bytes. Returns 0.0 on any
    parse failure — informational only (displayed in the admin UI)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate) if rate else 0.0
    except Exception:
        return 0.0


# ── /api/voice-profiles ───────────────────────────────────────────────


@router.get("/api/voice-profiles")
async def voice_list_profiles(
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    profiles = await voice_service.list_profile_summaries()
    return {"profiles": [p.to_dict() for p in profiles]}


@router.post("/api/voice-profiles", status_code=http_status.HTTP_201_CREATED)
async def voice_create_profile(
    name: str = Form(...),
    ref_text: str = Form(...),
    ref_audio: UploadFile = File(...),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Upload a reference audio sample + its transcript."""
    name = (name or "").strip()
    ref_text = (ref_text or "").strip()
    if not (1 <= len(name) <= 128):
        raise _voice_error(400, "invalid_name", "이름은 1자 이상 128자 이하여야 합니다.")
    if not (1 <= len(ref_text) <= 2048):
        raise _voice_error(400, "invalid_ref_text", "레퍼런스 대본은 1자 이상 2048자 이하여야 합니다.")

    declared_mime = (ref_audio.content_type or "").lower()
    if declared_mime and declared_mime not in ALLOWED_VOICE_MIMES:
        raise _voice_error(
            400, "unsupported_audio_mime",
            "지원하지 않는 오디오 형식입니다. WAV, MP3, OGG, WebM 중 하나를 올려 주세요.",
        )

    data = await ref_audio.read()
    if not data:
        raise _voice_error(400, "empty_audio", "오디오 파일이 비어 있습니다.")
    if len(data) > MAX_VOICE_REF_BYTES:
        raise _voice_error(
            413, "audio_too_large",
            f"오디오 파일이 너무 큽니다 (최대 {MAX_VOICE_REF_BYTES // (1024 * 1024)} MB).",
        )

    sniffed = _sniff_audio_mime(data)
    if sniffed is None:
        raise _voice_error(
            400, "unrecognized_audio",
            "오디오 포맷을 인식할 수 없습니다. 파일이 손상되었거나 지원하지 않는 컨테이너입니다.",
        )

    duration = _infer_duration_from_wav(data) if sniffed == "audio/wav" else 0.0
    if sniffed == "audio/wav" and duration > 0:
        if duration < MIN_VOICE_REF_DURATION_S:
            raise _voice_error(
                400, "audio_too_short",
                f"레퍼런스 오디오가 너무 짧습니다 (최소 {MIN_VOICE_REF_DURATION_S:.0f}초).",
            )
        if duration > MAX_VOICE_REF_DURATION_S:
            raise _voice_error(
                400, "audio_too_long",
                f"레퍼런스 오디오가 너무 깁니다 (최대 {MAX_VOICE_REF_DURATION_S:.0f}초).",
            )
    summary = await voice_service.create_profile(
        owner_user_id=user.id,
        name=name,
        ref_text=ref_text,
        ref_audio=data,
        ref_audio_mime=sniffed,
        duration_s=duration,
    )
    return {"profile": summary.to_dict()}


@router.get("/api/voice-profiles/{profile_id}/audio")
async def voice_get_profile_audio(
    profile_id: str,
    _user: User = Depends(require_active_user),
) -> FastAPIResponse:
    """Serve the reference audio bytes for in-browser preview."""
    result = await voice_service.get_profile_audio(profile_id)
    if result is None:
        raise _voice_error(404, "profile_not_found", "해당 프로필을 찾을 수 없습니다.")
    data, mime = result
    return FastAPIResponse(content=data, media_type=mime)


@router.delete(
    "/api/voice-profiles/{profile_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def voice_delete_profile(
    profile_id: str,
    user: User = Depends(require_active_user),
) -> FastAPIResponse:
    summary = await voice_service.get_profile_summary(profile_id)
    if summary is None:
        raise _voice_error(404, "profile_not_found", "해당 프로필을 찾을 수 없습니다.")
    if summary.owner_user_id != user.id and user.role != "admin":
        raise _voice_error(403, "not_owner", "이 프로필을 삭제할 권한이 없습니다.")
    if await voice_service.profile_is_bound(profile_id):
        raise _voice_error(
            409, "profile_in_use",
            "이 프로필은 캐릭터에 바인딩되어 있어 삭제할 수 없습니다. 먼저 바인딩을 해제해 주세요.",
        )
    try:
        ok = await voice_service.delete_profile(profile_id)
    except _VoiceIntegrityError:
        raise _voice_error(
            409, "profile_in_use",
            "이 프로필은 캐릭터에 바인딩되어 있어 삭제할 수 없습니다.",
        )
    if not ok:
        raise _voice_error(404, "profile_not_found", "해당 프로필을 찾을 수 없습니다.")
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@router.post("/api/voice-profiles/{profile_id}/test")
async def voice_test_profile(
    profile_id: str,
    payload: dict[str, Any],
    _user: User = Depends(require_active_user),
) -> FastAPIResponse:
    """Synthesize ``text`` with the given profile and return WAV bytes.

    Pure round-trip: the result is streamed back to the browser for a
    quick listen. No events, no worker, no budget — this is explicitly a
    test endpoint for the admin UI.
    """
    text_in = str(payload.get("text") or "").strip()
    if not (1 <= len(text_in) <= 2000):
        raise _voice_error(400, "invalid_text", "문장은 1자 이상 2000자 이하여야 합니다.")
    profile = await voice_service.get_profile(profile_id)
    if profile is None:
        raise _voice_error(404, "profile_not_found", "해당 프로필을 찾을 수 없습니다.")

    try:
        from autonoma.tts_omnivoice import get_shared_client
        from autonoma.tts_synth import classify_synth_error, synthesize_collected
    except ImportError as exc:
        raise _voice_error(
            503, "omnivoice_missing",
            "서버에 OmniVoice 패키지가 설치되지 않았습니다.",
            detail_raw=str(exc),
        )

    client = get_shared_client()
    try:
        result = await synthesize_collected(
            client,
            text=text_in,
            voice=profile.id,
            ref_audio=profile.ref_audio,
            ref_audio_mime=profile.ref_audio_mime,
            ref_text=profile.ref_text,
        )
    except Exception as exc:
        code, message = classify_synth_error(exc)
        logger.exception(
            "[tts] /test failed voice=%s code=%s class=%s",
            profile.id,
            code,
            type(exc).__name__,
        )
        raise _voice_error(503, code, message, detail_raw=str(exc))
    if not result.audio:
        raise _voice_error(
            503, "empty_synthesis",
            "합성 결과가 비어 있습니다. 레퍼런스 오디오/대본을 확인해 주세요.",
        )
    return FastAPIResponse(content=result.audio, media_type="audio/wav")


# ── /api/voice-bindings ───────────────────────────────────────────────


@router.get("/api/voice-bindings")
async def voice_list_bindings(
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    bindings = await voice_service.list_bindings()
    return {"bindings": [b.to_dict() for b in bindings]}


@router.put("/api/voice-bindings")
async def voice_upsert_binding(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    vrm_file = str(payload.get("vrm_file") or "").strip()
    profile_id = str(payload.get("profile_id") or "").strip()

    if not is_known_vrm(vrm_file):
        raise _voice_error(400, "unknown_vrm", "알 수 없는 VRM 파일입니다.")
    profile = await voice_service.get_profile_summary(profile_id)
    if profile is None:
        raise _voice_error(404, "profile_not_found", "해당 프로필을 찾을 수 없습니다.")

    binding = await voice_service.upsert_binding(
        vrm_file=vrm_file, profile_id=profile_id, updated_by=user.id
    )
    await bus.emit(
        "voice.bindings.updated",
        vrm_file=vrm_file,
        profile_id=profile_id,
        removed=False,
    )
    return {"binding": binding.to_dict()}


@router.delete(
    "/api/voice-bindings", status_code=http_status.HTTP_204_NO_CONTENT
)
async def voice_delete_binding(
    vrm_file: str = Query(...),
    _user: User = Depends(require_active_user),
) -> FastAPIResponse:
    if not is_known_vrm(vrm_file):
        raise _voice_error(400, "unknown_vrm", "알 수 없는 VRM 파일입니다.")
    ok = await voice_service.delete_binding(vrm_file=vrm_file)
    if not ok:
        raise _voice_error(404, "binding_not_found", "해당 바인딩을 찾을 수 없습니다.")
    await bus.emit(
        "voice.bindings.updated",
        vrm_file=vrm_file,
        profile_id=None,
        removed=True,
    )
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)

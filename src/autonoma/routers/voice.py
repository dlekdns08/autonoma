"""/api/voice-profiles + /api/voice-bindings endpoints.

OmniVoice reference-audio profiles + per-VRM voice bindings. Each
profile is a short reference clip (WAV preferred) + its transcript;
the TTS worker reads the binding at speech time and feeds the profile
to the zero-shot synthesizer.

Binding mutations emit ``voice.bindings.updated`` on the shared bus
so every connected viewer re-resolves the character's voice.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import wave
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi import status as http_status
from fastapi.responses import Response as FastAPIResponse

from autonoma import voice as voice_service
from autonoma.auth import (
    SESSION_COOKIE_NAME,
    User,
    get_user_by_id,
    read_session_token,
    require_active_user,
)
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
        logger.warning(
            "WAV duration parse failed (%d bytes); returning 0.0",
            len(data), exc_info=True,
        )
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


# ── Phase 2-#4 — ASR transcribe + voice command ──────────────────────
#
# The browser captures push-to-talk audio with MediaRecorder, posts it
# here as multipart audio, and we run it through the ASR provider
# (CohereLabs/cohere-transcribe-03-2026 by default — see
# ``src/autonoma/voice/asr.py``). Two endpoints:
#
#   POST /api/voice/transcribe — pure STT, returns the text.
#   POST /api/voice/command    — STT + inject through the
#                                 ``ExternalInputRouter`` so the swarm
#                                 receives the utterance as a directed
#                                 message. ``target`` is optional; when
#                                 omitted the Director picks it up.

# Soft cap on uploaded audio size to keep a malicious client from OOMing
# the ASR worker. Raw 16 kHz mono PCM16 ≈ 32 kB/s, so 8 MB ≈ 4 minutes —
# more than enough for a push-to-talk command.
MAX_ASR_AUDIO_BYTES = 8 * 1024 * 1024


def _asr_disabled_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "asr_disabled", "message": "ASR 비활성화됨"},
    )


@router.post("/api/voice/transcribe")
async def voice_transcribe(
    audio: UploadFile = File(...),
    language: str = Form(default=""),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Run ASR over an uploaded audio blob and return the transcribed text.

    ``language`` is forwarded to the processor as a hint. When empty we
    fall back to ``settings.voice_asr_default_language``.
    """
    from autonoma.config import settings as _settings
    from autonoma.voice.asr import get_asr_provider

    if getattr(_settings, "voice_asr_provider", "cohere") == "none":
        raise _asr_disabled_error()

    raw = await audio.read()
    if not raw:
        raise HTTPException(
            status_code=400,
            detail={"code": "empty_audio", "message": "오디오가 비어 있습니다."},
        )
    if len(raw) > MAX_ASR_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "audio_too_large",
                "message": f"오디오 크기가 한도({MAX_ASR_AUDIO_BYTES} 바이트)를 넘었습니다.",
            },
        )

    lang = (language or _settings.voice_asr_default_language or "").strip()
    provider = get_asr_provider()
    from autonoma.voice import metrics as _asr_metrics

    try:
        # Heavy CPU/GPU work — push to a worker thread so the event loop
        # keeps serving other requests while the model decodes.
        import anyio

        result = await anyio.to_thread.run_sync(
            lambda: provider.transcribe(raw, language=lang)
        )
    except RuntimeError as exc:
        # Most likely the ASR extras aren't installed.
        logger.error(f"[voice] transcribe failed: {exc}")
        _asr_metrics.record_transcribe(stage="batch", ok=False, duration_ms=0, error=str(exc))
        raise HTTPException(
            status_code=503,
            detail={
                "code": "asr_unavailable",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        logger.exception(f"[voice] transcribe crashed: {exc}")
        _asr_metrics.record_transcribe(stage="batch", ok=False, duration_ms=0, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail={"code": "asr_error", "message": str(exc)},
        ) from exc

    _asr_metrics.record_transcribe(stage="batch", ok=True, duration_ms=result.duration_ms)

    # Audit log — best-effort, swallows DB errors so a transient outage
    # doesn't break a transcribe response.
    from autonoma.voice import transcripts_store as _ts

    await _ts.record(
        user_id=str(user.id),
        text=result.text,
        stage="batch",
        language=result.language or lang,
        duration_ms=result.duration_ms,
        model=result.model,
    )

    logger.info(
        f"[voice] transcribed user={user.id} bytes={len(raw)} "
        f"text={result.text[:60]!r} ms={result.duration_ms}"
    )
    return {
        "text": result.text,
        "language": result.language,
        "duration_ms": result.duration_ms,
        "model": result.model,
    }


@router.post("/api/voice/command")
async def voice_command(
    audio: UploadFile = File(...),
    target: str = Form(default=""),
    language: str = Form(default=""),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Transcribe → inject through the ExternalInputRouter.

    Returns both the transcript and the routing result so the UI can
    show "✓ delivered to Alice" toasts.
    """
    from autonoma.external_input import ExternalMessage, router as ext_router

    transcript = await voice_transcribe(audio=audio, language=language, user=user)
    text = (transcript.get("text") or "").strip()
    if not text:
        return {
            "transcript": transcript,
            "route": {"action": "dropped_invalid", "detail": "empty transcript"},
        }

    msg = ExternalMessage(
        source="voice",
        user=str(user.id),
        text=text,
        target=(target.strip() or None),
        metadata={"language": transcript.get("language") or ""},
    )
    result = await ext_router.submit(msg)
    return {
        "transcript": transcript,
        "route": {"action": result.action.value, "detail": result.detail},
    }


# ── Streaming ASR over WebSocket ─────────────────────────────────────
#
# Cohere transcribe-03-2026 is encoder-decoder + ``model.generate`` so
# we can't stream tokens. Instead we do "rolling chunk transcription":
# the browser streams audio chunks (MediaRecorder timeslice=500ms) over
# this WS, and a background task transcribes the *accumulated* buffer
# every ``PARTIAL_INTERVAL_S`` seconds, pushing a ``partial`` event
# back to the client. On ``stop`` we run one last transcription over
# the full audio and route the final text through ``ExternalInputRouter``
# — same destination as ``POST /api/voice/command``.
#
# Why re-transcribe the full buffer each tick rather than the tail?
# Cohere ASR's quality on short isolated clips is poor (it hallucinates
# fillers and clips off the start of words). Feeding the cumulative
# buffer keeps the model's context anchored so the partial converges
# toward the same answer the final pass will produce.

# Browser timeslice is 500ms so even chatty captures land well below
# this. The cap is anti-OOM, not a feature limit.
MAX_STREAM_AUDIO_BYTES = 16 * 1024 * 1024
# How often the partial transcribe loop fires. Cohere on MPS takes
# 0.5–1.0s per pass over a few seconds of audio, so 1.5s gives the
# previous pass time to finish before the next tick.
PARTIAL_INTERVAL_S = 1.5
# Hard cap on WS lifetime — push-to-talk should be seconds, not minutes.
MAX_STREAM_DURATION_S = 120.0


@router.websocket("/api/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    """Streaming ASR for push-to-talk.

    Protocol (text frames are JSON, audio frames are binary):

      C → S  ``{"type":"start","language":"ko","target":"alice"}``
      S → C  ``{"type":"ready"}``  *or*  ``{"type":"error",...}``
      C → S  binary WebM/Opus chunks (cumulative — server appends)
      S → C  ``{"type":"partial","text":"..."}`` (every ~1.5s while audio)
      C → S  ``{"type":"stop"}``  *or*  socket close
      S → C  ``{"type":"final","text":"...","route":{...}}``

    Auth: same cookie-based session as HTTP. We accept the WS first so
    we can return a structured error frame (browsers can't see custom
    close codes reliably), then hard-close.
    """
    await ws.accept()

    # ── Cookie-based session auth ─────────────────────────────────
    cookie_token = ws.cookies.get(SESSION_COOKIE_NAME)
    user_id = read_session_token(cookie_token or "")
    user = await get_user_by_id(user_id) if user_id else None
    if user is None or user.status != "active":
        try:
            await ws.send_json(
                {"type": "error", "code": "unauthorized", "message": "로그인이 필요합니다."}
            )
        finally:
            await ws.close(code=4401)
        return

    # ── ASR provider readiness ────────────────────────────────────
    from autonoma.config import settings as _settings
    from autonoma.voice.asr import get_asr_provider

    if getattr(_settings, "voice_asr_provider", "cohere") == "none":
        try:
            await ws.send_json(
                {"type": "error", "code": "asr_disabled", "message": "ASR 비활성화됨"}
            )
        finally:
            await ws.close(code=4400)
        return

    # ── Wait for the start frame (language + target) ──────────────
    try:
        first_text = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        await ws.close(code=4400)
        return
    try:
        first = json.loads(first_text)
    except json.JSONDecodeError:
        first = None
    if not isinstance(first, dict) or first.get("type") != "start":
        try:
            await ws.send_json(
                {"type": "error", "code": "protocol", "message": "start frame required"}
            )
        finally:
            await ws.close(code=4400)
        return

    language = (
        str(first.get("language") or "").strip()
        or _settings.voice_asr_default_language
        or "en"
    )
    target_raw = str(first.get("target") or "").strip()
    target = target_raw or None
    # ``route`` defaults true for backward compat. The /voice studio page
    # sets it false because it only wants the transcript — feeding the
    # text into ExternalInputRouter from a non-running swarm would just
    # produce noisy ``dropped_invalid`` log lines.
    route_enabled = bool(first.get("route", True))

    await ws.send_json({"type": "ready"})

    provider = get_asr_provider()
    buffer = bytearray()
    transcribe_busy = False
    last_partial_text = ""
    closed = False

    async def _transcribe_snapshot(snapshot: bytes) -> Any:
        # Cohere is sync + thread-unsafe internally; the provider's own
        # lock serialises, so a background ``run_sync`` keeps the event
        # loop responsive without us reaching into provider internals.
        import anyio

        return await anyio.to_thread.run_sync(
            lambda: provider.transcribe(snapshot, language=language)
        )

    from autonoma.voice import metrics as _asr_metrics

    async def _maybe_partial() -> None:
        nonlocal transcribe_busy, last_partial_text, closed
        if closed or transcribe_busy or not buffer:
            return
        transcribe_busy = True
        snapshot = bytes(buffer)
        try:
            result = await _transcribe_snapshot(snapshot)
            _asr_metrics.record_transcribe(
                stage="partial", ok=True, duration_ms=result.duration_ms
            )
            text = (result.text or "").strip()
            if text and text != last_partial_text and not closed:
                last_partial_text = text
                await ws.send_json({"type": "partial", "text": text})
        except Exception as exc:
            logger.warning(f"[voice/stream] partial transcribe failed: {exc}")
            _asr_metrics.record_transcribe(
                stage="partial", ok=False, duration_ms=0, error=str(exc)
            )
        finally:
            transcribe_busy = False

    async def _partial_loop() -> None:
        try:
            while not closed:
                await asyncio.sleep(PARTIAL_INTERVAL_S)
                await _maybe_partial()
        except asyncio.CancelledError:
            pass

    pl_task = asyncio.create_task(_partial_loop())
    deadline = asyncio.get_event_loop().time() + MAX_STREAM_DURATION_S

    final_result: Any = None
    final_text = ""
    error_payload: dict[str, Any] | None = None

    try:
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                error_payload = {
                    "type": "error",
                    "code": "stream_timeout",
                    "message": f"녹음 한도({int(MAX_STREAM_DURATION_S)}s)를 초과했습니다.",
                }
                break
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
            except asyncio.TimeoutError:
                error_payload = {
                    "type": "error",
                    "code": "stream_timeout",
                    "message": "녹음 한도를 초과했습니다.",
                }
                break

            mtype = msg.get("type")
            if mtype == "websocket.disconnect":
                # Treat as implicit stop — caller dropped the socket.
                break
            payload_bytes = msg.get("bytes")
            payload_text = msg.get("text")
            if payload_bytes:
                if len(buffer) + len(payload_bytes) > MAX_STREAM_AUDIO_BYTES:
                    error_payload = {
                        "type": "error",
                        "code": "audio_too_large",
                        "message": (
                            f"오디오 크기가 한도({MAX_STREAM_AUDIO_BYTES} 바이트)를 넘었습니다."
                        ),
                    }
                    break
                buffer.extend(payload_bytes)
            elif payload_text:
                try:
                    frame = json.loads(payload_text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict):
                    continue
                ftype = frame.get("type")
                if ftype == "stop":
                    break
                if ftype == "interrupt":
                    # Barge-in (feature #2). Drop pending TTS jobs
                    # site-wide so the agent backlog doesn't replay
                    # over the user's voice. The current chunk in
                    # flight finishes — see ``cancel_all`` docstring.
                    await bus.emit("tts.cancel", reason="user_barge_in")
                    continue
                # Unknown text frames are ignored (forward-compat).
    except WebSocketDisconnect:
        pass
    finally:
        closed = True
        pl_task.cancel()
        try:
            await pl_task
        except (asyncio.CancelledError, Exception):
            pass

    if error_payload is not None:
        try:
            await ws.send_json(error_payload)
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
        return

    # ── Final pass: transcribe full buffer, route through ext input ─
    if not buffer:
        try:
            await ws.send_json(
                {
                    "type": "final",
                    "text": "",
                    "language": language,
                    "duration_ms": 0,
                    "model": "",
                    "route": {"action": "dropped_invalid", "detail": "empty audio"},
                }
            )
        finally:
            await ws.close()
        return

    try:
        final_result = await _transcribe_snapshot(bytes(buffer))
        final_text = (final_result.text or "").strip()
        _asr_metrics.record_transcribe(
            stage="final", ok=True, duration_ms=final_result.duration_ms
        )
    except Exception as exc:
        logger.exception(f"[voice/stream] final transcribe failed: {exc}")
        _asr_metrics.record_transcribe(
            stage="final", ok=False, duration_ms=0, error=str(exc)
        )
        try:
            await ws.send_json(
                {"type": "error", "code": "asr_error", "message": str(exc)}
            )
        finally:
            await ws.close()
        return

    route_payload: dict[str, Any] = {
        "action": "dropped_invalid",
        "detail": "empty transcript",
    }
    if final_text and route_enabled:
        from autonoma.external_input import ExternalMessage, router as ext_router

        ext_msg = ExternalMessage(
            source="voice",
            user=str(user.id),
            text=final_text,
            target=target,
            metadata={"language": language},
        )
        ext_result = await ext_router.submit(ext_msg)
        route_payload = {"action": ext_result.action.value, "detail": ext_result.detail}
    elif final_text and not route_enabled:
        # Studio page (/voice) — caller wants the transcript only.
        route_payload = {"action": "skipped", "detail": "route=false"}

    logger.info(
        f"[voice/stream] user={user.id} bytes={len(buffer)} "
        f"final={final_text[:60]!r} ms={getattr(final_result, 'duration_ms', 0)}"
    )

    # Audit log for the streaming final pass — same best-effort policy.
    from autonoma.voice import transcripts_store as _ts

    await _ts.record(
        user_id=str(user.id),
        text=final_text,
        stage="final",
        language=getattr(final_result, "language", "") or language,
        duration_ms=getattr(final_result, "duration_ms", 0),
        model=getattr(final_result, "model", ""),
        route_action=route_payload.get("action") or "",
        route_target=target,
    )

    try:
        await ws.send_json(
            {
                "type": "final",
                "text": final_text,
                "language": language,
                "duration_ms": getattr(final_result, "duration_ms", 0),
                "model": getattr(final_result, "model", ""),
                "route": route_payload,
            }
        )
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ── Voice metrics — feature #8 ───────────────────────────────────────


@router.get("/api/voice/metrics")
async def voice_metrics(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return a snapshot of in-process ASR counters.

    Cookie-gated to logged-in users — these aren't admin-only because
    operators routinely want to glance at "is the mic working" from a
    user account, but unauthenticated scraping isn't useful and gives
    away that the model is loaded.
    """
    from autonoma.voice import metrics as _asr_metrics

    snap = _asr_metrics.snapshot()
    # Operator hint surfaced inline so the dashboard can show a banner
    # when the provider is disabled (otherwise an empty metrics page is
    # confusing).
    from autonoma.config import settings as _settings

    snap["provider"] = getattr(_settings, "voice_asr_provider", "cohere")
    snap["model"] = getattr(_settings, "voice_asr_model", "")
    return snap


# ── Voice transcripts — feature #1 ───────────────────────────────────


@router.get("/api/voice/transcripts")
async def list_voice_transcripts(
    limit: int = Query(default=50, ge=1, le=500),
    session_id: int | None = Query(default=None),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """List the caller's recent transcripts, newest first.

    Non-admins are scoped to their own rows. ``session_id`` further
    narrows to a specific swarm run when set.
    """
    from autonoma.voice import transcripts_store as _ts

    rows = await _ts.list_recent(
        user_id=str(user.id),
        session_id=session_id,
        limit=limit,
    )
    return {
        "transcripts": [
            {
                "id": r.id,
                "session_id": r.session_id,
                "stage": r.stage,
                "text": r.text,
                "language": r.language,
                "duration_ms": r.duration_ms,
                "model": r.model,
                "route_action": r.route_action,
                "route_target": r.route_target,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }

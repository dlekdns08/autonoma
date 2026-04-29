"""Daily standup podcast generator — feature #10.

Takes a conversation transcript (from any recent session or a passed-in
script) and renders it as a concatenated WAV using each speaker's voice
profile. The caller (internal scheduler or the admin UI) requests a
standup; we write the audio under ``settings.standup_output_dir`` and
return the path + transcript.

This is the "async AI team podcast" — operators can auto-generate one
every morning via cron (``/api/standup/generate`` is a normal admin
endpoint) and stream from the static dir.
"""

from __future__ import annotations

import datetime as _dt
import io
import logging
import wave
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from autonoma.auth import User, require_active_user
from autonoma.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["standup"])


async def _synthesize_line(agent: str, text: str, profile_id: str | None) -> bytes:
    """Return WAV bytes for a single line. Falls back to 0.5s of silence
    when TTS is unavailable so the resulting file is still playable."""
    # Standup synthesis follows the same factory path as the swarm
    # worker and the podcast orchestrator — flipping
    # ``settings.tts_provider`` swaps the backend everywhere. The old
    # hard-coded ``tts_omnivoice`` import broke the moment the
    # operator switched to vibevoice (omnivoice extra dropped in the
    # same change).
    if not profile_id or settings.tts_provider not in ("omnivoice", "vibevoice"):
        return _silence_wav_bytes(500)
    try:
        from autonoma import voice as voice_service
        from autonoma.tts import create_tts_client, tts_config_from_settings
        from autonoma.tts_synth import synthesize_collected
    except ImportError:
        return _silence_wav_bytes(500)

    profile = await voice_service.get_profile(profile_id)
    if profile is None:
        return _silence_wav_bytes(500)
    client = create_tts_client(tts_config_from_settings())
    try:
        result = await synthesize_collected(
            client,
            text=text,
            voice=profile.id,
            ref_audio=profile.ref_audio,
            ref_audio_mime=profile.ref_audio_mime,
            ref_text=profile.ref_text,
        )
    except Exception as exc:  # pragma: no cover — depends on model
        logger.warning("[standup] %s synthesis failed: %s", agent, exc)
        return _silence_wav_bytes(500)
    return result.audio or _silence_wav_bytes(500)


def _silence_wav_bytes(ms: int, sample_rate: int = 24000) -> bytes:
    """Emit a silent mono PCM16 WAV of ``ms`` milliseconds."""
    frames = int(sample_rate * (ms / 1000.0))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def _concatenate_wavs(parts: list[bytes], pause_ms: int = 300) -> bytes:
    """Sum WAV parts + short silence between speakers. Assumes 24 kHz
    mono PCM16 throughout (what OmniVoice emits), which also matches
    ``_silence_wav_bytes``."""
    if not parts:
        return b""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        gap = _silence_wav_bytes(pause_ms)
        for i, wav_bytes in enumerate(parts):
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                out.writeframes(wf.readframes(wf.getnframes()))
            if i != len(parts) - 1:
                with wave.open(io.BytesIO(gap), "rb") as wf:
                    out.writeframes(wf.readframes(wf.getnframes()))
    return buf.getvalue()


@router.post("/api/standup/generate")
async def generate_standup(
    payload: dict[str, Any],
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Render a scripted standup to WAV + transcript.

    Shape::

        {
          "title": "2026-04-22 daily",
          "lines": [
            {"agent": "Alice", "voice_profile_id": "abc", "text": "좋은 아침!"},
            {"agent": "Bear",  "voice_profile_id": "def", "text": "어제 리뷰 다 끝냈어."},
            ...
          ]
        }

    Returns the relative path under ``standup_output_dir`` and the
    concatenated transcript. The file is persisted on disk, not
    streamed — standup players are expected to fetch the static path.
    """
    if not settings.standup_enabled:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "standup_disabled", "message": "Standup is disabled. Set AUTONOMA_STANDUP_ENABLED=true."},
        )
    lines = payload.get("lines") or []
    if not isinstance(lines, list) or not lines:
        raise HTTPException(400, detail={"code": "empty_script", "message": "lines 배열이 필요합니다."})

    title = str(payload.get("title") or _dt.datetime.now().strftime("%Y-%m-%d standup"))
    parts: list[bytes] = []
    transcript_lines: list[str] = [f"# {title}", ""]
    for i, line in enumerate(lines):
        if not isinstance(line, dict):
            raise HTTPException(400, detail={"code": "bad_line", "message": f"line #{i} is not an object"})
        agent = str(line.get("agent") or "Speaker")
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        profile_id = line.get("voice_profile_id")
        wav = await _synthesize_line(agent, text, profile_id)
        parts.append(wav)
        transcript_lines.append(f"**{agent}**: {text}")

    combined = _concatenate_wavs(parts)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(settings.standup_output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"standup-{stamp}.wav"
    txt_path = out_dir / f"standup-{stamp}.md"
    wav_path.write_bytes(combined)
    txt_path.write_text("\n".join(transcript_lines), encoding="utf-8")
    return {
        "title": title,
        "audio_path": str(wav_path),
        "transcript_path": str(txt_path),
        "lines": len(parts),
        "bytes": len(combined),
    }

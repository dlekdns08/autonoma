"""Sign language avatar scaffold — feature #12.

The full feature needs a text → pose-sequence ML model (e.g.
``signlanguagetranslator/SignBERT`` or a fine-tuned KSL model). That
lives outside this PoC. What we ship here is:

  * A registry of **named clips** — operator-uploaded short pose
    sequences (``{timestamp_ms, bone, rotation_quat_xyzw}`` records).
  * ``POST /api/sign/play`` — trigger a clip playback event that the
    VRM stage already knows how to consume (``mocap.play_clip``).
  * ``GET /api/sign/clips`` — list clips.

Once a real text→sign model is wired, a new endpoint can ingest
arbitrary strings and emit the same ``sign.clip`` events; the VRM
stage is already fed off the same channel.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from autonoma.auth import User, require_active_user
from autonoma.config import settings
from autonoma.event_bus import bus

router = APIRouter(tags=["sign"])


def _clips_dir() -> Path:
    path = Path(settings.data_dir) / "sign_clips"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/api/sign/clips")
async def list_clips(
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Operator-uploaded clips as (name, frames, durationMs)."""
    clips: list[dict[str, Any]] = []
    for fp in sorted(_clips_dir().glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        frames = data.get("frames") or []
        clips.append({
            "name": fp.stem,
            "frames": len(frames),
            "duration_ms": int(data.get("duration_ms") or 0),
            "language": data.get("language", "ksl"),
        })
    return {"clips": clips}


@router.post("/api/sign/play")
async def play_clip(
    payload: dict[str, Any],
    _user: User = Depends(require_active_user),
) -> dict[str, str]:
    name = str(payload.get("clip") or "").strip()
    if not name or "/" in name or ".." in name:
        raise HTTPException(400, detail={"code": "invalid_clip_name"})
    fp = _clips_dir() / f"{name}.json"
    if not fp.exists():
        raise HTTPException(404, detail={"code": "clip_not_found", "message": f"클립 {name} 을 찾을 수 없습니다."})
    try:
        clip_data = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "corrupt_clip"},
        )
    target_vrm = str(payload.get("vrm_file") or "")
    await bus.emit(
        "sign.clip",
        name=name,
        vrm_file=target_vrm,
        duration_ms=int(clip_data.get("duration_ms") or 0),
        frames=clip_data.get("frames") or [],
        language=clip_data.get("language", "ksl"),
    )
    return {"status": "ok"}

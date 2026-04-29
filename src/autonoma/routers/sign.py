"""Sign language avatar — feature #12.

A free, public text→KSL ML model does not exist today, so instead of
waiting we ship a **phrase-book translator**. Operator-authored clips
live under ``{data_dir}/sign_clips/`` and are indexed by the tokens
they cover. At translation time:

  1. The input sentence is tokenized (word-level).
  2. A greedy match walks left-to-right, longest phrase first, picking
     any multi-word clip that covers the prefix.
  3. Unknown tokens fall back to **fingerspelling**: each character
     plays the ``ksl_letter:<char>`` clip if one exists, skipped
     silently otherwise.
  4. The server concatenates the plans into a single ``sign.sequence``
     event so the VRM stage plays them back-to-back without gap.

Clip JSON schema (stored as ``sign_clips/<name>.json``)::

    {
      "name": "hello",
      "language": "ksl",
      "tokens": ["hello", "안녕", "안녕하세요"],
      "duration_ms": 1200,
      "frames": [ { "t_ms": 0, "bones": [...] }, ... ]
    }

``tokens`` is the list of input surface forms the clip covers. A
dictionary loaded at startup maps every token to its clip name, which
keeps greedy matching to O(sentence-length).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from autonoma.auth import User, require_active_user
from autonoma.config import settings
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

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


# ── text → KSL translator ────────────────────────────────────────────

# Tokenizer splits on whitespace + punctuation but keeps unicode letters
# (hangul syllables, jamo, latin) together as one token. Fingerspelling
# then walks token character-by-character.
_TOKEN_SPLIT = re.compile(r"[^\w가-힯ᄀ-ᇿ㄰-㆏]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(text.strip()) if t]


def _load_dictionary() -> dict[str, str]:
    """Scan clip dir, return ``{token_lowercase: clip_name}``.

    Cheap to re-run on every request for the PoC size (dozens of clips).
    Promote to a cached, file-mtime-guarded loader if the clip library
    ever grows to hundreds.
    """
    dictionary: dict[str, str] = {}
    for fp in _clips_dir().glob("*.json"):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        tokens = data.get("tokens") or []
        if not isinstance(tokens, list):
            continue
        for tok in tokens:
            if isinstance(tok, str) and tok.strip():
                dictionary.setdefault(tok.strip().lower(), fp.stem)
    return dictionary


def _greedy_plan(tokens: list[str], dictionary: dict[str, str]) -> list[dict[str, Any]]:
    """Longest-match word/phrase plan.

    Returns a list of steps ``{kind, clip, surface}`` where:
      * ``kind="phrase"``: a multi-word dictionary hit
      * ``kind="word"``:   a single-word dictionary hit
      * ``kind="fingerspell"``: fell through — each char references
        a ``ksl_letter:<char>`` clip that the VRM stage is expected
        to have. Unknown chars are emitted anyway so the frontend
        can skip or render a placeholder.
    """
    plan: list[dict[str, Any]] = []
    i = 0
    n = len(tokens)
    # Try up to 4-gram matches — plenty for Korean everyday phrases.
    MAX_NGRAM = 4
    while i < n:
        matched = False
        for span in range(min(MAX_NGRAM, n - i), 0, -1):
            surface = " ".join(tokens[i:i + span])
            clip = dictionary.get(surface.lower())
            if clip:
                plan.append({
                    "kind": "phrase" if span > 1 else "word",
                    "clip": clip,
                    "surface": surface,
                })
                i += span
                matched = True
                break
        if matched:
            continue
        # Fingerspell this single token character-by-character.
        word = tokens[i]
        for ch in word:
            clip = dictionary.get(f"ksl_letter:{ch}".lower())
            plan.append({
                "kind": "fingerspell",
                "clip": clip or "",  # empty = frontend skips / shows placeholder
                "surface": ch,
            })
        i += 1
    return plan


@router.post("/api/sign/translate")
async def translate_text(
    payload: dict[str, Any],
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Translate a free-form text input into a KSL clip sequence.

    Request::

        {"text": "안녕하세요 오늘 어떠세요?", "vrm_file": "midori.vrm"}

    Response::

        {
          "tokens": ["안녕하세요", "오늘", "어떠세요"],
          "plan": [
            {"kind": "word", "clip": "hello", "surface": "안녕하세요"},
            ...
          ],
          "missing": ["어떠세요"],          # tokens that had to be fingerspelled
          "coverage": 0.67                  # fraction of tokens covered by phrase/word clips
        }

    When ``emit`` is true in the payload, also broadcasts the plan as a
    ``sign.sequence`` event so the VRM stage plays it.
    """
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, detail={"code": "empty_text", "message": "text가 비어 있습니다."})
    if len(text) > 2000:
        raise HTTPException(400, detail={"code": "text_too_long", "message": "text는 2000자 이하여야 합니다."})

    dictionary = _load_dictionary()
    tokens = _tokenize(text)
    plan = _greedy_plan(tokens, dictionary)
    covered = sum(1 for step in plan if step["kind"] in ("word", "phrase"))
    total_tokens_in_plan = max(1, covered + sum(1 for step in plan if step["kind"] == "fingerspell"))
    missing = [step["surface"] for step in plan if step["kind"] == "fingerspell" and not step["clip"]]

    emit = bool(payload.get("emit"))
    if emit:
        await bus.emit(
            "sign.sequence",
            text=text,
            vrm_file=str(payload.get("vrm_file") or ""),
            plan=plan,
        )

    return {
        "tokens": tokens,
        "plan": plan,
        "missing": missing,
        "coverage": round(covered / total_tokens_in_plan, 3),
    }


@router.post("/api/sign/clips/upload", status_code=http_status.HTTP_201_CREATED)
async def upload_clip(
    payload: dict[str, Any],
    _user: User = Depends(require_active_user),
) -> dict[str, str]:
    """Operator uploads a pose clip JSON directly (no ML pipeline yet).

    Shape matches the on-disk format — this endpoint validates the
    required fields, normalizes tokens, and writes the file. Safe for
    admins + translators to call; no FS escape (sanitized name).
    """
    name = str(payload.get("name") or "").strip()
    # Tightened from ``[A-Za-z0-9_\-:.]{1,64}``: the ``.`` and ``:``
    # characters were never used by the legitimate UI and ``:`` is a
    # filesystem ADS marker on Windows, so dropping them removes a
    # cross-platform footgun without breaking any saved clips.
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", name):
        raise HTTPException(
            400,
            detail={
                "code": "invalid_clip_name",
                "message": "영숫자/_/- 1-64자만 허용됩니다.",
            },
        )
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        raise HTTPException(400, detail={"code": "invalid_frames", "message": "frames는 비어있지 않은 배열이어야 합니다."})
    tokens_raw = payload.get("tokens") or [name]
    tokens = [str(t).strip() for t in tokens_raw if isinstance(t, str) and str(t).strip()]
    clip = {
        "name": name,
        "language": str(payload.get("language") or "ksl"),
        "tokens": tokens,
        "duration_ms": int(payload.get("duration_ms") or 0),
        "frames": frames,
    }
    out = _clips_dir() / f"{name}.json"
    out.write_text(json.dumps(clip, ensure_ascii=False), encoding="utf-8")
    return {"status": "ok", "name": name}

"""Podcast / multi-character dialogue — feature "Wave C".

Two characters (host + guest) hold a scripted-but-LLM-generated
conversation about a topic. Each turn is synthesised through OmniVoice
with the speaker's own voice profile (pre-uploaded via /voice). Live
listeners can interrupt with a chat message or a voice utterance —
that input is folded into the next dialogue chunk so the conversation
can react to the room.

Why a separate router instead of building on the existing swarm?
  * Swarms are project-driven; this is dialogue-driven, with no
    tasks, files, or grading. Reusing AgentSwarm here would mean
    bending every prompt template the swarm holds.
  * Listener interaction is a much tighter loop than the swarm's
    round-based feedback queue, so the orchestrator runs as its
    own asyncio task with explicit interrupt handling.

Persistence: in-memory only for now (sessions live until the API
process restarts or the owner stops them). Adding a DB table is a
straight-forward follow-up if operators want resumable sessions.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field

from autonoma.auth import User, require_active_user
from autonoma.event_bus import bus
from autonoma.config import settings
from autonoma.tts import create_tts_client, tts_config_from_settings
from autonoma.tts_synth import synthesize_streaming
from autonoma.voice.store import get_profile

logger = logging.getLogger(__name__)

router = APIRouter(tags=["podcast"])


# ── Request / response models ────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    host_name: str = Field(..., min_length=1, max_length=64)
    guest_name: str = Field(..., min_length=1, max_length=64)
    host_voice_profile_id: str = Field(..., min_length=1, max_length=64)
    guest_voice_profile_id: str = Field(..., min_length=1, max_length=64)
    topic: str = Field(..., min_length=1, max_length=400)
    host_persona: str = Field("", max_length=400)
    guest_persona: str = Field("", max_length=400)
    # Total dialogue turns to script per LLM call. We re-call after
    # exhausting the chunk so the conversation can react to interrupts.
    chunk_size: int = Field(4, ge=2, le=8)
    # Hard cap so a runaway session can't hold the LLM hostage.
    max_total_turns: int = Field(20, ge=2, le=80)
    language: str = Field("ko", min_length=2, max_length=8)


class InterruptRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=400)


# ── Session state (in-memory) ─────────────────────────────────────────


@dataclass
class _SessionState:
    """Mutable state for one podcast session.

    Lives in ``_sessions`` keyed by id; cleared on stop/delete.
    """

    id: str
    owner_user_id: str
    spec: CreateSessionRequest
    status: str = "idle"  # idle / running / paused / ended / error
    # Rolling history of turns the LLM has *already produced* — both
    # spoken and pending. Used as conversational context on the next
    # LLM call.
    history: list[dict[str, str]] = field(default_factory=list)
    turns_played: int = 0
    # Pending listener interrupt (raw text). Consumed at the start
    # of the next LLM call, then cleared.
    pending_user_input: str | None = None
    # Background orchestrator task. None when not running.
    task: asyncio.Task[Any] | None = None
    # Set when the user (or system) wants the current loop to
    # break out of its TTS playback and re-enter the LLM stage —
    # typically because new listener input arrived.
    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None
    created_at: float = field(default_factory=time.time)


_sessions: dict[str, _SessionState] = {}
_sessions_lock = asyncio.Lock()


# ── Module-level resources ───────────────────────────────────────────


# Single shared TTS client for podcast synthesis. Same model the swarm
# uses; cheap to share across sessions because OmniVoice itself
# serialises generate() internally.
_tts_client = None


def _get_tts_client() -> Any:
    global _tts_client
    if _tts_client is None:
        _tts_client = create_tts_client(tts_config_from_settings())
    return _tts_client


# ── LLM dialogue scripting ───────────────────────────────────────────


_TURNS_RE = re.compile(r"\{[\s\S]*\}")


def _build_admin_llm_client() -> Any:
    """Reuse the same admin LLM credential the swarm boots with.

    We do this here instead of carrying a per-session config because
    the podcast feature is intended for the operator's own demo
    sessions, not multi-tenant. If a tenant model becomes a need, the
    config can move into ``CreateSessionRequest`` (along with rate
    limits per owner_user_id).
    """
    from autonoma.api import _build_admin_llm_config  # local import — circular at module load

    cfg = _build_admin_llm_config()
    if cfg is None:
        return None
    from autonoma.llm import create_llm_client

    return create_llm_client(cfg)


async def _generate_chunk(state: _SessionState) -> list[dict[str, str]]:
    """Ask the LLM to produce the next ``chunk_size`` dialogue turns.

    Returns a list of ``{"speaker": "host"|"guest", "text": str}`` dicts.
    Empty list on failure — caller decides whether to retry.
    """
    spec = state.spec
    client = _build_admin_llm_client()
    if client is None:
        state.error = "admin LLM not configured"
        return []

    history_str = (
        "\n".join(
            f"  {t['speaker'].upper()} ({spec.host_name if t['speaker']=='host' else spec.guest_name}): {t['text']}"
            for t in state.history[-12:]
        )
        or "  (start of conversation)"
    )

    listener_block = ""
    if state.pending_user_input:
        # The listener input is consumed here — we add it to history
        # afterwards so subsequent turns can refer to it without re-
        # injecting via the system prompt.
        listener_block = (
            f"\nA live listener just commented: \"{state.pending_user_input}\"\n"
            "The next turns should naturally acknowledge or react to this input — "
            "do NOT ignore it.\n"
        )

    system = (
        "You are scripting a relaxed two-person podcast dialogue. "
        "Keep each turn 1–3 sentences, conversational, and in the same "
        "language the topic is given in. Output STRICT JSON only — "
        "no markdown fences, no preface."
    )
    user_msg = f"""HOST: {spec.host_name} — {spec.host_persona or 'A curious, warm host who asks great questions.'}
GUEST: {spec.guest_name} — {spec.guest_persona or 'A thoughtful guest with strong opinions and stories.'}

TOPIC: {spec.topic}

CONVERSATION SO FAR:
{history_str}
{listener_block}
Generate the next {spec.chunk_size} dialogue turns. Alternate speakers
naturally — they don't have to strictly take turns if the conversation
demands a follow-up from the same speaker. Output ONLY this JSON:

{{"turns": [{{"speaker": "host", "text": "..."}}, {{"speaker": "guest", "text": "..."}}, ...]}}"""

    try:
        resp = await client.create(
            model=settings.model,
            max_tokens=1024,
            temperature=0.8,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception as exc:
        logger.exception("[podcast] LLM call failed")
        state.error = f"llm: {exc}"
        return []

    raw = (resp.text or "").strip()
    # Strip markdown fences if the model ignored the "no fences" note.
    if raw.startswith("```"):
        raw = raw.strip("`")
        # Drop the optional ``json`` language hint on the first line.
        nl = raw.find("\n")
        if nl != -1 and raw[:nl].strip().lower() == "json":
            raw = raw[nl + 1 :]
    # Salvage by grabbing the outermost JSON object if the model
    # surrounded it with prose.
    if not raw.startswith("{"):
        m = _TURNS_RE.search(raw)
        if m:
            raw = m.group(0)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[podcast] failed to parse LLM JSON: %s", raw[:200])
        return []

    turns = parsed.get("turns")
    if not isinstance(turns, list):
        return []
    out: list[dict[str, str]] = []
    for t in turns:
        if not isinstance(t, dict):
            continue
        speaker = str(t.get("speaker") or "").strip().lower()
        text = str(t.get("text") or "").strip()
        if speaker not in ("host", "guest") or not text:
            continue
        out.append({"speaker": speaker, "text": text[:600]})
    return out


# ── Orchestrator ─────────────────────────────────────────────────────


async def _emit(event: str, **kwargs: Any) -> None:
    """Wrapper so we can quickly add room-scoping later if needed."""
    await bus.emit(event, **kwargs)


async def _play_turn(state: _SessionState, turn: dict[str, str]) -> bool:
    """Synthesise + emit one turn. Returns False if interrupted mid-play.

    The audio is base64-chunked over the bus, mirroring the swarm's
    ``agent.speech_audio_*`` shape so the frontend can reuse the
    same playback machinery if it wants to. We emit under a different
    namespace (``podcast.line_audio_*``) to keep the event schema
    independent of the swarm's.
    """
    spec = state.spec
    speaker = turn["speaker"]
    text = turn["text"]
    profile_id = spec.host_voice_profile_id if speaker == "host" else spec.guest_voice_profile_id
    display_name = spec.host_name if speaker == "host" else spec.guest_name

    profile = await get_profile(profile_id)
    if profile is None:
        await _emit(
            "podcast.line_failed",
            session_id=state.id,
            speaker=speaker,
            reason="profile_not_found",
        )
        return True  # not an interrupt — keep going

    seq = state.turns_played
    await _emit(
        "podcast.line_started",
        session_id=state.id,
        seq=seq,
        speaker=speaker,
        speaker_name=display_name,
        text=text,
    )
    await _emit(
        "podcast.line_audio_start",
        session_id=state.id,
        seq=seq,
        speaker=speaker,
        mime="audio/wav",
    )

    client = _get_tts_client()
    index = 0
    interrupted = False
    try:
        async for chunk in synthesize_streaming(
            client,
            text=text,
            voice=profile_id,
            mood="",
            language=spec.language,
            ref_audio=profile.ref_audio,
            ref_audio_mime=profile.ref_audio_mime,
            ref_text=profile.ref_text,
        ):
            if state.interrupt_event.is_set():
                interrupted = True
                break
            if not chunk:
                continue
            # base64 over the bus matches the existing
            # agent.speech_audio_chunk encoding so a future merge
            # of the playback hooks is straightforward.
            await _emit(
                "podcast.line_audio_chunk",
                session_id=state.id,
                seq=seq,
                index=index,
                b64=base64.b64encode(chunk).decode("ascii"),
            )
            index += 1
    except Exception as exc:
        logger.exception("[podcast] synth failed")
        await _emit(
            "podcast.line_failed",
            session_id=state.id,
            seq=seq,
            speaker=speaker,
            reason=f"tts_error: {exc}",
        )

    await _emit(
        "podcast.line_audio_end",
        session_id=state.id,
        seq=seq,
        speaker=speaker,
        interrupted=interrupted,
    )
    state.turns_played += 1
    return not interrupted


async def _orchestrator(state: _SessionState) -> None:
    """Main loop for one podcast session.

    Drives LLM chunk → for each turn synthesise + play → on listener
    interrupt, drop the rest of the chunk and re-enter the LLM step
    with the new context. Stops cleanly on max_total_turns or
    explicit cancel.
    """
    state.status = "running"
    await _emit("podcast.started", session_id=state.id)
    try:
        while state.turns_played < state.spec.max_total_turns:
            chunk = await _generate_chunk(state)
            if not chunk:
                logger.warning("[podcast %s] empty chunk; stopping", state.id)
                state.status = "error"
                break
            # Append to history *before* playback so an interrupt
            # mid-chunk doesn't lose the lines already committed.
            for turn in chunk:
                state.history.append(turn)
            # Pending listener input was incorporated into the chunk
            # we just generated; merge it into history as a synthetic
            # ``listener`` turn so subsequent prompts have it.
            if state.pending_user_input:
                state.history.append(
                    {"speaker": "listener", "text": state.pending_user_input}
                )
                state.pending_user_input = None
            for turn in chunk:
                if state.interrupt_event.is_set():
                    state.interrupt_event.clear()
                    break  # re-enter the outer loop → new LLM chunk
                ok = await _play_turn(state, turn)
                if not ok:
                    state.interrupt_event.clear()
                    break
                if state.turns_played >= state.spec.max_total_turns:
                    break
        if state.status == "running":
            state.status = "ended"
    except asyncio.CancelledError:
        state.status = "ended"
        raise
    except Exception as exc:
        logger.exception("[podcast %s] orchestrator crashed", state.id)
        state.status = "error"
        state.error = str(exc)
    finally:
        await _emit(
            "podcast.ended",
            session_id=state.id,
            turns_played=state.turns_played,
            status=state.status,
        )


# ── Endpoints ────────────────────────────────────────────────────────


def _public_view(state: _SessionState) -> dict[str, Any]:
    """JSON-serialisable shape for GET responses."""
    return {
        "id": state.id,
        "owner_user_id": state.owner_user_id,
        "status": state.status,
        "turns_played": state.turns_played,
        "max_total_turns": state.spec.max_total_turns,
        "host_name": state.spec.host_name,
        "guest_name": state.spec.guest_name,
        "topic": state.spec.topic,
        "language": state.spec.language,
        "history": state.history[-20:],
        "error": state.error,
    }


@router.post("/api/podcast/sessions")
async def create_session(
    payload: CreateSessionRequest,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    # Validate both voice profiles up-front so the user gets a clear
    # 400 before they hit start and end up debugging the orchestrator.
    for label, pid in (
        ("host", payload.host_voice_profile_id),
        ("guest", payload.guest_voice_profile_id),
    ):
        prof = await get_profile(pid)
        if prof is None:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "voice_profile_not_found",
                    "message": f"{label} voice profile {pid} not found",
                },
            )
    sid = str(uuid.uuid4())
    state = _SessionState(id=sid, owner_user_id=str(user.id), spec=payload)
    async with _sessions_lock:
        _sessions[sid] = state
    return _public_view(state)


def _require_owned(session_id: str, user: User) -> _SessionState:
    state = _sessions.get(session_id)
    if state is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found"},
        )
    if state.owner_user_id != str(user.id) and user.role != "admin":
        # Same 404 vs 403 reasoning as elsewhere — don't reveal the
        # existence of someone else's session via status code.
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found"},
        )
    return state


@router.post("/api/podcast/sessions/{session_id}/start")
async def start_session(
    session_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    state = _require_owned(session_id, user)
    if state.task is not None and not state.task.done():
        return _public_view(state)  # already running — idempotent
    state.error = None
    state.interrupt_event.clear()
    state.task = asyncio.create_task(_orchestrator(state), name=f"podcast-{session_id}")
    return _public_view(state)


@router.post("/api/podcast/sessions/{session_id}/stop")
async def stop_session(
    session_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    state = _require_owned(session_id, user)
    if state.task and not state.task.done():
        state.task.cancel()
        try:
            await state.task
        except (asyncio.CancelledError, Exception):
            pass
    state.task = None
    state.status = "ended"
    return _public_view(state)


@router.post("/api/podcast/sessions/{session_id}/interrupt")
async def interrupt_session(
    session_id: str,
    payload: InterruptRequest,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Inject a listener message and break the current playback.

    The orchestrator picks up ``pending_user_input`` on the next LLM
    chunk; ``interrupt_event`` ensures we don't have to wait for the
    current line to finish first.
    """
    state = _require_owned(session_id, user)
    text = payload.text.strip()
    if not text:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"code": "empty_text"},
        )
    state.pending_user_input = text
    state.interrupt_event.set()
    await _emit(
        "podcast.user_input",
        session_id=state.id,
        username=user.username,
        text=text,
    )
    return {"status": "queued", "session_id": state.id}


@router.get("/api/podcast/sessions/{session_id}")
async def get_session(
    session_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    state = _require_owned(session_id, user)
    return _public_view(state)


@router.get("/api/podcast/sessions")
async def list_sessions(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    out = [
        _public_view(s)
        for s in _sessions.values()
        if s.owner_user_id == str(user.id) or user.role == "admin"
    ]
    return {"sessions": out}


@router.delete("/api/podcast/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    state = _require_owned(session_id, user)
    if state.task and not state.task.done():
        state.task.cancel()
        try:
            await state.task
        except (asyncio.CancelledError, Exception):
            pass
    async with _sessions_lock:
        _sessions.pop(session_id, None)
    return {"status": "deleted", "session_id": session_id}

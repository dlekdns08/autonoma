"""Podcast / multi-character dialogue — N-participant variant.

A session now describes 2–6 participants (each: name, voice profile,
persona, optional VRM file). The LLM picks who speaks next on every
turn — no strict rotation. Listeners can interject with text or voice;
that input is folded into the next dialogue chunk.

Persistence: in-memory only. Session lives until the API restarts or
the owner stops it.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import time
import uuid
import wave
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


class ParticipantSpec(BaseModel):
    """One speaker on the podcast.

    ``vrm_file`` is a hint for the frontend stage — the backend doesn't
    use it for anything other than echoing it back on each
    ``podcast.line_started`` event so the UI knows which VRM to
    spotlight. Empty string = let the frontend pick its default.
    """

    name: str = Field(..., min_length=1, max_length=64)
    voice_profile_id: str = Field(..., min_length=1, max_length=64)
    persona: str = Field("", max_length=400)
    vrm_file: str = Field("", max_length=128)


class CreateSessionRequest(BaseModel):
    # 2 minimum — a "podcast" needs more than one voice. 6 is a soft
    # cap so the LLM context stays manageable; raise carefully if you
    # try larger casts (token budget grows quickly).
    participants: list[ParticipantSpec] = Field(..., min_length=2, max_length=6)
    topic: str = Field(..., min_length=1, max_length=400)
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
    # Rolling history of turns the LLM has already produced. Each entry
    # is ``{"speaker": "<participant.name>" or "listener", "text": str}``.
    # Used as conversational context on the next LLM call.
    history: list[dict[str, str]] = field(default_factory=list)
    turns_played: int = 0
    # Pending listener interrupt (raw text). Consumed at the start
    # of the next LLM call, then cleared.
    pending_user_input: str | None = None
    # Background orchestrator task. None when not running.
    task: asyncio.Task[Any] | None = None
    # Set when the user (or system) wants the current loop to break
    # out of its TTS playback and re-enter the LLM stage — typically
    # because new listener input arrived.
    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Set while the orchestrator is paused. Cleared by /resume.
    paused_event: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    def participant_by_name(self, name: str) -> ParticipantSpec | None:
        """Case-insensitive lookup. The LLM occasionally capitalises
        differently than the spec, so we normalise here rather than
        forcing the model to echo the exact string.
        """
        target = name.strip().lower()
        for p in self.spec.participants:
            if p.name.lower() == target:
                return p
        return None


_sessions: dict[str, _SessionState] = {}
_sessions_lock = asyncio.Lock()


# ── Module-level resources ───────────────────────────────────────────


# Single shared TTS client. Cheap to share — OmniVoice itself
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
    """Reuse the same admin LLM credential the swarm boots with."""
    from autonoma.api import _build_admin_llm_config  # local — circular at module load

    cfg = _build_admin_llm_config()
    if cfg is None:
        return None
    from autonoma.llm import create_llm_client

    return create_llm_client(cfg)


def _render_history_for_prompt(state: _SessionState) -> str:
    if not state.history:
        return "  (start of conversation)"
    out: list[str] = []
    for t in state.history[-20:]:
        speaker = t["speaker"]
        if speaker == "listener":
            out.append(f"  LISTENER: {t['text']}")
        else:
            out.append(f"  {speaker}: {t['text']}")
    return "\n".join(out)


def _render_participants_for_prompt(state: _SessionState) -> str:
    out: list[str] = []
    for p in state.spec.participants:
        persona = p.persona or "A thoughtful contributor to the conversation."
        out.append(f"  - {p.name}: {persona}")
    return "\n".join(out)


async def _generate_chunk(state: _SessionState) -> list[dict[str, str]]:
    """Ask the LLM to produce the next ``chunk_size`` dialogue turns.

    Returns a list of ``{"speaker": "<name>", "text": str}`` dicts —
    speaker names are matched case-insensitively against the
    participant spec downstream.
    """
    spec = state.spec
    client = _build_admin_llm_client()
    if client is None:
        state.error = "admin LLM not configured"
        return []

    listener_block = ""
    if state.pending_user_input:
        listener_block = (
            f"\nA live listener just commented: \"{state.pending_user_input}\"\n"
            "The next turns should naturally acknowledge or react to this input — "
            "do NOT ignore it.\n"
        )

    valid_speakers = ", ".join(p.name for p in spec.participants)

    system = (
        "You are scripting a relaxed multi-person podcast dialogue. "
        "Keep each turn 1–3 sentences, conversational, and in the same "
        "language the topic is given in. Pick speakers naturally based "
        "on conversation flow — don't strictly rotate, but make sure "
        "everyone participates over the long run. Output STRICT JSON only "
        "— no markdown fences, no preface."
    )
    user_msg = f"""PARTICIPANTS:
{_render_participants_for_prompt(state)}

VALID SPEAKER NAMES (use exactly these, no others): {valid_speakers}

TOPIC: {spec.topic}

CONVERSATION SO FAR:
{_render_history_for_prompt(state)}
{listener_block}
Generate the next {spec.chunk_size} dialogue turns. Output ONLY this JSON:

{{"turns": [{{"speaker": "<name>", "text": "..."}}, ...]}}"""

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
    if raw.startswith("```"):
        raw = raw.strip("`")
        nl = raw.find("\n")
        if nl != -1 and raw[:nl].strip().lower() == "json":
            raw = raw[nl + 1 :]
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
        speaker = str(t.get("speaker") or "").strip()
        text = str(t.get("text") or "").strip()
        if not speaker or not text:
            continue
        # Resolve speaker case-insensitively. Drop the turn entirely if
        # the model invented a name that's not in the spec — better
        # than silently piping "Charlie" to Alice's voice.
        match = state.participant_by_name(speaker)
        if match is None:
            logger.warning("[podcast] LLM returned unknown speaker %r — dropping turn", speaker)
            continue
        out.append({"speaker": match.name, "text": text[:600]})
    return out


# ── Orchestrator ─────────────────────────────────────────────────────


async def _emit(event: str, **kwargs: Any) -> None:
    await bus.emit(event, **kwargs)


def _wav_duration_s(wav_bytes: bytes) -> float:
    """Return WAV blob duration in seconds, 0.0 if unparseable.

    The orchestrator uses this to space turns by the actual playback
    length. Without it, fast backends (MLX is ≤1 s synth for a 5 s
    utterance) emit several speakers' audio back-to-back; the client
    queues the WAVs into separate ``<audio>`` elements, but the next
    turn's ``line_audio_end`` arrives before the previous turn's
    ``onended`` fires, so the previous voice is cut off and speakers
    overlap mid-sentence ("마구 섞여서").
    """
    if not wav_bytes:
        return 0.0
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            sr = wf.getframerate()
            if sr <= 0 or frames <= 0:
                return 0.0
            return frames / float(sr)
    except (wave.Error, EOFError):
        return 0.0


async def _play_turn(state: _SessionState, turn: dict[str, str]) -> bool:
    """Synthesise + emit one turn. Returns False if interrupted mid-play."""
    spec = state.spec
    speaker_name = turn["speaker"]
    text = turn["text"]
    participant = state.participant_by_name(speaker_name)
    if participant is None:
        # Defensive — _generate_chunk should have filtered this.
        return True

    profile = await get_profile(participant.voice_profile_id)
    if profile is None:
        await _emit(
            "podcast.line_failed",
            session_id=state.id,
            speaker=speaker_name,
            reason="profile_not_found",
        )
        return True  # not an interrupt — keep going

    seq = state.turns_played
    await _emit(
        "podcast.line_started",
        session_id=state.id,
        seq=seq,
        speaker=speaker_name,
        speaker_name=speaker_name,
        vrm_file=participant.vrm_file or "",
        text=text,
    )
    await _emit(
        "podcast.line_audio_start",
        session_id=state.id,
        seq=seq,
        speaker=speaker_name,
        vrm_file=participant.vrm_file or "",
        mime="audio/wav",
    )

    client = _get_tts_client()
    index = 0
    interrupted = False
    audio_buf = bytearray()
    try:
        async for chunk in synthesize_streaming(
            client,
            text=text,
            voice=participant.voice_profile_id,
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
            audio_buf.extend(chunk)
            await _emit(
                "podcast.line_audio_chunk",
                session_id=state.id,
                seq=seq,
                speaker=speaker_name,
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
            speaker=speaker_name,
            reason=f"tts_error: {exc}",
        )

    await _emit(
        "podcast.line_audio_end",
        session_id=state.id,
        seq=seq,
        speaker=speaker_name,
        interrupted=interrupted,
    )
    state.turns_played += 1

    # Hold the orchestrator until the client is likely done playing
    # this line. Synth (esp. MLX) finishes far faster than playback,
    # so without this gate the next ``_play_turn`` would already be
    # streaming the next speaker before the current one finishes.
    # 250 ms safety margin covers WS jitter + browser audio decode +
    # ``onended`` event scheduling latency. The interrupt_event short
    # circuits the wait so listener interjections don't get queued
    # behind the unfinished line.
    if not interrupted and audio_buf:
        playback_s = _wav_duration_s(bytes(audio_buf))
        if playback_s > 0:
            try:
                await asyncio.wait_for(
                    state.interrupt_event.wait(),
                    timeout=playback_s + 0.25,
                )
                # Interrupt fired mid-playback — propagate so the outer
                # loop drops the remaining queued chunk and re-enters
                # the LLM step with the new context.
                interrupted = True
            except asyncio.TimeoutError:
                # Normal completion — playback finished on time.
                pass

    return not interrupted


async def _orchestrator(state: _SessionState) -> None:
    """Main loop for one podcast session.

    Drives LLM chunk → for each turn synthesise + play → on listener
    interrupt or pause, drop the rest of the chunk and re-enter the
    LLM step with the new context.
    """
    state.status = "running"
    await _emit(
        "podcast.started",
        session_id=state.id,
        participants=[
            {"name": p.name, "vrm_file": p.vrm_file} for p in state.spec.participants
        ],
    )
    try:
        while state.turns_played < state.spec.max_total_turns:
            # Honour pause — sit on the event until /resume clears it.
            if state.paused_event.is_set():
                state.status = "paused"
                await _emit("podcast.paused", session_id=state.id)
                while state.paused_event.is_set():
                    await asyncio.sleep(0.2)
                state.status = "running"
                await _emit("podcast.resumed", session_id=state.id)

            chunk = await _generate_chunk(state)
            if not chunk:
                logger.warning("[podcast %s] empty chunk; stopping", state.id)
                state.status = "error"
                break
            for turn in chunk:
                state.history.append(turn)
            if state.pending_user_input:
                state.history.append(
                    {"speaker": "listener", "text": state.pending_user_input}
                )
                state.pending_user_input = None
            for turn in chunk:
                if state.interrupt_event.is_set():
                    state.interrupt_event.clear()
                    break
                if state.paused_event.is_set():
                    break
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
    return {
        "id": state.id,
        "owner_user_id": state.owner_user_id,
        "status": state.status,
        "turns_played": state.turns_played,
        "max_total_turns": state.spec.max_total_turns,
        "topic": state.spec.topic,
        "language": state.spec.language,
        "participants": [
            {
                "name": p.name,
                "voice_profile_id": p.voice_profile_id,
                "persona": p.persona,
                "vrm_file": p.vrm_file,
            }
            for p in state.spec.participants
        ],
        "history": state.history[-30:],
        "error": state.error,
    }


@router.post("/api/podcast/sessions")
async def create_session(
    payload: CreateSessionRequest,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    # Reject duplicate names — the LLM disambiguates by name, so two
    # participants sharing one would be unaddressable.
    seen: set[str] = set()
    for p in payload.participants:
        key = p.name.strip().lower()
        if key in seen:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "duplicate_participant_name",
                    "message": f"Participant name '{p.name}' is duplicated.",
                },
            )
        seen.add(key)

    # Validate each voice profile so the user gets a clean 400 before
    # the orchestrator starts and ends up debugging it from logs.
    for p in payload.participants:
        prof = await get_profile(p.voice_profile_id)
        if prof is None:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "voice_profile_not_found",
                    "message": f"Voice profile {p.voice_profile_id} not found for {p.name}",
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
        return _public_view(state)
    state.error = None
    state.interrupt_event.clear()
    state.paused_event.clear()
    state.task = asyncio.create_task(_orchestrator(state), name=f"podcast-{session_id}")
    return _public_view(state)


@router.post("/api/podcast/sessions/{session_id}/stop")
async def stop_session(
    session_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    state = _require_owned(session_id, user)
    state.paused_event.clear()
    if state.task and not state.task.done():
        state.task.cancel()
        try:
            await state.task
        except (asyncio.CancelledError, Exception):
            pass
    state.task = None
    state.status = "ended"
    return _public_view(state)


@router.post("/api/podcast/sessions/{session_id}/pause")
async def pause_session(
    session_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Halt playback at the next inter-turn boundary.

    The current line finishes synthesising (cancelling mid-chunk would
    leave a half-rendered WAV on the bus), then the orchestrator
    parks on ``paused_event`` until /resume.
    """
    state = _require_owned(session_id, user)
    state.paused_event.set()
    return _public_view(state)


@router.post("/api/podcast/sessions/{session_id}/resume")
async def resume_session(
    session_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    state = _require_owned(session_id, user)
    state.paused_event.clear()
    return _public_view(state)


@router.post("/api/podcast/sessions/{session_id}/interrupt")
async def interrupt_session(
    session_id: str,
    payload: InterruptRequest,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Inject a listener message and break the current playback."""
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
    state.paused_event.clear()
    if state.task and not state.task.done():
        state.task.cancel()
        try:
            await state.task
        except (asyncio.CancelledError, Exception):
            pass
    async with _sessions_lock:
        _sessions.pop(session_id, None)
    return {"status": "deleted", "session_id": session_id}

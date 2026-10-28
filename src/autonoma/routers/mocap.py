"""Mocap clip / binding / trigger routes (``/api/mocap*``).

Extracted from ``autonoma.api``: paths, methods, auth dependencies,
status codes and behaviour are unchanged. Live session state stays in
``autonoma.api`` and is reached through lazy imports so importing this
router never triggers the module-load cycle.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)
from fastapi import (
    Response as FastAPIResponse,
)
from fastapi import (
    status as http_status,
)

from autonoma.auth import (
    require_active_user,
    require_admin,
)
from autonoma.db.users import (
    User,
)
from autonoma.event_bus import bus

logger = logging.getLogger(__name__)

router = APIRouter()


# ── /api/mocap/* ──────────────────────────────────────────────────────
#
# Motion-capture clip library + global trigger→clip bindings for the
# VTuber character playback path. The ``/mocap`` page records webcam
# motion via MediaPipe, uploads gzipped JSON clips, and wires them to
# (vrm_file, kind, value) triggers. Any agent rendered with that VRM
# plays the bound clip site-wide.
#
# Binding mutations emit ``mocap.bindings.updated`` on the shared bus.
# Because these handlers run in HTTP context (no ``_current_session_id``)
# the bus→WS bridge falls through to ``manager.broadcast``, fanning the
# update out to every connected viewer.

import collections as _collections  # noqa: E402

from autonoma.mocap import (  # noqa: E402  — grouped at feature boundary
    ALLOWED_TRIGGER_KINDS,
    MocapValidationError,
    is_known_vrm,
    validate_payload,
    validate_trigger,
)
from autonoma.mocap import store as mocap_store  # noqa: E402
from autonoma.mocap.triggers import trigger_catalog  # noqa: E402

# ── Mocap upload abuse-prevention ─────────────────────────────────────
#
# In-process rate limiter (deque of monotonic timestamps per user) and
# per-user storage quota. Deliberately no external dependency — the
# instance is small enough that a process-local limiter is accurate
# enough, and adding slowapi/fastapi-limiter for this alone is not
# worth the surface area.
#
# Policy (fixed):
#   - 5 uploads / 60s per user
#   - 20 uploads / 3600s per user
#   - 100 clips OR 500 MB total storage per user
#   - admins (user.role == "admin") bypass every check
#
# ``time.monotonic`` is used for timestamps because it's immune to
# wall-clock adjustments (ntp, DST) that would otherwise let a user
# burst past the window.
_mocap_upload_history: dict[str, _collections.deque[float]] = {}
_MOCAP_RATE_MINUTE = 5
_MOCAP_RATE_HOUR = 20
_MOCAP_QUOTA_CLIPS = 100
_MOCAP_QUOTA_BYTES = 500 * 1024 * 1024


def _check_mocap_upload_rate(user_id: str) -> str | None:
    """Record an upload attempt and return a rate-limit code or ``None``.

    Returns ``"rate_limited_minute"`` if the user has ≥5 uploads in the
    last 60 seconds, ``"rate_limited_hour"`` if ≥20 in the last 3600
    seconds, otherwise ``None`` (and records the timestamp so the next
    call sees this attempt). Entries older than 3600 seconds are
    trimmed on every call so the deque stays O(rate_hour).
    """
    now = time.monotonic()
    history = _mocap_upload_history.setdefault(user_id, _collections.deque())

    # Trim anything older than the hour window — keeps the deque
    # bounded and makes the count queries below cheap.
    hour_cutoff = now - 3600.0
    while history and history[0] < hour_cutoff:
        history.popleft()

    # Count entries in the hour window (== len(history) after trim) and
    # in the minute window. We check the minute rule first because it's
    # the tighter limit; either violation short-circuits.
    minute_cutoff = now - 60.0
    minute_count = sum(1 for ts in history if ts >= minute_cutoff)
    if minute_count >= _MOCAP_RATE_MINUTE:
        return "rate_limited_minute"
    if len(history) >= _MOCAP_RATE_HOUR:
        return "rate_limited_hour"

    history.append(now)
    return None


@router.get("/api/mocap/triggers")
async def mocap_triggers(
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Static whitelist of allowed trigger values for the UI."""
    return trigger_catalog()


@router.get("/api/mocap-clips")
async def mocap_list_clips(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    clips = await mocap_store.list_clips_for_user(user.id)
    return {"clips": [c.to_dict() for c in clips]}


@router.post("/api/mocap-clips", status_code=http_status.HTTP_201_CREATED)
async def mocap_create_clip(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    # Policy gate: rate limiter first (cheapest), then quota (one DB
    # round-trip), then the usual shape/content validation. Admins
    # bypass both the rate limit and the quota — they manage the
    # library for every user and can't be throttled by their own work.
    if user.role != "admin":
        rate_err = _check_mocap_upload_rate(user.id)
        if rate_err is not None:
            raise HTTPException(
                status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
                detail=rate_err,
            )

    name = str(payload.get("name") or "").strip()
    source_vrm = str(payload.get("source_vrm") or "").strip()
    payload_gz_b64 = str(payload.get("payload_gz_b64") or "")
    expected_size = payload.get("expected_size_bytes")

    if user.role != "admin":
        # Pessimistic projection: if the client supplied an
        # expected_size_bytes use it directly; otherwise fall back to
        # the base64 length. Base64 over-estimates the decoded size by
        # ~33% which is fine — it only biases us toward rejecting at
        # the quota boundary, never toward accepting past it.
        if isinstance(expected_size, (int, float)) and expected_size >= 0:
            projected_size = int(expected_size)
        else:
            projected_size = len(payload_gz_b64)

        count, total_bytes = await mocap_store.get_user_storage_usage(user.id)
        if count >= _MOCAP_QUOTA_CLIPS:
            raise HTTPException(
                status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="quota_clip_count",
            )
        if total_bytes + projected_size > _MOCAP_QUOTA_BYTES:
            raise HTTPException(
                status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="quota_bytes",
            )

    if not is_known_vrm(source_vrm):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="unknown_source_vrm",
        )

    try:
        validated = validate_payload(
            payload_gz_b64,
            name=name,
            source_vrm=source_vrm,
            expected_size_bytes=(
                int(expected_size) if isinstance(expected_size, (int, float)) else None
            ),
        )
    except MocapValidationError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=exc.code,
        )

    clip = await mocap_store.create_clip(owner_user_id=user.id, validated=validated)
    await bus.emit("mocap.clips.updated", clip_id=clip.id, action="created")
    return {"clip": clip.to_dict()}


@router.get("/api/mocap-clips/orphans")
async def mocap_list_orphans(
    days: int = Query(90, ge=1, le=3650),
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Admin-only report of clips with no binding that haven't been
    accessed in ``days`` days. Never deletes — the policy contract
    requires a human to decide what gets purged.

    Must be registered BEFORE the ``/{clip_id}`` route; FastAPI matches
    routes in declaration order and "orphans" would otherwise match the
    path parameter and 404 as an unknown clip.

    Returns:
        ``{"orphans": [...], "count": N, "total_bytes": M}`` where
        each element is a ``ClipSummary.to_dict()``.
    """
    clips, total_bytes = await mocap_store.list_orphan_clips(older_than_days=days)
    return {
        "orphans": [c.to_dict() for c in clips],
        "count": len(clips),
        "total_bytes": total_bytes,
    }


@router.get("/api/mocap-clips/{clip_id}")
async def mocap_get_clip(
    clip_id: str,
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return clip metadata + the gzipped base64 payload for playback.

    Any active user can fetch any clip — bindings are global so a
    viewer may need a clip owned by someone else to render a character.
    """
    result = await mocap_store.get_clip_payload(clip_id)
    if result is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="clip_not_found")
    summary, payload_b64 = result
    return {"clip": summary.to_dict(), "payload_gz_b64": payload_b64}


@router.patch("/api/mocap-clips/{clip_id}")
async def mocap_rename_clip(
    clip_id: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    summary = await mocap_store.get_clip_summary(clip_id)
    if summary is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="clip_not_found")
    if summary.owner_user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="not_owner")
    new_name = str(payload.get("name") or "").strip()
    if not (1 <= len(new_name) <= 128):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="invalid_name")
    updated = await mocap_store.rename_clip(clip_id, new_name)
    if updated is None:
        # The summary lookup above already proved the clip exists, so a
        # None here means the row vanished mid-request (concurrent delete)
        # rather than a 404 we should return cleanly.
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="rename_failed",
        )
    await bus.emit("mocap.clips.updated", clip_id=clip_id, action="renamed")
    return {"clip": updated.to_dict()}


@router.delete("/api/mocap-clips/{clip_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def mocap_delete_clip(
    clip_id: str,
    user: User = Depends(require_active_user),
) -> FastAPIResponse:
    summary = await mocap_store.get_clip_summary(clip_id)
    if summary is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="clip_not_found")
    if summary.owner_user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="not_owner")
    if await mocap_store.clip_is_bound(clip_id):
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="clip_in_use")
    try:
        ok = await mocap_store.delete_clip(clip_id)
    except mocap_store.IntegrityError:
        # Race: a binding was inserted between the ``clip_is_bound`` check
        # above and this delete. FK is ON DELETE RESTRICT so the DB
        # rejects the delete — surface the same 409 the pre-check would.
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="clip_in_use")
    if not ok:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="clip_not_found")
    await bus.emit("mocap.clips.updated", clip_id=clip_id, action="deleted")
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@router.get("/api/mocap-bindings")
async def mocap_list_bindings(
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    bindings = await mocap_store.list_bindings()
    return {"bindings": [b.to_dict() for b in bindings]}


@router.put("/api/mocap-bindings")
async def mocap_upsert_binding(
    payload: dict[str, Any],
    user: User = Depends(require_admin),
) -> dict[str, Any]:
    vrm_file = str(payload.get("vrm_file") or "").strip()
    kind = str(payload.get("trigger_kind") or "").strip()
    value = str(payload.get("trigger_value") or "").strip()
    clip_id = str(payload.get("clip_id") or "").strip()

    if not is_known_vrm(vrm_file):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="unknown_vrm")
    if kind not in ALLOWED_TRIGGER_KINDS:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="invalid_kind")
    trig_err = validate_trigger(kind, value)
    if trig_err:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=trig_err)
    clip = await mocap_store.get_clip_summary(clip_id)
    if clip is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="clip_not_found")

    binding = await mocap_store.upsert_binding(
        vrm_file=vrm_file,
        trigger_kind=kind,
        trigger_value=value,
        clip_id=clip_id,
        updated_by=user.id,
    )

    await bus.emit(
        "mocap.bindings.updated",
        vrm_file=vrm_file,
        trigger_kind=kind,
        trigger_value=value,
        clip_id=clip_id,
        removed=False,
    )
    return {"binding": binding.to_dict()}


@router.delete("/api/mocap-bindings", status_code=http_status.HTTP_204_NO_CONTENT)
async def mocap_delete_binding(
    vrm_file: str = Query(...),
    trigger_kind: str = Query(...),
    trigger_value: str = Query(...),
    _admin: User = Depends(require_admin),
) -> FastAPIResponse:
    if not is_known_vrm(vrm_file):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="unknown_vrm")
    trig_err = validate_trigger(trigger_kind, trigger_value)
    if trig_err:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=trig_err)
    ok = await mocap_store.delete_binding(
        vrm_file=vrm_file,
        trigger_kind=trigger_kind,
        trigger_value=trigger_value,
    )
    if not ok:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="binding_not_found")
    await bus.emit(
        "mocap.bindings.updated",
        vrm_file=vrm_file,
        trigger_kind=trigger_kind,
        trigger_value=trigger_value,
        clip_id=None,
        removed=True,
    )
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@router.post("/api/mocap-triggers/fire")
async def mocap_fire_trigger(
    payload: dict[str, Any],
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    vrm_file = str(payload.get("vrm_file") or "").strip()
    slug = str(payload.get("value") or "").strip()

    if not is_known_vrm(vrm_file):
        raise HTTPException(status_code=400, detail="unknown_vrm")
    if validate_trigger("manual", slug) is not None:
        raise HTTPException(status_code=400, detail="invalid_manual_slug")

    binding = await mocap_store.get_binding(vrm_file, "manual", slug)
    if binding is None:
        raise HTTPException(status_code=404, detail="binding_not_found")

    await bus.emit(
        "mocap.trigger.fired",
        vrm_file=vrm_file,
        trigger_kind="manual",
        trigger_value=slug,
        clip_id=binding.clip_id,
    )
    return {"ok": True, "clip_id": binding.clip_id}

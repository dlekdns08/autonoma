"""Harness routes: ``/api/harness/*`` and ``/api/session/{id}/metadata``.

Extracted from ``autonoma.api``: paths, methods, auth dependencies,
status codes and behaviour are unchanged. Live session state stays in
``autonoma.api`` and is reached through lazy imports so importing this
router never triggers the module-load cycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
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
from autonoma.harness.policy import HarnessPolicyContent, default_policy_content

if TYPE_CHECKING:
    from autonoma.api import SessionState

logger = logging.getLogger(__name__)

router = APIRouter()


def _live_sessions() -> dict[int, SessionState]:
    """The live session registry, owned by ``autonoma.api``.

    Imported lazily: ``autonoma.api`` includes this router at module
    load, so a top-level import here would be a cycle.
    """
    from autonoma.api import _sessions

    return _sessions


# ── /api/harness/presets/* ────────────────────────────────────────────

_HARNESS_NAME_MIN = 1
_HARNESS_NAME_MAX = 64


def _pydantic_errors_to_fastapi(exc: Any) -> list[dict[str, Any]]:
    """Convert a pydantic ValidationError to FastAPI's native error shape.

    FastAPI renders ``RequestValidationError`` as
    ``{"detail": [{"loc": [...], "msg": "...", "type": "..."}, ...]}`` —
    we mirror that shape when rejecting ``HarnessPolicyContent(**body)``
    so the frontend can reuse the same rendering path for both.
    """
    items: list[dict[str, Any]] = []
    for err in exc.errors():
        items.append(
            {
                "loc": list(err.get("loc", [])),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return items


def _reject_invalid_harness_content(content: HarnessPolicyContent, *, is_admin: bool) -> None:
    """Raise HTTPException when semantic validation rejects ``content``.

    Admin-only violations surface as 403 (the caller lacks the role),
    dangerous-combo violations as 422 (the content is illegal for
    everyone). Field-level ``path`` ("safety.code_execution") is exposed
    via ``loc`` so the frontend can highlight the offending control.
    """
    from autonoma.harness.validation import check_content

    issues = check_content(content, is_admin=is_admin)
    if not issues:
        return
    admin_only = [i for i in issues if i.admin_only]
    if admin_only:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail=[
                {
                    "loc": ["body", "content", *i.path.split(".")],
                    "msg": i.message,
                    "type": "admin_only",
                }
                for i in admin_only
            ],
        )
    raise HTTPException(
        status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=[
            {
                "loc": ["body", "content"],
                "msg": i.message,
                "type": "dangerous_combo",
            }
            for i in issues
        ],
    )


def _harness_policy_to_dict(policy: Any) -> dict[str, Any]:
    return policy.model_dump(mode="json")


@router.get("/api/harness/pipeline")
async def get_harness_pipeline(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Pipeline-view layout: 16 nodes grouped into A/B/C plus intra-group edges.

    Read-only; the actual editing flow still goes through the existing
    preset / override endpoints. This endpoint exists so the UI can lay
    the harness out as a visual flow without embedding the grouping
    decisions into frontend code.
    """
    from autonoma.harness.pipeline import pipeline_payload

    return pipeline_payload()


@router.get("/api/harness/schema")
async def get_harness_schema(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Field-by-field schema so the frontend can build a form without
    mirroring the Pydantic model. Returns a flattened shape:

    ``{"sections": {"<section>": {"<field>": {type, default, options?,
    min?, max?}}}}``

    The frontend builds a drop-down for ``type == "enum"`` and a number
    input with ``min``/``max`` for ``type == "int"`` / ``"float"``.
    Stays in lockstep with ``HarnessPolicyContent`` automatically — when
    a Literal gains a value or a Field changes bounds, this endpoint
    reflects it on the next request without a code change here.
    """
    from typing import Literal, get_args, get_origin

    content_cls = HarnessPolicyContent
    sections: dict[str, dict[str, Any]] = {}
    default_content = default_policy_content().model_dump(mode="json")

    for section_name, section_field in content_cls.model_fields.items():
        sub_model = section_field.annotation
        assert sub_model is not None  # every section is a typed sub-model
        fields: dict[str, Any] = {}
        for field_name, field_info in sub_model.model_fields.items():
            annot = field_info.annotation
            default = default_content[section_name].get(field_name)
            spec: dict[str, Any] = {"default": default}
            if get_origin(annot) is Literal:
                spec["type"] = "enum"
                spec["options"] = list(get_args(annot))
            elif annot is bool:
                spec["type"] = "bool"
            elif annot is int:
                spec["type"] = "int"
            elif annot is float:
                spec["type"] = "float"
            else:
                spec["type"] = "unknown"
            # Pydantic stores ge/le on constraint metadata.
            for meta in field_info.metadata:
                if hasattr(meta, "ge"):
                    spec["min"] = meta.ge
                if hasattr(meta, "le"):
                    spec["max"] = meta.le
            fields[field_name] = spec
        sections[section_name] = fields
    return {"sections": sections}


@router.get("/api/harness/presets")
async def list_harness_presets(
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """List the caller's presets plus the system default preset."""
    from autonoma.db.harness_policies import (
        get_default_policy,
        list_policies_for_user,
    )

    owned = await list_policies_for_user(user.id)
    seen_ids = {p.id for p in owned}
    # list_policies_for_user already includes the default when it exists,
    # but we re-fetch and merge defensively in case the default was
    # created after the user's rows (ordering quirks).
    default = await get_default_policy()
    presets = list(owned)
    if default is not None and default.id not in seen_ids:
        presets.append(default)
    return {"presets": [_harness_policy_to_dict(p) for p in presets]}


@router.get("/api/harness/presets/{preset_id}")
async def get_harness_preset(
    preset_id: str,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    from autonoma.db.harness_policies import get_policy_by_id

    preset = await get_policy_by_id(preset_id)
    if preset is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="preset_not_found",
        )
    if not preset.is_default and preset.owner_user_id != user.id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    return _harness_policy_to_dict(preset)


@router.post(
    "/api/harness/presets",
    status_code=http_status.HTTP_201_CREATED,
)
async def create_harness_preset(
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    from pydantic import ValidationError

    from autonoma.db.harness_policies import create_policy

    name = str(payload.get("name") or "").strip()
    if not (_HARNESS_NAME_MIN <= len(name) <= _HARNESS_NAME_MAX):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[
                {
                    "loc": ["body", "name"],
                    "msg": (f"name must be {_HARNESS_NAME_MIN}..{_HARNESS_NAME_MAX} chars"),
                    "type": "value_error",
                }
            ],
        )
    content_raw = payload.get("content")
    if not isinstance(content_raw, dict):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[
                {
                    "loc": ["body", "content"],
                    "msg": "content must be an object",
                    "type": "type_error",
                }
            ],
        )
    try:
        content = HarnessPolicyContent(**content_raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_pydantic_errors_to_fastapi(exc),
        )
    _reject_invalid_harness_content(content, is_admin=(user.role == "admin"))
    created = await create_policy(
        owner_user_id=user.id,
        name=name,
        content=content,
    )
    return _harness_policy_to_dict(created)


@router.put("/api/harness/presets/{preset_id}")
async def update_harness_preset(
    preset_id: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    from pydantic import ValidationError

    from autonoma.db.harness_policies import (
        get_policy_by_id,
        update_policy,
    )

    existing = await get_policy_by_id(preset_id)
    if existing is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="preset_not_found",
        )
    # Default preset is read-only; surface as 403 rather than letting the
    # helper's ValueError escape as 500.
    if existing.is_default:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    if existing.owner_user_id != user.id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )

    new_name: str | None = None
    if "name" in payload and payload["name"] is not None:
        new_name = str(payload["name"]).strip()
        if not (_HARNESS_NAME_MIN <= len(new_name) <= _HARNESS_NAME_MAX):
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=[
                    {
                        "loc": ["body", "name"],
                        "msg": (f"name must be {_HARNESS_NAME_MIN}..{_HARNESS_NAME_MAX} chars"),
                        "type": "value_error",
                    }
                ],
            )

    new_content: HarnessPolicyContent | None = None
    if "content" in payload and payload["content"] is not None:
        content_raw = payload["content"]
        if not isinstance(content_raw, dict):
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=[
                    {
                        "loc": ["body", "content"],
                        "msg": "content must be an object",
                        "type": "type_error",
                    }
                ],
            )
        try:
            new_content = HarnessPolicyContent(**content_raw)
        except ValidationError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=_pydantic_errors_to_fastapi(exc),
            )
        _reject_invalid_harness_content(new_content, is_admin=(user.role == "admin"))

    try:
        updated = await update_policy(preset_id, name=new_name, content=new_content)
    except ValueError:
        # Defense in depth: the explicit is_default check above should
        # have caught this, but keep the translation in case the helper
        # adds other ValueError paths.
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    if updated is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="preset_not_found",
        )
    return _harness_policy_to_dict(updated)


@router.delete(
    "/api/harness/presets/{preset_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_harness_preset(
    preset_id: str,
    user: User = Depends(require_active_user),
) -> FastAPIResponse:
    from autonoma.db.harness_policies import delete_policy, get_policy_by_id

    existing = await get_policy_by_id(preset_id)
    if existing is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="preset_not_found",
        )
    if existing.is_default or existing.owner_user_id != user.id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    try:
        await delete_policy(preset_id)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="preset_forbidden",
        )
    return FastAPIResponse(status_code=http_status.HTTP_204_NO_CONTENT)


@router.get("/api/harness/metrics")
async def get_harness_metrics(
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Global strategy-pick counters across every run on this process.

    Keys are ``"section.field=value"`` (e.g. ``"routing.strategy=priority"``);
    values are pick counts since server start. Admin-only since the
    signal is effectively a usage heat-map — not sensitive per se, but
    not something we want public either.
    """
    from autonoma.harness.observability import get_global_counters

    return {"counters": get_global_counters()}


@router.get("/api/harness/metrics/summary")
async def get_harness_metrics_summary(
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Aggregated harness metrics for the current (or most recently completed)
    run. Admin-only.

    Returns preset usage breakdown, LLM parse success rate, average stalls
    per run, top blocked actions, and LLM error categories.
    """
    from autonoma.harness.observability import get_metrics_summary

    num_runs = max(1, len(_live_sessions()))
    return get_metrics_summary(num_runs=num_runs)


@router.get("/api/session/{session_id}/metadata")
async def get_session_harness_metadata(
    session_id: int,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Per-run metadata: preset id, overridden sections, effective
    policy, timestamps, and per-run picks. Callers that own a live
    session can fetch mid-run; the ``ended_at`` field flips from null to
    a timestamp when the run finishes.

    Scoped to the caller's own sessions — admins can fetch any session
    since the signal is useful for post-mortem debugging."""
    from autonoma.harness.observability import (
        get_session_metadata,
        metadata_to_dict,
    )

    meta = get_session_metadata(session_id)
    if meta is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="session_not_found",
        )
    # Non-admins can only see metadata for sessions they opened on this
    # connection. ``_sessions`` is keyed by session_id and currently has
    # no user_id link, so fall back to a "any active user sees their
    # own session" check via the live SessionState (ws owner).
    if user.role != "admin":
        sess = _live_sessions().get(session_id)
        if sess is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="session_not_found",
            )
    return metadata_to_dict(meta)

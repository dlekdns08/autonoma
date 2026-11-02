"""Billed cost routes (``/api/billing/*``) — admin only.

Reads Anthropic's own usage and cost reports (see ``autonoma.billing``)
so the operator can reconcile the swarm's list-price estimates
(``swarm.cost`` events) against the invoice.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from autonoma import billing
from autonoma.auth import require_admin
from autonoma.db.users import User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/billing/summary")
async def billing_summary(
    days: int = Query(7, ge=1, le=31),
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Daily billed USD by model plus token usage for the last ``days``.

    503 with ``code: billing_unavailable`` when no admin credential is
    configured or Anthropic refuses it — the dashboard then falls back
    to list-price estimates.
    """
    try:
        result = await billing.summary(days=days)
    except billing.BillingUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "billing_unavailable", "message": str(exc)},
        ) from exc
    return result.to_dict()

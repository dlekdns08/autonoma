"""Prometheus scrape endpoint — feature #10.

A single ``GET /metrics`` route that hands out the current snapshot of
the in-process ``PrometheusRegistry``. The
content type is the canonical Prometheus text format (``version=0.0.4``)
so vanilla Prometheus, Grafana Agent, and the OpenTelemetry Collector
all scrape it without extra config.

Auth: open in dev. In production (``session_secret`` set) the API is
published through a tunnel, so the route requires ``Authorization:
Bearer <AUTONOMA_METRICS_TOKEN>`` — the standard ``bearer_token`` scrape
option — and answers 404 otherwise. A cookie session would block every
scraper, a bearer token does not.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Request, Response

from autonoma.config import settings
from autonoma.observability_otel import prom_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Return the Prometheus text-format snapshot.

    When ``settings.prometheus_metrics_enabled`` is False we return a
    404 so operators can quickly take the endpoint offline without
    redeploying. The rest of the registry keeps recording — flipping
    the flag back on doesn't lose data.
    """
    if not settings.prometheus_metrics_enabled:
        return Response(status_code=404)
    if settings.session_secret:
        # Production: bearer token or nothing. 404 (not 401) so the
        # endpoint doesn't advertise itself.
        token = settings.metrics_token
        supplied = request.headers.get("authorization", "")
        if not token or not hmac.compare_digest(supplied, f"Bearer {token}"):
            return Response(status_code=404)
    payload = prom_registry.render()
    return Response(payload, media_type="text/plain; version=0.0.4")

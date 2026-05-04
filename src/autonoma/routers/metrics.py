"""Prometheus scrape endpoint — feature #10.

A single public, unauthenticated ``GET /metrics`` route that hands out
the current snapshot of the in-process ``PrometheusRegistry``. The
content type is the canonical Prometheus text format (``version=0.0.4``)
so vanilla Prometheus, Grafana Agent, and the OpenTelemetry Collector
all scrape it without extra config.

Why no auth: scrape targets are expected to be either bound to a
loopback / private network or fronted by the operator's reverse proxy.
Putting a cookie session in front of ``/metrics`` would block every
production scraper out of the box.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response

from autonoma.config import settings
from autonoma.observability_otel import prom_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Return the Prometheus text-format snapshot.

    When ``settings.prometheus_metrics_enabled`` is False we return a
    404 so operators can quickly take the endpoint offline without
    redeploying. The rest of the registry keeps recording — flipping
    the flag back on doesn't lose data.
    """
    if not settings.prometheus_metrics_enabled:
        return Response(status_code=404)
    payload = prom_registry.render()
    return Response(payload, media_type="text/plain; version=0.0.4")

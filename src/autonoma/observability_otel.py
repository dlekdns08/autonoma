"""OpenTelemetry + Prometheus metrics exporter — feature #10.

Optional, fail-soft observability bridge that lets existing Grafana /
Prometheus stacks scrape Autonoma without dragging the OTel SDK in as a
hard dependency.

Two parts:

1. ``setup_otel()`` — wires the global TracerProvider against an OTLP/HTTP
   collector. If ``settings.otel_endpoint`` is empty or the
   ``opentelemetry-*`` packages aren't installed, this is a silent (or
   one-line warning) no-op. The rest of Autonoma keeps using its custom
   ``tracing.py`` regardless.

2. ``PrometheusRegistry`` — a hand-rolled, stdlib-only metrics registry
   that powers the ``GET /metrics`` endpoint (``routers/metrics.py``). We
   roll our own so the runtime never needs the ``prometheus_client``
   package; ~80 lines of code is cheaper than a dep.

The convenience helpers (``record_round`` etc.) are the only API the
swarm loop should call — they hide the metric names + label conventions
from the call sites.
"""

from __future__ import annotations

import logging
import math
import threading
from typing import Any, Iterable

from autonoma.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────── OTel bridge ────────────────────────────

# Singleton MeterProvider. Populated by ``setup_otel`` only when the OTel
# SDK is importable AND ``settings.otel_endpoint`` is set. Stays ``None``
# otherwise so ``get_meter()`` callers can short-circuit.
_meter_provider: Any | None = None
_otel_initialised: bool = False


def setup_otel() -> bool:
    """Configure the global OTel tracer + meter provider.

    Returns ``True`` once the OTLP exporter is wired, ``False`` for any
    no-op path (endpoint unset, package missing, configuration error).
    Idempotent: calling twice is safe.
    """
    global _meter_provider, _otel_initialised

    if not settings.otel_endpoint:
        # Silent no-op — operators that don't run a collector shouldn't
        # see startup noise.
        return False

    if _otel_initialised:
        return _meter_provider is not None

    try:
        # Imports live inside the function so this module loads cleanly
        # without ``opentelemetry`` installed. If any of these miss, we
        # warn once and fall through to the Prometheus-only path.
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "OTel endpoint %s configured but opentelemetry-* packages "
            "are not installed; OTLP export disabled. (Prometheus "
            "/metrics still works.)",
            settings.otel_endpoint,
        )
        _otel_initialised = True
        return False

    try:
        resource = Resource.create({"service.name": settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
        )
        trace.set_tracer_provider(provider)

        # Optional MeterProvider — only wire it if the SDK ships one. We
        # don't currently emit OTel metrics (Prometheus covers that),
        # but ``get_meter()`` exists for downstream code that wants to.
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import (
                PeriodicExportingMetricReader,
            )

            reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=settings.otel_endpoint)
            )
            _meter_provider = MeterProvider(
                resource=resource, metric_readers=[reader]
            )
        except ImportError:
            # Trace-only is fine — Prometheus handles metrics.
            _meter_provider = None
    except Exception as exc:  # pragma: no cover — exporter setup failure
        logger.warning("OTel setup failed: %s", exc)
        _otel_initialised = True
        return False

    _otel_initialised = True
    logger.info(
        "OTel exporter wired: endpoint=%s service=%s",
        settings.otel_endpoint,
        settings.otel_service_name,
    )
    return True


def get_meter() -> Any | None:
    """Return the global MeterProvider, or ``None`` if OTel is off."""
    if not _otel_initialised:
        # Lazy init on first read — avoids forcing every importer to
        # call ``setup_otel`` explicitly.
        setup_otel()
    return _meter_provider


# ─────────────────────── Prometheus registry ────────────────────────

# Standard latency buckets (seconds). Mirrors the defaults you'll find
# in most Grafana dashboards; chosen so swarm rounds (~0.1-5s) and tail
# LLM calls (~10s) all land in distinct buckets.
_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    """Canonical, hashable form of a label set."""
    if not labels:
        return ()
    # Sort so {a:1,b:2} and {b:2,a:1} hash identically.
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def _format_labels(labels_key: tuple[tuple[str, str], ...]) -> str:
    """Render label tuple in Prometheus exposition syntax."""
    if not labels_key:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in labels_key)
    return "{" + inner + "}"


def _escape(value: str) -> str:
    """Escape a label value per the Prometheus text format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class PrometheusRegistry:
    """Tiny, thread-safe Prometheus exposition registry.

    Supports counters and bucketed histograms only — that covers every
    Autonoma metric we'd want to expose today. Gauges can be added later;
    deliberately kept out for now.
    """

    def __init__(self, buckets: Iterable[float] = _DEFAULT_BUCKETS) -> None:
        self._lock = threading.Lock()
        # name → {label_key → float}
        self._counters: dict[str, dict[tuple, float]] = {}
        # name → {label_key → {"buckets": [count,...], "sum": float, "count": int}}
        self._histograms: dict[str, dict[tuple, dict[str, Any]]] = {}
        # name → help string
        self._help: dict[str, str] = {}
        self._buckets: tuple[float, ...] = tuple(buckets)

    # ── Recording API ──

    def inc(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        """Increment a counter by ``value`` (default 1)."""
        if value < 0:
            # Counters are monotonic by contract; silently clamp rather
            # than raise so a buggy call site never crashes the loop.
            return
        key = _labels_key(labels)
        with self._lock:
            bucket = self._counters.setdefault(name, {})
            bucket[key] = bucket.get(key, 0.0) + float(value)

    def observe(
        self,
        name: str,
        labels: dict[str, str] | None,
        value: float,
    ) -> None:
        """Record a single observation into a histogram."""
        key = _labels_key(labels)
        with self._lock:
            series = self._histograms.setdefault(name, {})
            entry = series.get(key)
            if entry is None:
                entry = {
                    "buckets": [0] * len(self._buckets),
                    "sum": 0.0,
                    "count": 0,
                }
                series[key] = entry
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    entry["buckets"][i] += 1
            entry["sum"] += float(value)
            entry["count"] += 1

    def set_help(self, name: str, text: str) -> None:
        """Attach a HELP description to a metric (optional)."""
        with self._lock:
            self._help[name] = text

    # ── Render ──

    def render(self) -> str:
        """Return the current snapshot as Prometheus text/plain output."""
        lines: list[str] = []
        with self._lock:
            for name in sorted(self._counters):
                help_text = self._help.get(name, f"{name} counter")
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} counter")
                for key, val in sorted(self._counters[name].items()):
                    lines.append(f"{name}{_format_labels(key)} {_fmt(val)}")

            for name in sorted(self._histograms):
                help_text = self._help.get(name, f"{name} histogram")
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} histogram")
                for key, entry in sorted(self._histograms[name].items()):
                    base_labels = list(key)
                    # ``entry["buckets"][i]`` is *already* the cumulative
                    # count of observations ≤ ``self._buckets[i]`` — see
                    # ``observe`` where we bump every bucket whose bound
                    # is ≥ the value. Don't accumulate again here.
                    for i, bound in enumerate(self._buckets):
                        bucket_labels = base_labels + [("le", _fmt(bound))]
                        lines.append(
                            f"{name}_bucket{_format_labels(tuple(bucket_labels))} {entry['buckets'][i]}"
                        )
                    inf_labels = base_labels + [("le", "+Inf")]
                    lines.append(
                        f"{name}_bucket{_format_labels(tuple(inf_labels))} {entry['count']}"
                    )
                    lines.append(
                        f"{name}_sum{_format_labels(key)} {_fmt(entry['sum'])}"
                    )
                    lines.append(
                        f"{name}_count{_format_labels(key)} {entry['count']}"
                    )

        # Trailing newline keeps Prometheus parser happy.
        return "\n".join(lines) + ("\n" if lines else "")


def _fmt(value: float) -> str:
    """Format a float the way Prometheus expects (no trailing zeros, +Inf etc.)."""
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if math.isnan(value):
        return "NaN"
    if float(value).is_integer():
        return str(int(value))
    # ``repr`` gives the shortest round-trippable representation.
    return repr(float(value))


# Module-level singleton — routers + helpers share it.
prom_registry = PrometheusRegistry()

# Pre-register HELP strings for the metrics emitted by the helpers
# below so the first scrape carries useful metadata even before any
# observations have been recorded.
prom_registry.set_help(
    "autonoma_round_duration_seconds",
    "Wall-clock duration of a single swarm round in seconds.",
)
prom_registry.set_help(
    "autonoma_llm_tokens_total",
    "Total LLM tokens consumed across rounds, partitioned by session.",
)
prom_registry.set_help(
    "autonoma_round_errors_total",
    "Round-level errors observed by the coordinator.",
)
prom_registry.set_help(
    "autonoma_anomaly_total",
    "Anomalies detected by the harness, partitioned by kind.",
)
prom_registry.set_help(
    "autonoma_sandbox_failure_total",
    "Sandbox execution failures, partitioned by reason.",
)


# ─────────────────── Convenience recorders ──────────────────────────


def record_round(
    session_id: Any,
    round_number: int,
    duration_s: float,
    llm_tokens: int = 0,
    errors: int = 0,
) -> None:
    """Record per-round telemetry. Safe to call from anywhere; never raises."""
    try:
        labels = {"session": str(session_id)}
        prom_registry.observe(
            "autonoma_round_duration_seconds", labels, float(duration_s)
        )
        if llm_tokens:
            prom_registry.inc(
                "autonoma_llm_tokens_total", labels, float(llm_tokens)
            )
        if errors:
            prom_registry.inc(
                "autonoma_round_errors_total", labels, float(errors)
            )
        # ``round_number`` is captured for log correlation but not
        # exposed as a label — high-cardinality labels are a known
        # Prometheus footgun.
        _ = round_number
    except Exception:  # pragma: no cover
        logger.debug("record_round suppressed exception", exc_info=True)


def record_anomaly(session_id: Any, kind: str) -> None:
    """Bump the anomaly counter for ``kind`` under a session."""
    try:
        prom_registry.inc(
            "autonoma_anomaly_total",
            {"session": str(session_id), "kind": str(kind)},
        )
    except Exception:  # pragma: no cover
        logger.debug("record_anomaly suppressed exception", exc_info=True)


def record_sandbox_failure(reason: str) -> None:
    """Bump the sandbox failure counter for ``reason``."""
    try:
        prom_registry.inc(
            "autonoma_sandbox_failure_total", {"reason": str(reason)}
        )
    except Exception:  # pragma: no cover
        logger.debug("record_sandbox_failure suppressed exception", exc_info=True)

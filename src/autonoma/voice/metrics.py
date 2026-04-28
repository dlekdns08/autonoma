"""In-process counters for ASR observability — feature #8.

Tiny module on purpose. The goal is "is the mic feature healthy right
now?" not full-fidelity Prometheus metrics — operators who want
percentile histograms in Grafana can scrape a real exporter. Here we
keep:

  * total transcribe calls / failures by stage (batch vs stream final
    vs stream partial)
  * a rolling window of recent latencies → on-demand p50 / p95
  * the timestamp of the most recent ASR error, with its message

State is process-local. A second uvicorn worker would have its own
counters; we run with --workers=1 (see Dockerfile.api comment) so
this is fine in the deployed topology.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# Window of recent latencies (ms) for percentile queries. ``deque`` so
# the rotation is O(1). 1024 is enough for stable p95 at our QPS.
_LATENCY_WINDOW: int = 1024


@dataclass
class _Counters:
    transcribe_total: int = 0
    transcribe_failures: int = 0
    partial_total: int = 0
    partial_failures: int = 0
    final_total: int = 0
    final_failures: int = 0
    last_error_ts: float = 0.0
    last_error_message: str = ""
    latencies_ms: deque[int] = field(default_factory=lambda: deque(maxlen=_LATENCY_WINDOW))


_state = _Counters()
_lock = threading.Lock()


def record_transcribe(
    *, stage: str, ok: bool, duration_ms: int, error: str | None = None
) -> None:
    """Hook called from the ASR codepath.

    ``stage`` is one of ``"batch"`` (single POST), ``"partial"`` (one
    rolling pass during streaming), ``"final"`` (the close-the-stream
    pass). We tally each separately so an operator can see whether
    failures cluster in partials (likely Cohere choking on truncated
    WebM) vs. finals (real transcribe issues).
    """
    with _lock:
        _state.transcribe_total += 1
        if duration_ms > 0:
            _state.latencies_ms.append(duration_ms)
        if not ok:
            _state.transcribe_failures += 1
            _state.last_error_ts = _now()
            _state.last_error_message = (error or "")[:200]
        if stage == "partial":
            _state.partial_total += 1
            if not ok:
                _state.partial_failures += 1
        elif stage == "final":
            _state.final_total += 1
            if not ok:
                _state.final_failures += 1
        # ``batch`` is implicit in the totals above; no per-stage extra.


def _now() -> float:
    import time as _t
    return _t.time()


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    sorted_vals = sorted(values)
    # Nearest-rank — adequate for an at-a-glance dashboard.
    k = max(0, min(len(sorted_vals) - 1, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def snapshot() -> dict[str, Any]:
    """Return a JSON-serialisable snapshot for the metrics endpoint."""
    with _lock:
        latencies = list(_state.latencies_ms)
        snap = {
            "transcribe_total": _state.transcribe_total,
            "transcribe_failures": _state.transcribe_failures,
            "partial_total": _state.partial_total,
            "partial_failures": _state.partial_failures,
            "final_total": _state.final_total,
            "final_failures": _state.final_failures,
            "last_error_ts": _state.last_error_ts,
            "last_error_message": _state.last_error_message,
        }
    snap["latency_count"] = len(latencies)
    snap["latency_p50_ms"] = _percentile(latencies, 50)
    snap["latency_p95_ms"] = _percentile(latencies, 95)
    snap["latency_max_ms"] = max(latencies) if latencies else 0
    return snap


def reset_for_tests() -> None:
    global _state
    with _lock:
        _state = _Counters()

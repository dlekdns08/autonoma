"""Tests for ``observability_otel`` — feature #10."""

from __future__ import annotations

import pytest

from autonoma.config import settings
from autonoma.observability_otel import (
    PrometheusRegistry,
    record_anomaly,
    record_round,
    record_sandbox_failure,
    setup_otel,
)

# ─────────────────── PrometheusRegistry behaviour ────────────────────


def test_registry_inc_and_render_counter() -> None:
    reg = PrometheusRegistry()
    reg.set_help("widgets_total", "Number of widgets emitted")
    reg.inc("widgets_total", {"color": "blue"})
    reg.inc("widgets_total", {"color": "blue"}, value=2)
    reg.inc("widgets_total", {"color": "red"})

    out = reg.render()

    # Metadata lines.
    assert "# TYPE widgets_total counter" in out
    assert "# HELP widgets_total Number of widgets emitted" in out
    # Sample lines (sorted: blue=3 listed before red=1).
    assert 'widgets_total{color="blue"} 3' in out
    assert 'widgets_total{color="red"} 1' in out


def test_registry_inc_negative_is_clamped() -> None:
    reg = PrometheusRegistry()
    reg.inc("c_total", value=5)
    reg.inc("c_total", value=-3)  # ignored — counters are monotonic
    out = reg.render()
    assert "c_total 5" in out


def test_registry_observe_emits_buckets_sum_count() -> None:
    reg = PrometheusRegistry()
    reg.set_help("latency_seconds", "Latency")
    # Three observations across the standard bucket bounds.
    for v in (0.04, 0.4, 7.0):
        reg.observe("latency_seconds", {"path": "/x"}, v)

    out = reg.render()

    assert "# TYPE latency_seconds histogram" in out
    # Cumulative bucket counts: 0.04 hits le=0.05+; 0.4 hits le=0.5+; 7.0 hits le=10+.
    assert 'latency_seconds_bucket{path="/x",le="0.05"} 1' in out
    assert 'latency_seconds_bucket{path="/x",le="0.5"} 2' in out
    assert 'latency_seconds_bucket{path="/x",le="10"} 3' in out
    assert 'latency_seconds_bucket{path="/x",le="+Inf"} 3' in out
    assert 'latency_seconds_count{path="/x"} 3' in out
    # Sum should equal 0.04+0.4+7.0 = 7.44 — formatted via repr.
    assert 'latency_seconds_sum{path="/x"}' in out


def test_registry_label_value_escaping() -> None:
    reg = PrometheusRegistry()
    reg.inc("tricky_total", {"path": 'a"b\\c'})
    out = reg.render()
    # Backslashes and quotes must be escaped per the text-format spec.
    assert 'tricky_total{path="a\\"b\\\\c"} 1' in out


def test_render_without_data_is_empty_string() -> None:
    reg = PrometheusRegistry()
    assert reg.render() == ""


# ─────────────────── Module-level recorders ─────────────────────────


def test_record_round_observes_duration_and_tokens() -> None:
    from autonoma.observability_otel import prom_registry

    record_round(session_id="sess-A", round_number=3, duration_s=0.42, llm_tokens=120)
    out = prom_registry.render()

    # The shared registry survives between tests, so just assert our
    # values landed somewhere — both metric families and the right
    # session label are present.
    assert "autonoma_round_duration_seconds" in out
    assert "autonoma_llm_tokens_total" in out
    assert 'session="sess-A"' in out


def test_record_anomaly_and_sandbox_failure_increment_counters() -> None:
    from autonoma.observability_otel import prom_registry

    record_anomaly("sess-B", "stall")
    record_sandbox_failure("oom")
    out = prom_registry.render()
    assert "autonoma_anomaly_total" in out
    assert 'kind="stall"' in out
    assert "autonoma_sandbox_failure_total" in out
    assert 'reason="oom"' in out


# ───────────────────────── setup_otel ───────────────────────────────


def test_setup_otel_returns_false_when_endpoint_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "otel_endpoint", "", raising=False)
    # Reset the module-level "already initialised" flag so this test
    # exercises the empty-endpoint branch regardless of test order.
    import autonoma.observability_otel as obs

    monkeypatch.setattr(obs, "_otel_initialised", False, raising=False)
    monkeypatch.setattr(obs, "_meter_provider", None, raising=False)

    assert setup_otel() is False

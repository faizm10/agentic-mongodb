"""Uptime maths from recorded samples, missing data, and the incident lifecycle."""

import asyncio
from datetime import timedelta

import pytest

from uptime.models import Sample
from uptime.uptime_calc import compute_uptime, latency_series, uptime_bars

from .conftest import NOVAMART, T0


def samples(start, statuses, interval=30, latency=50.0):
    return [Sample(checked_at=start + timedelta(seconds=interval * i), status=s, latency_ms=latency,
                   functional_latency_ms=latency * 2, interval_seconds=interval) for i, s in enumerate(statuses)]


# ------------------------------------------------------------------- uptime
def test_full_day_all_operational_is_100_and_sufficient():
    now = T0
    data = samples(now - timedelta(hours=24), ["operational"] * 2880)
    stats = compute_uptime(data, "24h", now, data[0].checked_at)
    assert stats["uptime_pct"] == 100.0 and stats["sufficient"] is True
    assert stats["coverage_pct"] == pytest.approx(100.0, abs=0.1)


def test_degraded_time_lowers_uptime_but_not_availability():
    now = T0
    data = samples(now - timedelta(hours=24), ["operational"] * 2160 + ["degraded"] * 720)  # last 6 h degraded
    stats = compute_uptime(data, "24h", now, data[0].checked_at)
    assert stats["uptime_pct"] == pytest.approx(75.0, abs=0.1)
    assert stats["availability_pct"] == pytest.approx(100.0, abs=0.1)
    assert stats["degraded_seconds"] == pytest.approx(6 * 3600, abs=60)


def test_short_history_is_never_reported_as_full_30_day_uptime():
    now = T0
    data = samples(now - timedelta(minutes=10), ["operational"] * 20)
    stats = compute_uptime(data, "30d", now, data[0].checked_at)
    assert stats["sufficient"] is False
    assert stats["coverage_pct"] < 0.1
    assert stats["observed_seconds"] == pytest.approx(600, abs=1)
    assert stats["unknown_seconds"] > 29 * 86400, "time before monitoring began is unknown, not uptime"


def test_gaps_are_unknown_not_uptime():
    now = T0
    first = samples(now - timedelta(hours=2), ["operational"] * 60)    # 30 min of data
    second = samples(now - timedelta(minutes=30), ["operational"] * 60)  # 1 h gap, then 30 min
    stats = compute_uptime(first + second, "24h", now, first[0].checked_at)
    assert stats["observed_seconds"] == pytest.approx(3600, abs=60)
    assert stats["uptime_pct"] == 100.0 and stats["sufficient"] is False


def test_unknown_samples_are_excluded_and_down_counts_as_downtime():
    now = T0
    data = samples(now - timedelta(minutes=40), ["operational"] * 40 + ["down"] * 20 + ["unknown"] * 20)
    stats = compute_uptime(data, "24h", now, data[0].checked_at)
    assert stats["down_seconds"] == pytest.approx(600, abs=1)
    assert stats["uptime_pct"] == pytest.approx(66.667, abs=0.1)


def test_no_data_at_all():
    stats = compute_uptime([], "7d", T0, None)
    assert stats["uptime_pct"] is None and stats["observed_seconds"] == 0 and stats["sufficient"] is False


def test_bars_show_unknown_before_monitoring_and_worst_state_per_bucket():
    now = T0
    data = samples(now - timedelta(minutes=30), ["operational"] * 50 + ["down"] + ["operational"] * 9)
    bars = uptime_bars(data, "24h", now)
    assert len(bars) == 96
    assert all(b["status"] == "unknown" and b["uptime_pct"] is None for b in bars[:-2])
    assert bars[-1]["status"] == "down" and bars[-1]["failed_checks"] == 1
    assert bars[-2]["status"] == "operational"


def test_latency_series_excludes_down_samples_and_leaves_empty_buckets_null():
    now = T0
    data = samples(now - timedelta(minutes=10), ["operational"] * 10 + ["down"] * 10, latency=80.0)
    series = latency_series(data, "24h", now, points=96)
    assert series[0]["availability_avg_ms"] is None
    assert series[-1]["availability_avg_ms"] == 80.0
    assert sum(p["samples"] for p in series) == 10


# ---------------------------------------------------------------- incidents
def cycle(monitor, clock, seconds=30):
    result = asyncio.run(monitor.run_cycle())
    clock.advance(seconds)
    return result


def test_ongoing_outage_opens_exactly_one_incident(storage, monitor_factory, clock, apps):
    monitor = monitor_factory(storage)
    apps.mode["novamart"] = "functional_bug"
    for _ in range(4):
        cycle(monitor, clock)
    incidents = storage.list_incidents(service_id="novamart")
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.state == "open" and inc.failed_check_count == 4 and inc.severity == "degraded"
    assert inc.investigation_route == "investigate"
    assert storage.list_incidents(service_id="novaconnect") == []


def test_storage_rejects_a_second_open_incident(storage, monitor_factory, clock, apps):
    apps.mode["novamart"] = "backend_down"
    cycle(monitor_factory(storage), clock)
    existing = storage.get_open_incident("novamart")
    duplicate = existing.model_copy(update={"id": "UPT-novamart-duplicate"})
    assert storage.create_incident(duplicate) is False


def test_escalation_degraded_to_down_keeps_one_incident(storage, monitor_factory, clock, apps):
    monitor = monitor_factory(storage)
    apps.mode["novamart"] = "functional_bug"
    cycle(monitor, clock)
    apps.mode["novamart"] = "backend_down"
    cycle(monitor, clock)
    inc = storage.get_open_incident("novamart")
    assert inc.severity == "down" and inc.peak_severity == "down"
    assert [e.type for e in inc.events] == ["opened", "escalated", "failure_changed"]
    assert inc.investigation_route == "service_health"


def test_recovery_resolves_incident_with_duration_and_new_failure_opens_new_one(storage, monitor_factory, clock, apps):
    monitor = monitor_factory(storage)
    apps.mode["novamart"] = "functional_bug"
    cycle(monitor, clock)
    cycle(monitor, clock)
    apps.mode["novamart"] = "healthy"
    cycle(monitor, clock)
    resolved = storage.list_incidents(service_id="novamart")[0]
    assert resolved.state == "resolved"
    assert resolved.elapsed_seconds(clock()) == 60, "two 30 s failing intervals"
    assert resolved.events[-1].type == "resolved"
    assert storage.get_state("novamart").status == "operational"
    apps.mode["novamart"] = "backend_down"
    cycle(monitor, clock)
    assert len(storage.list_incidents(service_id="novamart")) == 2


def test_failure_threshold_requires_consecutive_failures(storage, monitor_factory, clock, apps):
    service = NOVAMART.model_copy(update={"failure_threshold": 2})
    monitor = monitor_factory(storage, services=[service])
    apps.mode["novamart"] = "functional_bug"
    cycle(monitor, clock)
    assert storage.get_open_incident("novamart") is None
    cycle(monitor, clock)
    assert storage.get_open_incident("novamart") is not None

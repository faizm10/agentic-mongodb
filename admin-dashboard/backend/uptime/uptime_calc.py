"""Observed uptime from recorded checks.

Methodology
-----------
* Each check sample vouches for the service state from its timestamp until the next sample,
  but for at most ``MAX_GAP_FACTOR x interval`` (1.5 x 30 s = 45 s). Beyond that the time is a
  **gap** and counts as unknown, never as uptime. The newest sample vouches up to "now" under
  the same cap.
* Time before monitoring began, gaps, and samples whose status is ``unknown`` are excluded from
  the denominator. ``observed = operational + degraded + down`` seconds.
* ``uptime_pct      = operational / observed``  (fully working, including the business workflow)
  ``availability_pct = (operational + degraded) / observed``  (website reachable)
* ``coverage_pct = observed / window``. Below ``SUFFICIENT_COVERAGE`` (90%) the window is flagged
  ``sufficient = False`` and the dashboard says "insufficient history" instead of a bare percentage,
  so a few minutes of data is never shown as "100% over 30 days".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .models import Sample

MAX_GAP_FACTOR = 1.5
SUFFICIENT_COVERAGE = 0.90
WINDOWS: dict[str, tuple[timedelta, int]] = {
    # window -> (length, number of timeline bars)
    "24h": (timedelta(hours=24), 96),   # 15-minute bars
    "7d": (timedelta(days=7), 84),      # 2-hour bars
    "30d": (timedelta(days=30), 90),    # 8-hour bars
}
SEVERITY = {"operational": 0, "degraded": 1, "down": 2}


@dataclass(frozen=True)
class Segment:
    start: datetime
    end: datetime
    status: str


def segments(samples: list[Sample], window_start: datetime, window_end: datetime) -> list[Segment]:
    """Covered time intervals (clipped to the window). Everything else in the window is a gap."""
    out: list[Segment] = []
    for i, s in enumerate(samples):
        cap = s.checked_at + timedelta(seconds=MAX_GAP_FACTOR * s.interval_seconds)
        nxt = samples[i + 1].checked_at if i + 1 < len(samples) else window_end
        start, end = max(s.checked_at, window_start), min(nxt, cap, window_end)
        if end > start and s.status != "unknown":
            out.append(Segment(start, end, s.status))
    return out


def window_bounds(window: str, now: datetime) -> tuple[datetime, datetime]:
    length, _ = WINDOWS[window]
    return now - length, now


def needed_since(window: str, now: datetime, interval_seconds: int) -> datetime:
    """Fetch slightly before the window: a sample just before it can still cover its start."""
    start, _ = window_bounds(window, now)
    return start - timedelta(seconds=MAX_GAP_FACTOR * interval_seconds)


def compute_uptime(samples: list[Sample], window: str, now: datetime, first_check_at: datetime | None) -> dict[str, Any]:
    start, end = window_bounds(window, now)
    window_s = (end - start).total_seconds()
    totals = {"operational": 0.0, "degraded": 0.0, "down": 0.0}
    for seg in segments(samples, start, end):
        totals[seg.status] += (seg.end - seg.start).total_seconds()
    observed = sum(totals.values())
    in_window = [s for s in samples if start <= s.checked_at <= end]
    latencies = sorted(s.latency_ms for s in in_window if s.latency_ms is not None and s.status != "down")
    flatencies = [s.functional_latency_ms for s in in_window if s.functional_latency_ms is not None and s.status != "down"]
    coverage = observed / window_s if window_s else 0.0
    return {
        "window": window,
        "window_start": start,
        "window_end": end,
        "window_seconds": window_s,
        "observed_seconds": round(observed, 3),
        "operational_seconds": round(totals["operational"], 3),
        "degraded_seconds": round(totals["degraded"], 3),
        "down_seconds": round(totals["down"], 3),
        "unknown_seconds": round(window_s - observed, 3),
        "coverage_pct": round(coverage * 100, 3),
        "sufficient": coverage >= SUFFICIENT_COVERAGE,
        "uptime_pct": round(totals["operational"] / observed * 100, 3) if observed else None,
        "availability_pct": round((totals["operational"] + totals["degraded"]) / observed * 100, 3) if observed else None,
        "check_count": len(in_window),
        "failed_check_count": sum(1 for s in in_window if s.status in ("degraded", "down")),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
        "p95_latency_ms": round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))], 2) if latencies else None,
        "avg_functional_latency_ms": round(sum(flatencies) / len(flatencies), 2) if flatencies else None,
        "monitoring_started_at": first_check_at,
    }


def uptime_bars(samples: list[Sample], window: str, now: datetime) -> list[dict[str, Any]]:
    """Fixed-size timeline buckets. A bucket's status is the worst state observed in it; a bucket
    with no observed time is 'unknown' (shown as 'no data', never as healthy)."""
    start, end = window_bounds(window, now)
    _, count = WINDOWS[window]
    size = (end - start) / count
    size_s = size.total_seconds()
    buckets = [{"op": 0.0, "deg": 0.0, "down": 0.0, "worst": -1, "checks": 0, "failures": 0} for _ in range(count)]

    for seg in segments(samples, start, end):
        first = max(0, int((seg.start - start) / size))
        last = min(count - 1, int((seg.end - start - timedelta(microseconds=1)) / size))
        for b in range(first, last + 1):
            b_start = start + size * b
            overlap = (min(seg.end, b_start + size) - max(seg.start, b_start)).total_seconds()
            if overlap <= 0:
                continue
            key = {"operational": "op", "degraded": "deg", "down": "down"}[seg.status]
            buckets[b][key] += overlap
            buckets[b]["worst"] = max(buckets[b]["worst"], SEVERITY[seg.status])
    for s in samples:
        if start <= s.checked_at < end:
            b = min(count - 1, int((s.checked_at - start) / size))
            buckets[b]["checks"] += 1
            buckets[b]["failures"] += s.status in ("degraded", "down")

    out = []
    names = {-1: "unknown", 0: "operational", 1: "degraded", 2: "down"}
    for i, b in enumerate(buckets):
        observed = b["op"] + b["deg"] + b["down"]
        out.append({
            "start": start + size * i,
            "end": start + size * (i + 1),
            "status": names[b["worst"]],
            "uptime_pct": round(b["op"] / observed * 100, 2) if observed else None,
            "coverage_pct": round(observed / size_s * 100, 2),
            "operational_seconds": round(b["op"], 1),
            "degraded_seconds": round(b["deg"], 1),
            "down_seconds": round(b["down"], 1),
            "checks": b["checks"],
            "failed_checks": b["failures"],
        })
    return out


def latency_series(samples: list[Sample], window: str, now: datetime, points: int = 120) -> list[dict[str, Any]]:
    """Average/max latency per bucket (successful responses only). Empty buckets are null, not 0."""
    start, end = window_bounds(window, now)
    size = (end - start) / points
    acc = [{"a": [], "f": []} for _ in range(points)]
    for s in samples:
        if not (start <= s.checked_at < end) or s.status == "down":
            continue
        b = min(points - 1, int((s.checked_at - start) / size))
        if s.latency_ms is not None:
            acc[b]["a"].append(s.latency_ms)
        if s.functional_latency_ms is not None:
            acc[b]["f"].append(s.functional_latency_ms)
    series = []
    for i, b in enumerate(acc):
        series.append({
            "start": start + size * i,
            "availability_avg_ms": round(sum(b["a"]) / len(b["a"]), 2) if b["a"] else None,
            "availability_max_ms": round(max(b["a"]), 2) if b["a"] else None,
            "functional_avg_ms": round(sum(b["f"]) / len(b["f"]), 2) if b["f"] else None,
            "functional_max_ms": round(max(b["f"]), 2) if b["f"] else None,
            "samples": len(b["a"]),
        })
    return series

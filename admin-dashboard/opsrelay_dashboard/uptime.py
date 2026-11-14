"""Health-endpoint uptime checks with a rolling in-memory history and a JSONL log."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

DAY = 24 * 3600
HOUR = 3600
RECENT_SAMPLES = 60


@dataclass
class Sample:
    app: str
    ts: float
    up: bool
    latency_ms: float | None
    status_code: int | None
    error: str | None = None


@dataclass
class Target:
    app: str
    url: str
    interval_seconds: float = 15.0
    timeout_seconds: float = 5.0


CheckFn = Callable[[str, float], tuple[int | None, float | None, str | None]]


def http_check(url: str, timeout: float) -> tuple[int | None, float | None, str | None]:
    """GET ``url``; return (status code, latency ms, error). Any 2xx/3xx counts as up."""
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "opsrelay-uptime/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL comes from config
            response.read(1024)
            return response.status, round((time.perf_counter() - started) * 1000, 1), None
    except urllib.error.HTTPError as exc:
        return exc.code, round((time.perf_counter() - started) * 1000, 1), f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, None, str(reason)[:200]


class UptimeMonitor:
    def __init__(
        self,
        targets: list[Target],
        log_path: Path | None = None,
        check: CheckFn = http_check,
        clock: Callable[[], float] = time.time,
    ):
        self.targets = {t.app: t for t in targets}
        self.log_path = log_path
        self._check = check
        self._clock = clock
        self._lock = threading.Lock()
        self._samples: dict[str, deque[Sample]] = {t.app: deque() for t in targets}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load_log()

    # --- checking -----------------------------------------------------------

    def check_once(self, app: str) -> Sample:
        target = self.targets[app]
        status, latency, error = self._check(target.url, target.timeout_seconds)
        up = status is not None and 200 <= status < 400
        sample = Sample(app, self._clock(), up, latency, status, None if up else error)
        self._record(sample)
        return sample

    def check_all(self) -> list[Sample]:
        return [self.check_once(app) for app in self.targets]

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="uptime-monitor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        due = {app: 0.0 for app in self.targets}
        while not self._stop.is_set():
            now = time.monotonic()
            for app, target in self.targets.items():
                if now >= due[app]:
                    self.check_once(app)
                    due[app] = now + target.interval_seconds
            self._stop.wait(min(1.0, min(t.interval_seconds for t in self.targets.values())))

    # --- storage ------------------------------------------------------------

    def _record(self, sample: Sample) -> None:
        with self._lock:
            history = self._samples.setdefault(sample.app, deque())
            history.append(sample)
            cutoff = sample.ts - DAY
            while history and history[0].ts < cutoff:
                history.popleft()
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(json.dumps(asdict(sample)) + "\n")

    def _load_log(self) -> None:
        if self.log_path is None or not self.log_path.exists():
            return
        cutoff = self._clock() - DAY
        for line in self.log_path.read_text().splitlines():
            try:
                sample = Sample(**json.loads(line))
            except (ValueError, TypeError):
                continue
            if sample.app in self._samples and sample.ts >= cutoff:
                self._samples[sample.app].append(sample)

    # --- reporting ----------------------------------------------------------

    def summary(self, app: str) -> dict:
        now = self._clock()
        with self._lock:
            samples = list(self._samples.get(app, ()))
        if not samples:
            return {"status": "unknown", "checks_24h": 0, "recent": [], "url": self.targets[app].url}

        last = samples[-1]
        since = last.ts
        for sample in reversed(samples):
            if sample.up != last.up:
                break
            since = sample.ts
        hour = [s for s in samples if s.ts >= now - HOUR]
        latencies = sorted(s.latency_ms for s in hour if s.up and s.latency_ms is not None)
        last_down = next((s for s in reversed(samples) if not s.up), None)
        return {
            "status": "up" if last.up else "down",
            "url": self.targets[app].url,
            "since": since,
            "last_checked": last.ts,
            "latency_ms": last.latency_ms,
            "latency_p50_1h": _percentile(latencies, 50),
            "latency_p95_1h": _percentile(latencies, 95),
            "uptime_1h": _uptime(hour),
            "uptime_24h": _uptime(samples),
            "checks_24h": len(samples),
            "last_error": last_down.error if last_down else None,
            "last_down_at": last_down.ts if last_down else None,
            "recent": [
                {"ts": s.ts, "up": s.up, "latency_ms": s.latency_ms, "status_code": s.status_code, "error": s.error}
                for s in samples[-RECENT_SAMPLES:]
            ],
        }


def _uptime(samples: list[Sample]) -> float | None:
    return round(100 * sum(s.up for s in samples) / len(samples), 2) if samples else None


def _percentile(sorted_values: list[float], pct: int) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, round(pct / 100 * len(sorted_values) + 0.5) - 1))
    return sorted_values[index]

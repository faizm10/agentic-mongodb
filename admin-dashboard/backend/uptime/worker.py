"""The monitoring worker.

Scheduling
----------
* Runs a cycle **immediately** on start, then on every interval boundary (slots aligned to
  ``interval_seconds``, 30 s by default).
* All services are probed **concurrently** (``asyncio.gather``); within a service the frontend,
  availability and functional probes also run concurrently. One service failing, hanging or
  raising never stops the others: every probe has a timeout and the cycle catches everything.
* Cycles **never overlap**: the loop is sequential and a lock guards manual triggers. If a cycle
  overruns, the missed slots are skipped and counted, never queued up.
* Each scheduled check id is ``<service>:<slot>``, so a worker restarted inside the same slot
  cannot record that slot twice (the insert is rejected as a duplicate).
* A **lease** in storage means only one worker polls at a time. A crashed worker's lease expires
  after ``2 x interval + 10`` s; a cleanly stopped worker releases it immediately.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from . import incidents as incident_engine
from . import netguard
from .classify import classify
from .models import CheckRecord, IncidentEvent, ProbeResult, ServiceConfig
from .probes import probe_availability, probe_frontend, probe_functional
from .storage.base import Storage, iso

logger = logging.getLogger("opsrelay.uptime")
Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]
EventHook = Callable[[ServiceConfig, IncidentEvent, CheckRecord], Awaitable[None] | None]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _skipped() -> None:
    """A probe the service has no URL for."""
    return None


def slot_for(now: datetime, interval: int) -> datetime:
    epoch = now.timestamp()
    return datetime.fromtimestamp(math.floor(epoch / interval) * interval, tz=timezone.utc)


class UptimeMonitor:
    def __init__(
        self,
        storage: Storage,
        services: list[ServiceConfig],
        *,
        interval_seconds: int = 30,
        lease_ttl_seconds: int | None = None,
        retention_days: int = 35,
        incident_retention_days: int = 180,
        clock: Clock = utcnow,
        sleep: Sleep = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
        worker_id: str | None = None,
        on_incident_event: EventHook | None = None,
    ) -> None:
        self.storage = storage
        self.config_services = services
        self.interval = interval_seconds
        self.lease_ttl = lease_ttl_seconds or (2 * interval_seconds + 10)
        self.retention_days = retention_days
        self.incident_retention_days = incident_retention_days
        self.clock = clock
        self.sleep = sleep
        self.transport = transport
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.on_incident_event = on_incident_event
        self._cycle_lock = asyncio.Lock()
        self._last_purge: datetime | None = None
        self.has_lease = False
        self.cycles = 0
        self.register_services()

    # ---------------------------------------------------------------- probing
    def services(self) -> list[ServiceConfig]:
        """Storage is the source of truth: every workspace's sites, plus any from the config file."""
        return self.storage.list_services()

    def register_services(self) -> None:
        for service in self.config_services:
            self.storage.upsert_service(service.model_copy(update={"interval_seconds": self.interval}))

    async def check_service(self, client: httpx.AsyncClient, service: ServiceConfig, slot: datetime, *, scheduled: bool = True) -> CheckRecord:
        started = self.clock()
        check_id = f"{service.id}:{iso(slot)}" if scheduled else f"{service.id}:{iso(started)}:manual"
        try:
            frontend, availability, functional = await asyncio.gather(
                probe_frontend(client, service),
                probe_availability(client, service) if service.availability_url else _skipped(),
                probe_functional(client, service) if service.functional_url else _skipped(),
            )
            status, failure_type, summary = classify(frontend, availability, functional)
        except Exception as exc:  # noqa: BLE001 - a monitor bug must surface as 'unknown', not crash the loop
            logger.exception("check of %s failed inside the monitor", service.id)
            frontend = availability = functional = None
            status, failure_type, summary = "unknown", "monitor_error", f"Monitor error: {type(exc).__name__}: {exc}"
        return CheckRecord(
            id=check_id, service_id=service.id, slot=slot, checked_at=started, scheduled=scheduled,
            status=status, failure_type=failure_type, summary=summary,
            # Response time is the health endpoint's, or the page's for a site without one.
            latency_ms=next((p.latency_ms for p in (availability or frontend,) if isinstance(p, ProbeResult) and p.ok), None),
            functional_latency_ms=functional.latency_ms if isinstance(functional, ProbeResult) and functional.http_status else None,
            frontend=frontend, availability=availability, functional=functional,
            interval_seconds=self.interval, worker_id=self.worker_id,
        )

    async def run_cycle(self, *, scheduled: bool = True, workspace_id: str | None = None) -> dict[str, Any] | None:
        """Check every enabled service (or one workspace's) once, concurrently. None if a cycle is already running."""
        if self._cycle_lock.locked():
            return None
        async with self._cycle_lock:
            started_wall = self.clock()
            t0 = time.perf_counter()
            slot = slot_for(started_wall, self.interval)
            services = [s for s in self.services() if s.enabled and workspace_id in (None, s.workspace_id)]
            async with httpx.AsyncClient(transport=self.transport, follow_redirects=True,
                                         headers={"User-Agent": "opsrelay-uptime/1.0"},
                                         event_hooks={"request": [netguard.guard_request]}) as client:
                records = await asyncio.gather(*(self.check_service(client, s, slot, scheduled=scheduled) for s in services))

            recorded, duplicates, events = [], 0, []
            for service, record in zip(services, records):
                if not self.storage.insert_check(record):
                    duplicates += 1  # this slot was already recorded (e.g. before a restart)
                    continue
                recorded.append(record)
                for event in incident_engine.apply_check(self.storage, service, record):
                    events.append((service, event, record))
            for service, event, record in events:
                logger.warning("incident %s %s: %s", service.id, event.type, event.message)
                if self.on_incident_event:
                    try:
                        result = self.on_incident_event(service, event, record)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:  # noqa: BLE001 - integrations must not break monitoring
                        logger.exception("incident hook failed")

            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            self.cycles += 1
            worker = self.storage.get_worker()
            self.storage.update_worker(self.worker_id, {
                "last_cycle_started_at": started_wall,
                "last_cycle_finished_at": self.clock(),
                "last_cycle_duration_ms": duration_ms,
                "heartbeat_at": self.clock(),
                "cycles_completed": (worker.cycles_completed if worker else 0) + 1,
                "duplicate_slots": (worker.duplicate_slots if worker else 0) + duplicates,
                "interval_seconds": self.interval,
            })
            self._maybe_purge()
            return {"slot": slot, "started_at": started_wall, "duration_ms": duration_ms,
                    "recorded": [r.id for r in recorded], "duplicates": duplicates,
                    "statuses": {r.service_id: r.status for r in recorded},
                    "incident_events": [(s.id, e.type) for s, e, _ in events]}

    def _maybe_purge(self) -> None:
        now = self.clock()
        if self._last_purge and now - self._last_purge < timedelta(hours=1):
            return
        self._last_purge = now
        self.storage.purge(now - timedelta(days=self.retention_days), now - timedelta(days=self.incident_retention_days))

    # --------------------------------------------------------------- the loop
    async def _sleep_until(self, target: datetime, stop: asyncio.Event) -> bool:
        """Sleep until target in short steps; return True early if a manual check was requested."""
        while not stop.is_set():
            remaining = (target - self.clock()).total_seconds()
            if remaining <= 0:
                return False
            await self.sleep(min(remaining, 1.0))
            worker = self.storage.get_worker()
            if worker and worker.check_requested_at and (
                worker.last_cycle_started_at is None or worker.check_requested_at > worker.last_cycle_started_at
            ):
                return True
        return False

    async def run_forever(self, stop: asyncio.Event) -> None:
        logger.info("uptime worker %s starting: interval=%ss storage=%s", self.worker_id, self.interval,
                    self.storage.describe()["label"])
        while not stop.is_set():
            now = self.clock()
            if not self.storage.acquire_lease(self.worker_id, now, self.lease_ttl):
                if self.has_lease:
                    logger.warning("lost the monitoring lease; standing by")
                self.has_lease = False
                await self._sleep_until(now + timedelta(seconds=min(self.interval, 5)), stop)
                continue
            if not self.has_lease:
                self.has_lease = True
                self.storage.update_worker(self.worker_id, {"started_at": now, "interval_seconds": self.interval})
                logger.info("acquired monitoring lease; first cycle runs now")

            result = await self.run_cycle()
            if result:
                logger.info("cycle %s: %s (%.0f ms)", iso(result["slot"]), result["statuses"], result["duration_ms"])

            # Next scheduled slot strictly after now; overrun slots are skipped, never run back to back.
            after = self.clock()
            next_slot = slot_for(after, self.interval) + timedelta(seconds=self.interval)
            if result:
                missed = int((slot_for(after, self.interval) - result["slot"]).total_seconds() // self.interval)
                if missed > 0:
                    worker = self.storage.get_worker()
                    self.storage.update_worker(self.worker_id, {"skipped_slots": (worker.skipped_slots if worker else 0) + missed})
            self.storage.acquire_lease(self.worker_id, self.clock(), self.lease_ttl)  # renew
            while await self._sleep_until(next_slot, stop):  # manual "check now" requests between slots
                logger.info("manual check requested")
                await self.run_cycle(scheduled=False)
        self.storage.release_lease(self.worker_id)
        logger.info("uptime worker %s stopped and released its lease", self.worker_id)

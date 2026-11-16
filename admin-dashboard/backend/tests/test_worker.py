"""Scheduling: immediate first check, 30 s cadence, concurrency, no overlap, resilience, restart."""

import asyncio
import time

import pytest

from uptime.config import load_settings
from uptime.worker import UptimeMonitor, slot_for

from .conftest import SERVICES, T0


def test_default_interval_is_5_minutes_and_overridable(monkeypatch):
    monkeypatch.delenv("OPSRELAY_MONITOR_INTERVAL", raising=False)
    settings = load_settings()
    assert settings.interval_seconds == 300 and settings.services == []
    monkeypatch.setenv("OPSRELAY_MONITOR_INTERVAL", "2")
    assert load_settings().interval_seconds == 2


def test_first_cycle_runs_immediately_then_every_30_seconds(storage, monitor_factory, clock):
    clock.advance(7)  # start mid-slot (12:00:07)
    monitor = monitor_factory(storage)
    starts = []
    original = monitor.run_cycle

    async def recording(**kw):
        starts.append(clock())
        return await original(**kw)

    monitor.run_cycle = recording

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_forever(stop))
        while len(starts) < 4:
            await asyncio.sleep(0)
        stop.set()
        await task

    asyncio.run(go())
    offsets = [(s - T0).total_seconds() for s in starts]
    assert offsets[0] == 7, "first check must run immediately on startup"
    gaps = [b - a for a, b in zip(offsets[1:], offsets[2:])]
    assert offsets[1] == 30 and gaps == [30, 30], f"cycle start offsets {offsets}"
    assert storage.count_checks() == 8  # 4 cycles x 2 services


def test_services_are_checked_concurrently(storage, monitor_factory, apps):
    apps.mode = {"novamart": "slow:0.4", "novaconnect": "slow:0.4"}
    monitor = monitor_factory(storage)
    started = time.perf_counter()
    result = asyncio.run(monitor.run_cycle())
    elapsed = time.perf_counter() - started
    assert set(result["statuses"]) == {"novamart", "novaconnect"}
    assert elapsed < 0.75, f"two 0.4 s services took {elapsed:.2f}s; they must run concurrently"


def test_cycles_never_overlap(storage, monitor_factory, apps):
    apps.mode = {"novamart": "slow:0.3", "novaconnect": "healthy"}
    monitor = monitor_factory(storage)

    async def go():
        first = asyncio.create_task(monitor.run_cycle())
        await asyncio.sleep(0.05)
        second = await monitor.run_cycle()  # while the first is still running
        return await first, second

    first, second = asyncio.run(go())
    assert first is not None and second is None
    assert storage.count_checks() == 2


def test_one_service_down_does_not_stop_the_other(storage, monitor_factory, apps):
    apps.mode["novaconnect"] = "backend_down"
    result = asyncio.run(monitor_factory(storage).run_cycle())
    assert result["statuses"] == {"novamart": "operational", "novaconnect": "down"}


def test_monitor_bug_is_recorded_as_unknown_not_a_crash(storage, monitor_factory, monkeypatch):
    import uptime.worker as worker_mod

    def boom(*_):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(worker_mod, "classify", boom)
    result = asyncio.run(monitor_factory(storage).run_cycle())
    assert result["statuses"] == {"novamart": "unknown", "novaconnect": "unknown"}
    assert storage.get_open_incident("novamart") is None, "unknown results never open incidents"


def test_restart_in_the_same_slot_does_not_duplicate_checks(storage, monitor_factory, clock):
    asyncio.run(monitor_factory(storage, worker_id="A").run_cycle())
    clock.advance(10)  # still the same 30 s slot
    again = asyncio.run(monitor_factory(storage, worker_id="B").run_cycle())
    assert again["duplicates"] == 2 and again["recorded"] == []
    assert storage.count_checks() == 2


def test_crashed_worker_lease_expires_then_new_worker_resumes(storage, monitor_factory, clock, apps):
    a = monitor_factory(storage, worker_id="A")
    assert storage.acquire_lease("A", clock(), a.lease_ttl)
    apps.mode["novamart"] = "functional_bug"
    asyncio.run(a.run_cycle())
    open_before = storage.get_open_incident("novamart")
    assert open_before is not None
    # A crashes without releasing its lease. B cannot take over until it expires.
    clock.advance(30)
    assert not storage.acquire_lease("B", clock(), a.lease_ttl)
    clock.advance(a.lease_ttl)
    assert storage.acquire_lease("B", clock(), a.lease_ttl)
    b = monitor_factory(storage, worker_id="B")
    asyncio.run(b.run_cycle())
    open_after = storage.get_open_incident("novamart")
    assert open_after.id == open_before.id and open_after.failed_check_count == 2, "B continued A's incident"
    apps.mode["novamart"] = "healthy"
    clock.advance(30)
    asyncio.run(b.run_cycle())
    assert storage.get_open_incident("novamart") is None
    assert [i.state for i in storage.list_incidents(service_id="novamart")] == ["resolved"]


def test_clean_shutdown_releases_lease_for_immediate_restart(storage, monitor_factory, clock):
    monitor = monitor_factory(storage, worker_id="A")

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run_forever(stop))
        while storage.count_checks() < 2:
            await asyncio.sleep(0)
        stop.set()
        await task

    asyncio.run(go())
    assert storage.get_worker().owner is None
    assert storage.acquire_lease("B", clock(), 70), "a cleanly stopped worker hands over immediately"


def test_only_one_worker_polls_at_a_time(storage, monitor_factory, clock):
    a, b = monitor_factory(storage, worker_id="A"), monitor_factory(storage, worker_id="B")

    async def go():
        stop = asyncio.Event()
        tasks = [asyncio.create_task(a.run_forever(stop)), asyncio.create_task(b.run_forever(stop))]
        while storage.count_checks() < 6:
            await asyncio.sleep(0)
        stop.set()
        await asyncio.gather(*tasks)

    asyncio.run(go())
    owners = {c.worker_id for s in SERVICES for c in storage.list_checks(s.id, limit=100)}
    assert len(owners) == 1, f"both workers recorded checks: {owners}"


def test_slot_alignment():
    assert slot_for(T0.replace(second=47), 30) == T0.replace(second=30)
    assert slot_for(T0.replace(second=29), 30) == T0

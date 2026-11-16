"""Service health and incident recovery are separate: healthy again is not the same as closed."""

import asyncio

from .conftest import NOVACONNECT, NOVAMART, make_mongo, make_sqlite

RECOVERING_NOVAMART = NOVAMART.model_copy(update={"recovery": "checkout_attempts"})
SERVICES = [RECOVERING_NOVAMART, NOVACONNECT]


def cycle(monitor, clock, seconds=30):
    asyncio.run(monitor.run_cycle())
    clock.advance(seconds)


def _outage_then_fresh_checkout(storage, monitor_factory, clock, apps, interrupted=2):
    monitor = monitor_factory(storage, services=SERVICES)
    apps.mode["novamart"] = "functional_bug"
    cycle(monitor, clock)
    apps.mode["novamart"] = "healthy"
    apps.interrupted["novamart"] = interrupted
    cycle(monitor, clock)
    return monitor


def test_fresh_checkout_marks_service_operational_but_keeps_incident_recovering(monitor_factory, clock, apps):
    storage = make_mongo()
    monitor = _outage_then_fresh_checkout(storage, monitor_factory, clock, apps)
    incident = storage.get_open_incident("novamart")
    assert storage.get_state("novamart").status == "operational"
    assert incident.state == "open" and incident.recovery_phase == "recovering"
    assert incident.service_recovered_at is not None and incident.recovery_check_id
    assert incident.events[-1].type == "service_recovered"

    cycle(monitor, clock)  # no recovery record yet: still open
    storage.db["checkout_recoveries"].insert_one({"_id": incident.id, "phase": "needs_review",
                                                  "affected_checkout_ids": ["chk_a"], "attempts": {}})
    cycle(monitor, clock)  # an attempt needs review: still open
    assert storage.get_open_incident("novamart").id == incident.id

    storage.db["checkout_recoveries"].update_one({"_id": incident.id}, {"$set": {"phase": "complete"}})
    cycle(monitor, clock)
    resolved = storage.get_incident(incident.id)
    assert resolved.state == "resolved" and resolved.recovery_phase is None
    assert "verified outcome" in resolved.events[-1].message
    assert resolved.recovery_check_id == incident.recovery_check_id, "the fresh checkout that ended the outage"


def test_failing_again_while_recovering_returns_to_outage_on_the_same_incident(monitor_factory, clock, apps):
    storage = make_mongo()
    monitor = _outage_then_fresh_checkout(storage, monitor_factory, clock, apps)
    incident_id = storage.get_open_incident("novamart").id
    apps.mode["novamart"] = "functional_bug"
    cycle(monitor, clock)
    incident = storage.get_open_incident("novamart")
    assert incident.id == incident_id and incident.recovery_phase is None
    assert "relapsed" in [e.type for e in incident.events]


def test_nothing_interrupted_resolves_as_before(monitor_factory, clock, apps):
    storage = make_mongo()
    _outage_then_fresh_checkout(storage, monitor_factory, clock, apps, interrupted=0)
    assert storage.get_open_incident("novamart") is None
    assert storage.list_incidents(service_id="novamart")[0].state == "resolved"


def test_local_storage_without_the_recovery_worker_resolves_as_before(monitor_factory, clock, apps, tmp_path):
    storage = make_sqlite(tmp_path)
    _outage_then_fresh_checkout(storage, monitor_factory, clock, apps)
    assert storage.get_open_incident("novamart") is None

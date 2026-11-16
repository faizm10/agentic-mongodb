"""Reconciling checkouts interrupted by the NovaMart -> NovaConnect confirmation timeout.

The real NovaMart and NovaConnect backends run as subprocesses (real HTTP, real client
timeouts, their own uniqueness rules). Atlas is mongomock. Each scenario creates the
interrupted checkout the way the incident does, marks the uptime incident as recovering
(the monitor does this once a fresh checkout passes) and runs the recovery worker.
"""
from __future__ import annotations

from datetime import timedelta

import mongomock
import pytest

from agents import mongodb as m
from agents.recovery import LEASE_SECONDS, NovaConnectRecords, NovaMartRecords, RecoveryWorker

from .checkout_services import CheckoutServices

INCIDENT_ID = "UPT-novamart-20261115-101500"


class SimulatedCrash(BaseException):
    """Stands in for os._exit: nothing in the worker may catch it."""


class CountingNovaMart(NovaMartRecords):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resends: list[str] = []

    def resend_confirmation(self, checkout_id):
        self.resends.append(checkout_id)
        return super().resend_confirmation(checkout_id)


@pytest.fixture
def services(tmp_path):
    svc = CheckoutServices(tmp_path).start()
    yield svc
    svc.stop()


@pytest.fixture
def db():
    database = mongomock.MongoClient(tz_aware=True)["opsrelay_recovery_test"]
    m.ensure_setup(database, vector_index=False)
    return database


def _recovering(db, started_at):
    """What the uptime monitor writes when a fresh checkout passes after the outage."""
    db[m.UPTIME_INCIDENTS].insert_one({
        "_id": INCIDENT_ID, "service_id": "novamart", "app": "shopping", "state": "open",
        "recovery_phase": "recovering", "started_at": started_at, "service_recovered_at": m.utcnow(),
        "recovery_check_id": "novamart:fresh-checkout",
    })


def _worker(services, db, **kwargs):
    novamart = CountingNovaMart(services.novamart.url, timeout=0.5)
    return RecoveryWorker(novamart, NovaConnectRecords(services.novaconnect.url, timeout=0.5), db=db, **kwargs)


def _attempt(db, checkout_id):
    return m.checkout_recoveries(db).find_one({"_id": INCIDENT_ID})["attempts"][checkout_id]


def test_order_saved_confirmation_missing_is_resent_once_and_read_back(services, db):
    started = m.utcnow()
    services.novaconnect_fault("drop")
    response = services.checkout("chk_missing_0001")
    assert response.status_code == 201 and response.json()["confirmation"]["status"] == "pending"
    order_id = response.json()["order_id"]
    assert services.confirmations_for(order_id) == []  # dropped: NovaConnect never recorded it
    services.novaconnect_fault(None)  # NovaConnect answers again

    _recovering(db, started)
    worker = _worker(services, db)
    [doc] = worker.poll()

    a = doc["attempts"]["chk_missing_0001"]
    assert a["outcome"] == "completed" and a["final"] and a["outcome_recorded"]
    assert a["retry_intent"]["confirmation_id"] == "cnf-chk_missing_0001"
    assert a["verified_result"]["resent"] is True and a["verified_result"]["status"] == "delivered"
    assert a["verified_result"]["confirmations_for_order"] == 1
    assert worker.novamart.resends == ["chk_missing_0001"]
    assert len(services.confirmations_for(order_id)) == 1
    assert doc["phase"] == "complete"
    assert services.checkout_record("chk_missing_0001")["attempt"]["state"] == "completed"


def test_confirmation_recorded_but_response_lost_completes_without_resending(services, db):
    started = m.utcnow()
    services.novaconnect_fault("lost_response")
    response = services.checkout("chk_lostresp_001")
    assert response.status_code == 201
    order_id = response.json()["order_id"]
    attempt = services.checkout_record("chk_lostresp_001")["attempt"]
    assert attempt["confirmation"]["status"] == "unknown"  # NovaMart timed out waiting
    services.novaconnect_fault(None)

    _recovering(db, started)
    worker = _worker(services, db)
    [doc] = worker.poll()

    a = doc["attempts"]["chk_lostresp_001"]
    assert a["outcome"] == "completed"
    assert a["retry_intent"] is None and worker.novamart.resends == []
    assert a["verified_result"]["resent"] is False
    assert len(services.confirmations_for(order_id)) == 1
    assert doc["phase"] == "complete"


def test_order_write_failed_is_marked_failed_and_retryable(services, db):
    started = m.utcnow()
    services.novamart_fault("order_write")
    response = services.checkout("chk_nowrite_0001")
    assert response.status_code == 503 and response.json()["error"]["code"] == "ORDER_STORE_UNAVAILABLE"
    services.novamart_fault(None)

    _recovering(db, started)
    [doc] = _worker(services, db).poll()

    a = doc["attempts"]["chk_nowrite_0001"]
    assert a["state_at_discovery"] == "order_failed"
    assert a["outcome"] == "failed" and a["final"]
    assert a["evidence"]["order"]["state"] == "absent"
    assert a["evidence"]["confirmation"]["state"] == "absent"
    assert "can retry" in a["reason"]
    assert services.checkout_record("chk_nowrite_0001")["attempt"]["state"] == "failed"
    assert doc["phase"] == "complete"
    # The customer's retry with the same checkout ID now creates the order exactly once.
    assert services.checkout("chk_nowrite_0001").status_code == 201


def test_confirmation_lookup_timeout_stays_open_for_review(services, db):
    started = m.utcnow()
    services.novaconnect_fault("drop")
    order_id = services.checkout("chk_lookupto_001").json()["order_id"]
    services.novaconnect_fault("lookup_timeout")

    _recovering(db, started)
    worker = _worker(services, db)
    [doc] = worker.poll()

    a = doc["attempts"]["chk_lookupto_001"]
    assert a["outcome"] == "needs_review" and not a["final"]
    assert a["missing"] == ["NovaConnect confirmation record cnf-chk_lookupto_001 (timeout)"]
    assert a["evidence"]["order"]["state"] == "saved"
    assert worker.novamart.resends == [], "an unknown confirmation is never resent blindly"
    assert doc["phase"] == "needs_review"

    services.novaconnect_fault(None)  # the lookup works again on the next pass
    [doc] = worker.poll()
    assert doc["attempts"]["chk_lookupto_001"]["outcome"] == "completed"
    assert doc["phase"] == "complete"
    assert len(services.confirmations_for(order_id)) == 1


def test_worker_crash_after_confirmation_recorded_resumes_without_a_duplicate(services, db):
    started = m.utcnow()
    services.novaconnect_fault("drop")
    order_id = services.checkout("chk_crash_000001").json()["order_id"]
    services.novaconnect_fault(None)
    _recovering(db, started)

    def crash(point):
        if point == "after_confirmation_recorded":
            raise SimulatedCrash(point)

    first = _worker(services, db, crash=crash)
    with pytest.raises(SimulatedCrash):
        first.poll()

    # NovaConnect recorded the confirmation; OpsRelay saved only the intent, not the result.
    assert len(services.confirmations_for(order_id)) == 1
    crashed = _attempt(db, "chk_crash_000001")
    assert crashed["retry_intent"]["worker_id"] == first.worker_id
    assert crashed["verified_result"] is None and not crashed["final"]

    # A new worker after the dead worker's lease expired.
    later = lambda: m.utcnow() + timedelta(seconds=LEASE_SECONDS + 1)  # noqa: E731
    second = _worker(services, db, clock=later)
    assert second.worker_id != first.worker_id
    [doc] = second.poll()

    assert doc["incident_id"] == INCIDENT_ID
    assert doc["workers"] == [first.worker_id, second.worker_id]
    a = doc["attempts"]["chk_crash_000001"]
    assert a["outcome"] == "completed"
    assert a["verified_result"]["found_after_restart"] is True
    assert a["verified_result"]["confirmations_for_order"] == 1
    assert second.novamart.resends == [], "the restarted worker looked the confirmation up instead of resending"
    assert len(services.confirmations_for(order_id)) == 1
    assert doc["phase"] == "complete"
    assert any(e["type"] == "worker_claimed" and "resuming" in e["message"] and e["worker_id"] == second.worker_id
               for e in doc["events"])


def test_pending_delivery_is_not_complete_until_read_back_as_delivered(services, db):
    started = m.utcnow()
    services.novaconnect_fault("drop")
    services.checkout("chk_pending_00001")
    services.novaconnect_fault("pending")  # the resend is acknowledged, but delivery is held

    _recovering(db, started)
    [doc] = _worker(services, db).poll()

    a = doc["attempts"]["chk_pending_00001"]
    assert a["outcome"] == "awaiting_delivery" and not a["final"]
    assert doc["phase"] == "reconciling"


def test_a_worker_without_the_lease_cannot_write(services, db):
    started = m.utcnow()
    services.checkout("chk_lease_000001")
    _recovering(db, started)
    holder = _worker(services, db)
    holder._ensure(db[m.UPTIME_INCIDENTS].find_one({"_id": INCIDENT_ID}))
    assert holder.claim(INCIDENT_ID) is not None
    assert _worker(services, db).claim(INCIDENT_ID) is None

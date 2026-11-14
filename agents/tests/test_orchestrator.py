from datetime import timedelta

import mongomock
import pytest

from agents import memory
from agents import mongodb as m
from agents import orchestrator as o
from agents.fakes import SAMPLE_RUN, FakeGitHub, FakeRepairer


class Crash(BaseException):
    """Simulates the process dying (not caught by the orchestrator's `except Exception`)."""


@pytest.fixture
def db():
    db = mongomock.MongoClient(tz_aware=True)["opsrelay_test"]
    m.ensure_setup(db, vector_index=False)
    return db


def seed(db):
    memory.seed_memory(
        "shopping",
        "FAILED test_cart.py::test_discount_applied AssertionError assert 100.0 == 90.0 Cart.total",
        "Cart.total ignored discount; multiply by (1 - discount)", db=db)
    memory.seed_memory(
        "shopping", "FAILED test_inventory.py::test_stock KeyError sku",
        "Inventory lookup used wrong key", db=db)
    memory.seed_memory(
        "messaging", "FAILED test_send.py AssertionError assert 100.0 == 90.0 Cart.total",
        "messaging memory must never leak into shopping", db=db)


def test_dedup(db):
    iid, created = o.ingest_failed_run(SAMPLE_RUN, db=db)
    iid2, created2 = o.ingest_failed_run(SAMPLE_RUN, db=db)
    assert created and not created2 and iid == iid2
    assert m.incidents(db).count_documents({}) == 1
    # A re-run of the same workflow is a different attempt -> new incident
    _, created3 = o.ingest_failed_run({**SAMPLE_RUN, "run_attempt": 2}, db=db)
    assert created3


def test_similar_scoped_to_app(db):
    seed(db)
    sims = memory.find_similar(SAMPLE_RUN, k=3, db=db)
    assert sims and all(s["app"] == "shopping" for s in sims)
    assert "discount" in sims[0]["summary"]


def test_forget_memory(db):
    seed(db)
    top = memory.find_similar(SAMPLE_RUN, db=db)[0]
    assert memory.forget(top["memory_id"], "wrong fix", db=db)
    assert all(s["memory_id"] != top["memory_id"] for s in memory.find_similar(SAMPLE_RUN, db=db))


def test_happy_path(db):
    seed(db)
    gh, rep = FakeGitHub(), FakeRepairer()
    iid, _ = o.ingest_failed_run(SAMPLE_RUN, db=db)
    inc = o.Orchestrator(gh, rep, db=db, worker_id="w1").run_incident(iid)
    assert inc["status"] == m.STATUS_AWAITING_MERGE
    assert inc["stage"] == "memory_saved"
    assert inc["pr"]["number"] and gh.create_calls == 1
    assert rep.seen_similar and "discount" in rep.seen_similar[0]["summary"]
    stages = [s["stage"] for s in m.incident_timeline(iid, db=db) if s["outcome"] == "completed"]
    assert stages == m.STAGES
    # The fix is now a memory that future incidents will find
    assert m.memories(db).count_documents({"source_incident_id": iid}) == 1


def test_failed_verification_stops_before_pr(db):
    gh = FakeGitHub()
    iid, _ = o.ingest_failed_run(SAMPLE_RUN, db=db)
    inc = o.Orchestrator(gh, FakeRepairer(passes=False), db=db).run_incident(iid)
    assert inc["status"] == m.STATUS_NEEDS_REVIEW
    assert gh.create_calls == 0 and "pr" not in inc


def test_crash_after_pr_created_resumes_without_duplicate(db):
    gh = FakeGitHub()

    class CrashingGitHub(FakeGitHub):
        def open_draft_pr(self, *a, **kw):
            gh.open_draft_pr(*a, **kw)  # GitHub really creates it…
            raise Crash()               # …then the worker dies before saving the number

        def find_pr(self, repo, branch):
            return gh.find_pr(repo, branch)

    iid, _ = o.ingest_failed_run(SAMPLE_RUN, db=db)
    with pytest.raises(Crash):
        o.Orchestrator(CrashingGitHub(), FakeRepairer(), db=db, worker_id="w1").run_incident(iid)

    inc = m.incidents(db).find_one({"_id": iid})
    assert inc["stage"] == "pr_intent_saved" and "pr" not in inc
    assert gh.create_calls == 1

    # New worker can't steal a live lease…
    w2 = o.Orchestrator(gh, FakeRepairer(), db=db, worker_id="w2")
    assert w2.run_incident(iid) is None
    # …but once it expires, it resumes from saved state and adopts the existing PR.
    m.incidents(db).update_one({"_id": iid}, {"$set": {
        "lease.expires_at": m.utcnow() - timedelta(seconds=1)}})
    assert w2.resumable_ids() == [iid]
    inc = w2.run_incident(iid)
    assert inc["status"] == m.STATUS_AWAITING_MERGE
    assert gh.create_calls == 1  # no duplicate PR
    recovered = [s for s in m.incident_timeline(iid, db=db) if s["outcome"] == "recovered"]
    assert recovered and recovered[0]["worker_id"] == "w2"
    confirm = [s for s in m.incident_timeline(iid, db=db)
               if s["stage"] == "pr_confirmed" and s["outcome"] == "completed"][0]
    assert confirm["evidence"]["recovered_existing"] is True


def test_stale_worker_is_fenced(db):
    iid, _ = o.ingest_failed_run(SAMPLE_RUN, db=db)
    w1 = o.Orchestrator(FakeGitHub(), FakeRepairer(), db=db, worker_id="w1")
    inc = w1.claim(iid)
    # w2 takes over after w1's lease "expires"
    m.incidents(db).update_one({"_id": iid}, {"$set": {"lease.owner": "w2"}})
    with pytest.raises(o.LeaseLost):
        w1._advance(inc, "memory_retrieved", {"similar": []})


def test_stop_requested(db):
    iid, _ = o.ingest_failed_run(SAMPLE_RUN, db=db)
    o.request_stop(iid, "looks wrong", by="reviewer", db=db)
    gh = FakeGitHub()
    inc = o.Orchestrator(gh, FakeRepairer(), db=db).run_incident(iid)
    assert inc["status"] == m.STATUS_NEEDS_REVIEW and gh.create_calls == 0


def test_transient_error_retries(db):
    class Flaky(FakeRepairer):
        calls = 0

        def repair(self, run, similar):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise TimeoutError("LLM timeout")
            return super().repair(run, similar)

    iid, _ = o.ingest_failed_run(SAMPLE_RUN, db=db)
    orch = o.Orchestrator(FakeGitHub(), Flaky(), db=db)
    inc = orch.run_incident(iid)
    assert inc["status"] == m.STATUS_OPEN and inc["attempts"] == 1
    assert inc["stage"] == "memory_retrieved"  # didn't redo or lose earlier work
    assert orch.run_incident(iid)["status"] == m.STATUS_AWAITING_MERGE

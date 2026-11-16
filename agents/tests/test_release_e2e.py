"""Release path end to end on the Messaging staging fixture: failed live probe -> diagnosis ->
verified patch -> draft PR -> worker dies right after GitHub creates the PR -> a fresh worker
adopts the same PR -> human merge -> deployment verified -> fresh probe passes.

The model is a stub. The service, the live probe, the verifier and the stage machine are real.
GitHub is FakeGitHub persisted to a file, so it outlives the crashed worker like GitHub would.
"""
from datetime import timedelta

import mongomock
import pytest

from agents import diagnose_incident
from agents import mongodb as m
from agents import orchestrator as o
from agents.fakes import FakeGitHub
from agents.live_check import load_service_config, run_live_check
from agents.live_incident import LiveIncident, confirm_recovery

from .messaging_support import STORE_BUGGY, STORE_PATH, messaging_llm

REPO = "faizm10/agentic-mongodb"


class Crash(BaseException):
    """The worker process dying (not caught by the orchestrator's `except Exception`)."""


def fast_config(url):
    config = load_service_config("messaging", base_url=url, timeout=2.0)
    return config.model_copy(update={"delivery_wait_seconds": 1.0, "poll_interval_seconds": 0.2})


class LiveRepairer:
    def __init__(self, messaging, service, check):
        self.messaging, self.service, self.check = messaging, service, check

    def repair(self, failed_run, similar):
        result = diagnose_incident(LiveIncident(
            check=self.check, repo_path=str(self.messaging.repo), deployed_sha=failed_run["commit_sha"],
            service_logs=self.service.logs(), recent_diff=self.messaging.recent_diff,
            source_files={STORE_PATH: STORE_BUGGY}), messaging_llm())
        repair = result.repair
        return {"tests_passed": result.outcome == "verified", "test_output": repair.patched_suite.output_tail,
                "diff": repair.patch, "files": repair.patched_files, "changed_files": repair.changed_files,
                "summary": result.reason, "root_cause": result.likely_cause, "reason": result.reason}


class StagingDeployments:
    """Deployment evidence from the staging service: its version endpoint and a fresh probe."""

    def __init__(self, service, previous_probe_id):
        self.service, self.previous_probe_id, self.deployed = service, previous_probe_id, None

    def deployed_sha(self, app):
        return self.deployed  # None: the version endpoint is not reporting yet

    def includes(self, repo, fix_sha, deployed_sha):
        return fix_sha == deployed_sha

    def probe(self, app):
        r = confirm_recovery("messaging", config=fast_config(self.service.url), target="fixture",
                             previous_probe_id=self.previous_probe_id)
        return {"passed": r.recovered, "detail": r.reason}


@pytest.fixture
def db():
    db = mongomock.MongoClient(tz_aware=True)["opsrelay_release"]
    m.ensure_setup(db, vector_index=False)
    return db


def test_failed_probe_to_verified_recovery_survives_a_crash_after_pr_creation(messaging, deploy, db, tmp_path,
                                                                             monkeypatch):
    service = deploy(messaging.bad_sha)
    check = run_live_check("messaging", config=fast_config(service.url), target="fixture")
    assert check.reachable and check.failure_kind == "not_delivered"
    iid, _ = o.ingest_live_incident(REPO, {"uptime_incident_id": "UPT-messaging-1", "app": "messaging",
                                           "title": "Messages not delivered"}, messaging.bad_sha, db=db)
    repairer = LiveRepairer(messaging, service, check)
    deployments = StagingDeployments(service, check.probe_id)

    def crash_after(name):
        if name == "pr_created":
            raise Crash()

    monkeypatch.setattr(o, "crash_point", crash_after)
    first = FakeGitHub(tmp_path / "github.json")
    with pytest.raises(Crash):
        o.Orchestrator(first, repairer, db=db, worker_id="w1", publish_repos={REPO},
                       deployments=deployments).run_incident(iid)
    inc = m.incidents(db).find_one({"_id": iid})
    assert first.create_calls == 1 and inc["stage"] == "pr_intent_saved" and "pr" not in inc

    # The dead worker's lease expires; a fresh worker resumes and adopts the PR GitHub already has.
    m.incidents(db).update_one({"_id": iid}, {"$set": {"lease.expires_at": m.utcnow() - timedelta(seconds=1)}})
    github = FakeGitHub(tmp_path / "github.json")
    fresh = o.Orchestrator(github, repairer, db=db, worker_id="w2", publish_repos={REPO}, deployments=deployments)
    inc = fresh.run_incident(iid)
    assert inc["status"] == m.STATUS_AWAITING_MERGE and github.create_calls == 0
    assert len(github._load()) == 1 and inc["pr"]["number"] == next(iter(github._load().values()))["number"]
    assert inc["recommendation"]["kind"] == "verified_patch" and inc["verification"]["tests_passed"]

    github.set_state(REPO, inc["pr_intent"]["branch"], "merged", merge_sha="f1" * 20)
    assert fresh.check_reviews() == [(iid, m.STATUS_AWAITING_DEPLOYMENT)]

    fresh.check_deployments()  # no version reported yet: evidence unavailable, incident stays open
    inc = m.incidents(db).find_one({"_id": iid})
    assert inc["status"] == m.STATUS_AWAITING_DEPLOYMENT and inc["deployment"]["state"] == "unavailable"

    deployments.deployed = "f1" * 20  # the version endpoint claims the fix, but the old code still runs
    fresh.check_deployments()
    inc = m.incidents(db).find_one({"_id": iid})
    assert inc["deployment"]["state"] == "verified" and inc["live_recovery"]["state"] == "failing"
    assert inc["status"] == m.STATUS_AWAITING_DEPLOYMENT

    service.deploy_patch(inc["patch"]["diff"])
    fresh.check_deployments()
    inc = m.incidents(db).find_one({"_id": iid})
    assert inc["status"] == m.STATUS_RESOLVED and inc["live_recovery"]["state"] == "verified"
    assert (inc["verification"]["tests_passed"], inc["review"]["pr_state"], inc["deployment"]["state"]) == (
        True, "merged", "verified")

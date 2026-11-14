"""The incident agent end to end: uptime incident -> incidents table -> deep dive -> draft PR -> human.

Uses Taha's real NovaMart backend (shopping/backend) in a throwaway repo, Mo's orchestrator on
mongomock, a fake GitHub and a stub model. The scenario is the hard case: the discount bug is
live, but the tests that would catch it are missing, so the agent must write a regression test
that fails on the deployed code before it may propose a fix.
"""

import importlib.util
import io
import subprocess
import sys
import tarfile
from datetime import timedelta
from pathlib import Path

import mongomock
import pytest

from agents import Repairer
from agents import mongodb as m
from agents import orchestrator as o
from agents.fakes import FakeGitHub
from agents.github_publisher import validate_request
from agents.github_types import PatchFile, PublishRequest
from agents.intake import UPTIME_INCIDENTS, LiveIntake, handoff_from_uptime
from agents.investigator import _EvidenceDraft
from agents.regression import _RegressionTestDraft

from .support import StubLLM, _git, candidate, diagnosis_draft, patch_draft

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO = "faizm10/agentic-mongodb"
PRICING = "shopping/backend/app/pricing.py"
UNCOVERING = ["test_discounts.py", "test_checkout_api.py", "test_health_and_catalog.py"]

REGRESSION_TEST = '''"""Regression test written by OpsRelay for the live checkout probe failure."""

import pytest

from app import pricing

pytestmark = pytest.mark.opsrelay(component="pricing", capability="discounts")


def test_percent_promotion_takes_the_advertised_share():
    assert pricing.compute_discount(20_000, pricing.PROMOTIONS["WELCOME10"]) == 2_000
'''


def _bug():
    spec = importlib.util.spec_from_file_location("novamart_inject_bug_agent", REPO_ROOT / "shopping/inject_bug.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.BUGS["discount"]


@pytest.fixture(scope="module")
def novamart(tmp_path_factory):
    """main: NovaMart -> tests that cover discounts removed -> discount bug deployed."""
    tree = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", "HEAD", "shopping/backend"], capture_output=True)
    if tree.returncode != 0 or not tree.stdout:
        pytest.skip("shopping/backend is not committed in this checkout")
    repo = tmp_path_factory.mktemp("novamart-live")
    with tarfile.open(fileobj=io.BytesIO(tree.stdout)) as tar:
        tar.extractall(repo, filter="data")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "novamart")
    for name in UNCOVERING:
        (repo / "shopping/backend/tests" / name).unlink()
    _git(repo, "commit", "-q", "-am", "tests: trim slow suites")
    bug = _bug()
    path = repo / "shopping" / bug.file
    path.write_text(path.read_text().replace(bug.original, bug.buggy, 1))
    _git(repo, "commit", "-q", "-am", "pricing: tweak promo math")
    return repo, _git(repo, "rev-parse", "HEAD"), bug


def uptime_doc(started_at, route="investigate", **overrides):
    """An uptime incident as Taha's monitor stores it (Incident.model_dump + _id)."""
    failures = [{"check": "promotion_discounts", "component": "pricing",
                 "reason": "Promotion discounts do not match the advertised percentage/amount.",
                 "source": "backend/app/pricing.py::compute_discount",
                 "expected": {"WELCOME10@20000": 2000}, "actual": {"WELCOME10@20000": 20000}}]
    functional = {"kind": "functional", "url": "http://localhost:8100/api/health/checkout", "ok": False,
                  "http_status": 503, "error_kind": "http_error", "error": "HTTP 503",
                  "details": {"failures": failures if route == "investigate" else []}}
    availability = ({"kind": "availability", "ok": True, "http_status": 200} if route == "investigate"
                    else {"kind": "availability", "ok": False, "error_kind": "connection_refused",
                          "error": "connection refused"})
    doc = {
        "_id": f"UPT-novamart-{started_at:%Y%m%d-%H%M%S}", "service_id": "novamart", "app": "shopping",
        "state": "open", "severity": "degraded" if route == "investigate" else "down",
        "failure_type": "functional_probe_failed" if route == "investigate" else "backend_unreachable",
        "investigation_route": route, "title": "NovaMart is degraded", "summary": "checkout probe failed",
        "started_at": started_at,
        "evidence": {"latest_failure": {"checked_at": started_at.isoformat(),
                                        "probes": {"availability": availability, "functional": functional}}},
    }
    doc.update(overrides)
    return doc


def agent_llm():
    diagnosis = diagnosis_draft(
        likely_cause="compute_discount divides percentage promotions by 10 instead of 100 (10x discount).",
        explanation="The checkout probe expected WELCOME10 on $200 to be $20 off but got $200 off. "
                    "The latest commit changed the divisor in compute_discount.",
        evidence=[
            _EvidenceDraft(source="live_probe", reference="live_probe",
                           excerpt='"source": "backend/app/pricing.py::compute_discount"',
                           supports="The failing probe check names compute_discount."),
            _EvidenceDraft(source="source_file", reference=PRICING,
                           excerpt="discount = subtotal_cents * promotion.value // 10",
                           supports="The deployed code divides by 10."),
        ],
        suspect_files=[PRICING],
    )
    regression = _RegressionTestDraft(outcome="test", slug="percent_promotion_share", content=REGRESSION_TEST,
                                      asserts="WELCOME10 takes 10% off", review_reason=None)
    bug = _bug()
    fix = patch_draft(candidate(path=PRICING, find=bug.buggy, replace=bug.original,
                                summary="Divide percentage promotions by 100."))
    return StubLLM({"_DiagnosisDraft": diagnosis, "_RegressionTestDraft": regression, "_PatchDraft": fix})


@pytest.fixture
def db():
    db = mongomock.MongoClient(tz_aware=True)["opsrelay_agent_test"]
    m.ensure_setup(db, vector_index=False)
    return db


@pytest.fixture
def agent(db, novamart, monkeypatch):
    monkeypatch.delenv("OPSRELAY_APPS_CONFIG")  # Taha's real layout
    repo, sha, _ = novamart
    github, llm = FakeGitHub(), agent_llm()
    orch = o.Orchestrator(github, Repairer(repo_path=str(repo), llm=llm), db=db, worker_id="test-worker")
    intake = LiveIntake(REPO, db=db, resolve_sha=lambda: sha)
    return orch, intake, github, llm, sha


def test_intake_records_each_uptime_incident_once(db):
    now = m.utcnow()
    db[UPTIME_INCIDENTS].insert_many([
        uptime_doc(now - timedelta(minutes=2)),
        uptime_doc(now - timedelta(days=3)),                       # too old
        uptime_doc(now - timedelta(minutes=1), state="resolved"),  # already over
    ])
    intake = LiveIntake(REPO, db=db, resolve_sha=lambda: "a" * 40, clock=lambda: now)

    created = intake.poll()

    assert created == [f"{REPO}#UPT-novamart-{now - timedelta(minutes=2):%Y%m%d-%H%M%S}"]
    assert intake.poll() == []
    inc = m.incidents(db).find_one({"_id": created[0]})
    assert (inc["source"], inc["app"], inc["status"], inc["stage"]) == ("live", "shopping", "open", "detected")
    assert inc["live"]["suspect_sources"] == ["backend/app/pricing.py::compute_discount"]
    assert inc["live"]["live_check"]["reachable"] and inc["live"]["live_check"]["healthy"]
    assert o.branch_for(inc).startswith("agent/fix-live-upt-novamart-")


def test_intake_skips_bug_already_reported_by_ci(db):
    o.ingest_failed_run({"repo": REPO, "app": "shopping", "run_id": 7, "run_attempt": 1, "commit_sha": "b" * 40,
                         "failed_logs": "FAILED tests/test_discounts.py::test_x"}, db=db)
    db[UPTIME_INCIDENTS].insert_one(uptime_doc(m.utcnow()))

    assert LiveIntake(REPO, db=db, resolve_sha=lambda: "b" * 40).poll() == []
    assert m.incidents(db).count_documents({"source": "live"}) == 0


def test_live_bug_missed_by_tests_gets_regression_test_fix_and_draft_pr(db, agent):
    orch, intake, github, llm, sha = agent
    db[UPTIME_INCIDENTS].insert_one(uptime_doc(m.utcnow()))
    [iid] = intake.poll()

    orch.run_pending()

    inc = m.incidents(db).find_one({"_id": iid})
    assert inc["status"] == m.STATUS_AWAITING_MERGE, inc.get("status_reason")
    patch = inc["patch"]
    [added] = patch["added_tests"]
    assert added == "shopping/backend/tests/test_opsrelay_percent_promotion_share.py"
    assert sorted(patch["files"]) == sorted([PRICING, added])
    assert "// 100" in patch["files"][PRICING] and patch["files"][added] == REGRESSION_TEST
    assert f"+++ b/{added}" in patch["diff"] and "-        discount = subtotal_cents * promotion.value // 10" in patch["diff"]
    assert "added `shopping/backend/tests/test_opsrelay_percent_promotion_share.py`" in patch["investigation"]
    assert "promotion_discounts" in patch["investigation"]

    pr = github.find_pr(REPO, inc["pr_intent"]["branch"])
    assert pr["draft"] and pr["state"] == "open" and pr["files"] == sorted([PRICING, added])
    assert inc["pr_intent"]["title"].startswith("[OpsRelay] Fix shopping live incident")
    # The regression test was written once, before any fix, and the model saw the probe failure.
    names = [name for name, _, _ in llm.calls]
    assert names.index("_RegressionTestDraft") < names.index("_PatchDraft")
    assert "backend/app/pricing.py::compute_discount" in llm.calls[0][2]

    # Human approval: nothing is merged until a person does it.
    assert orch.check_reviews() == []
    github.set_state(REPO, inc["pr_intent"]["branch"], "merged")
    assert orch.check_reviews() == [(iid, m.STATUS_RESOLVED)]
    assert m.incidents(db).find_one({"_id": iid})["status"] == m.STATUS_RESOLVED
    assert m.memories(db).find_one({"source_incident_id": iid})["active"] is True


def test_rejected_pr_marks_incident_rejected_and_forgets_the_fix(db, agent):
    orch, intake, github, _, _ = agent
    db[UPTIME_INCIDENTS].insert_one(uptime_doc(m.utcnow()))
    [iid] = intake.poll()
    orch.run_pending()
    branch = m.incidents(db).find_one({"_id": iid})["pr_intent"]["branch"]

    github.set_state(REPO, branch, "closed")

    assert orch.check_reviews() == [(iid, m.STATUS_REJECTED)]
    assert m.memories(db).find_one({"source_incident_id": iid})["active"] is False
    assert orch.check_reviews() == []  # recorded once


def test_service_down_goes_to_a_human_without_model_or_code_change(db, agent):
    orch, intake, github, llm, _ = agent
    db[UPTIME_INCIDENTS].insert_one(uptime_doc(m.utcnow(), route="service_health"))
    [iid] = intake.poll()

    orch.run_pending()

    inc = m.incidents(db).find_one({"_id": iid})
    assert inc["status"] == m.STATUS_NEEDS_REVIEW
    assert "service health problem" in inc["status_reason"]
    assert "Check whether the service process" in inc["patch"]["investigation"]
    assert llm.calls == [] and github.create_calls == 0


def test_handoff_mapping_matches_taha_contract():
    doc = uptime_doc(m.utcnow())
    live = handoff_from_uptime(doc)
    assert live["schema"] == "opsrelay.uptime_incident.v1"
    assert live["live_check"]["failure_kind"] == "http_error" and live["live_check"]["http_status"] == 503
    assert live["probe_failures"][0]["source"] == "backend/app/pricing.py::compute_discount"


@pytest.mark.parametrize(("path", "ok"), [
    ("shopping/backend/tests/test_opsrelay_discount.py", True),
    ("message-platform/backend/tests/test_opsrelay_dupes.py", True),
    ("shopping/backend/app/new_module.py", False),
    ("shopping/backend/tests/test_discounts.py", False),
    (".github/workflows/tests/test_opsrelay_x.py", False),
])
def test_publisher_only_creates_agent_regression_tests(path, ok):
    req = PublishRequest(incident_id="live-upt-1", base_sha="a" * 40, root_cause="x", tests_passed=True,
                         files=[PatchFile(path=path, content="x = 1\n")], new_files=[path])
    assert (validate_request(req) is None) is ok


def test_regression_test_must_fail_on_deployed_code_before_any_fix_is_tried(db, agent):
    orch, intake, github, llm, _ = agent
    passes_on_bug = REGRESSION_TEST.replace("== 2_000", "== 20_000")       # asserts the buggy value
    broken = REGRESSION_TEST.replace("from app import pricing", "from app import no_such_module")
    llm.responses["_RegressionTestDraft"] = [
        _RegressionTestDraft(outcome="test", slug="a", content=passes_on_bug, asserts="x", review_reason=None),
        _RegressionTestDraft(outcome="test", slug="b", content=broken, asserts="x", review_reason=None),
    ]
    db[UPTIME_INCIDENTS].insert_one(uptime_doc(m.utcnow()))
    [iid] = intake.poll()

    orch.run_pending()

    inc = m.incidents(db).find_one({"_id": iid})
    assert inc["status"] == m.STATUS_NEEDS_REVIEW
    assert "no regression test reproduced the failure after 2 attempt(s)" in inc["status_reason"]
    assert "errors instead of failing an assertion" in inc["status_reason"]
    second_prompt = [u for n, _, u in llm.calls if n == "_RegressionTestDraft"][1]
    assert "does not reproduce the bug" in second_prompt   # round 1's rejection was fed back
    assert llm.count("_PatchDraft") == 0 and github.create_calls == 0

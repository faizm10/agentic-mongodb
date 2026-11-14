"""End-to-end live incident scenarios against the Messaging fixture (not the live app).

Run with -s to see each scenario's result:

    agents/.venv/bin/python -m pytest agents/tests/test_live_scenarios.py -s -q
"""

from agents import diagnose_incident
from agents.live_check import load_service_config, run_live_check
from agents.live_incident import LiveIncident, confirm_recovery
from agents.regression import _RegressionTestDraft

from .messaging_support import (
    STORE_BUGGY,
    STORE_PATH,
    HangingServer,
    code_bug_draft,
    free_port,
    infrastructure_draft,
    messaging_llm,
)
from .support import stub_llm


def fast_config(url, timeout=2.0):
    config = load_service_config("messaging", base_url=url, timeout=timeout)
    return config.model_copy(update={"delivery_wait_seconds": 1.0, "poll_interval_seconds": 0.2})


def probe(url, timeout=2.0):
    return run_live_check("messaging", config=fast_config(url, timeout), target="fixture")


def show(title, check, outcome=None):
    line = f"[{check.target}] {title}: reachable={check.reachable} healthy={check.healthy} " \
           f"delivery={check.delivery} failure={check.failure_kind} probe={check.probe_id[-8:]}"
    print(f"\n{line}" + (f"\n    -> {outcome}" if outcome else ""))


def incident(messaging, check, sha, logs="", **fields):
    return LiveIncident(check=check, repo_path=str(messaging.repo), deployed_sha=sha, service_logs=logs,
                        recent_diff=messaging.recent_diff, source_files={STORE_PATH: STORE_BUGGY}, **fields)


def test_scenario_1_messaging_healthy(messaging, deploy):
    llm = messaging_llm()
    check = probe(deploy(messaging.good_sha).url)

    result = diagnose_incident(incident(messaging, check, messaging.good_sha), llm)

    show("healthy", check, f"{result.outcome} / {result.route}")
    assert check.ok
    assert result.outcome == "healthy" and result.route == "none"
    assert llm.calls == []


def test_scenario_2_unreachable_while_ci_is_green(messaging):
    llm = messaging_llm()
    refused = probe(f"http://127.0.0.1:{free_port()}")
    hanging = HangingServer()
    try:
        timed_out = probe(hanging.url, timeout=0.5)
    finally:
        hanging.close()

    for check in (refused, timed_out):
        result = diagnose_incident(incident(messaging, check, messaging.good_sha, ci_status="passing"), llm)
        show(f"unreachable ({check.failure_kind})", check, f"{result.route}: {result.likely_cause}")

        assert result.outcome == "needs_review" and result.route == "service_health"
        assert "check service and deployment health" in result.reason
        assert "CI is green" in result.reason
        assert result.recommended_actions and result.repair is None

    assert refused.failure_kind == "connection_refused"
    assert timed_out.failure_kind == "connection_timeout"
    timeout_result = diagnose_incident(incident(messaging, timed_out, messaging.good_sha), llm)
    assert timeout_result.likely_cause.startswith("unknown:")
    assert llm.calls == [], "unreachable services never go to the model or code repair"


def test_scenario_3_delivery_fails_then_code_fix_restores_it(messaging, deploy):
    service = deploy(messaging.bad_sha)
    check = probe(service.url)
    llm = messaging_llm()

    result = diagnose_incident(incident(messaging, check, messaging.bad_sha, logs=service.logs()), llm)

    show("responds but does not deliver", check, f"{result.outcome} / {result.route}: {result.reason}")
    assert check.reachable and check.healthy and check.failure_kind == "not_delivered"
    assert "to inbox=opsrelay-probe-sender" in service.logs()
    assert result.outcome == "verified" and result.route == "code_repair"
    assert result.recovery == "pending_deploy"
    assert "FAILED tests/test_store.py::test_message_lands_in_recipient_inbox" in result.reproduction
    repair = result.repair
    assert repair.changed_files == [STORE_PATH]
    assert "+        inbox = normalize(message.recipient)" in repair.patch
    assert all(run.returncode == 1 for run in repair.baseline)
    assert repair.patched_suite.passed and "3 passed" in repair.patched_suite.output_tail
    print(f"    pytest before: {repair.baseline[0].output_tail.strip().splitlines()[-1]}")
    print(f"    pytest after:  {repair.patched_suite.output_tail.strip().splitlines()[-1]}")

    # Not recovered until the fix is actually deployed: a fresh probe still fails.
    before_deploy = confirm_recovery("messaging", config=fast_config(service.url), target="fixture",
                                     previous_probe_id=check.probe_id)
    show("recovery check before deploy", before_deploy.check, before_deploy.reason)
    assert not before_deploy.recovered

    service.deploy_patch(repair.patch)
    after_deploy = confirm_recovery("messaging", config=fast_config(service.url), target="fixture",
                                    previous_probe_id=check.probe_id)
    show("recovery check after deploy", after_deploy.check, after_deploy.reason)
    assert after_deploy.recovered
    assert after_deploy.check.probe_id not in (check.probe_id, before_deploy.check.probe_id)
    assert after_deploy.check.delivery == "delivered"


def test_scenario_3b_delivery_fails_for_infrastructure_reasons(messaging, deploy):
    service = deploy(messaging.good_sha, paused=True)
    check = probe(service.url)
    llm = messaging_llm(infrastructure_draft())

    result = diagnose_incident(incident(messaging, check, messaging.good_sha, logs=service.logs()), llm)

    show("paused delivery queue", check, f"{result.outcome}: {result.next_diagnostic}")
    assert check.failure_kind == "not_delivered"
    assert result.outcome == "needs_review" and result.route == "investigate"
    assert "infrastructure" in result.reason
    assert result.next_diagnostic.startswith("Check the delivery worker")
    assert result.repair is None
    assert [name for name, _, _ in llm.calls] == ["_DiagnosisDraft"]


def test_code_bug_claim_that_tests_cannot_reproduce_needs_review(messaging, deploy):
    service = deploy(messaging.good_sha, paused=True)
    check = probe(service.url)
    from agents.investigator import _EvidenceDraft

    claim = code_bug_draft(evidence=[_EvidenceDraft(
        source="service_logs", reference="service_logs", excerpt="delivery queue paused", supports="x")])

    llm = messaging_llm(claim)
    # The model's regression test passes on the deployed code: the claimed bug isn't there.
    llm.responses["_RegressionTestDraft"] = _RegressionTestDraft(
        outcome="test", slug="probe_reaches_recipient", asserts="probe lands in the recipient inbox",
        review_reason=None, content=(
            "from messaging.store import MessageStore\n\n\n"
            "def test_probe_reaches_recipient():\n"
            "    store = MessageStore()\n"
            "    store.send(\"probe-sender\", \"opsrelay-probe\", \"x\")\n"
            "    assert len(store.inbox(\"opsrelay-probe\")) == 1\n"))

    result = diagnose_incident(incident(messaging, check, messaging.good_sha, logs=service.logs()), llm)

    assert result.outcome == "needs_review" and result.route == "investigate"
    assert "test suite passes" in result.reason
    assert "passes on the current code, so it does not reproduce the bug" in result.reason
    assert result.next_diagnostic.startswith("Write a pytest regression test")
    assert "3 passed" in result.reproduction
    assert llm.count("_RegressionTestDraft") == 2 and llm.count("_PatchDraft") == 0


def test_delivery_failure_without_deployed_commit_asks_for_it(messaging, deploy):
    check = probe(deploy(messaging.bad_sha).url)

    result = diagnose_incident(LiveIncident(check=check), messaging_llm())

    assert result.outcome == "needs_review"
    assert "deployed commit" in result.next_diagnostic


def test_ci_evidence_still_takes_the_ci_repair_path(shopping):
    result = diagnose_incident(shopping.request(), stub_llm())

    assert result.outcome == "verified", result.reason
    assert result.patched_files["shopping/src/shopping/cart.py"].count("discount = self.subtotal()") == 1

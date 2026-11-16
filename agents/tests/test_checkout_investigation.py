"""The agent's evidence for a failed checkout probe, and the one action it recommends.

Real NovaMart and NovaConnect (subprocesses) hold the records and write the logs. The failing
``checkout_completes`` check is built in the shape NovaMart's probe reports it.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents import checkout_evidence as ce
from agents.live_repair import repair_live_incident
from agents.recovery import NovaConnectRecords, NovaMartRecords

from .checkout_services import CheckoutServices

FORBIDDEN = ("confirmation_timeout", "demo", "outage", "fault", "inject", "mixed", "lost_response",
             "raise ConfirmationUnavailableError")


@pytest.fixture
def services(tmp_path, monkeypatch):
    svc = CheckoutServices(tmp_path).start()
    monkeypatch.setenv("OPSRELAY_SHOPPING_LOG_FILE", str(tmp_path / "novamart.log"))
    monkeypatch.setenv("OPSRELAY_MESSAGING_LOG_FILE", str(tmp_path / "novaconnect.log"))
    yield svc
    svc.stop()


def _live(probe_checkout_id: str, request_id: str, order_id: str) -> dict:
    """An uptime incident whose checkout probe saved the order, then timed out on the confirmation."""
    return {
        "uptime_incident_id": "UPT-novamart-20261115-101500", "app": "shopping", "service_id": "novamart",
        "title": "NovaMart checkout failing", "investigation_route": "investigate",
        "started_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "probe_failures": [{
            "check": "checkout_completes", "component": "checkout",
            "source": "backend/app/checkout.py::CheckoutService.checkout",
            "reason": "Checkout returned CONFIRMATION_UNAVAILABLE (HTTP 504)",
            "expected": {"checkout": "succeeds"},
            "actual": {"checkout": "failed", "error_code": "CONFIRMATION_UNAVAILABLE", "http_status": 504,
                       "checkout_id": probe_checkout_id, "request_id": request_id, "order_saved": True,
                       "order_id": order_id,
                       "confirmation": {"status": "unknown",
                                        "error": {"kind": "timeout", "elapsed_ms": 512.0, "http_status": None}}},
        }],
    }


def _interrupted(services) -> tuple[dict, dict]:
    services.novaconnect_fault("drop")
    dropped = services.checkout("chk_evidence_0001")
    services.novaconnect_fault("lost_response")
    lost = services.checkout("chk_evidence_0002")
    services.novaconnect_fault(None)
    return dropped, lost


def _collect(services, live, changed=()):
    return ce.collect(live, novamart=NovaMartRecords(services.novamart.url, timeout=2),
                      novaconnect=NovaConnectRecords(services.novaconnect.url, timeout=2),
                      changed_in=lambda path: list(changed), ci_status="passing")


def test_evidence_correlates_records_and_logs_by_checkout_and_request_id(services):
    dropped, lost = _interrupted(services)
    request_id = dropped.headers["X-Request-ID"]
    evidence = _collect(services, _live("chk_evidence_0001", request_id, dropped.json()["order_id"]))

    by_id = {a["checkout_id"]: a for a in evidence.attempts}
    assert by_id["chk_evidence_0001"]["novaconnect"] == "absent"      # NovaConnect never recorded it
    assert by_id["chk_evidence_0002"]["novaconnect"] == "delivered"   # recorded; only the answer was lost
    assert {a["confirmation_status"] for a in by_id.values()} == {"unknown"}
    assert evidence.failure_point.startswith(f"after NovaMart saved order {dropped.json()['order_id']}")
    assert "timeout" in evidence.failure_point
    assert evidence.cause.startswith("unknown")
    # Both services' log lines for the same request, found by its ID.
    assert any(ln.startswith("[NovaMart]") and request_id in ln and "order_saved" in ln for ln in evidence.log_lines)
    assert any(ln.startswith("[NovaConnect]") and request_id in ln for ln in evidence.log_lines)
    assert evidence.hinted_file == "shopping/backend/app/checkout.py"


def test_timeout_without_a_recent_change_recommends_an_operational_step_without_a_model(services, monkeypatch):
    dropped, _ = _interrupted(services)
    live = _live("chk_evidence_0001", dropped.headers["X-Request-ID"], dropped.json()["order_id"])

    class NoChanges:
        repo_path = "."

        def _git(self, *args, default=None):
            return ""  # no commit touched the hinted file

        def _source_files(self, *args):
            raise AssertionError("the agent should stop before reading source or calling a model")

    monkeypatch.setenv("OPSRELAY_SHOPPING_URL", services.novamart.url)
    monkeypatch.setenv("OPSRELAY_MESSAGING_URL", services.novaconnect.url)
    out = repair_live_incident(NoChanges(), {"app": "shopping", "commit_sha": "a" * 40, "live": live}, [])

    rec = out["recommendation"]
    assert out["outcome"] == "needs_review" and out["diff"] == ""
    assert rec["action"] == "operational_step" and rec["cause"].startswith("unknown")
    assert "Recommendation:** Operational step" in out["investigation"]
    assert "chk_evidence_0001" in out["investigation"]


def test_recommendation_follows_the_repair_result_when_code_changed(services):
    dropped, _ = _interrupted(services)
    evidence = _collect(services, _live("chk_evidence_0001", dropped.headers["X-Request-ID"],
                                        dropped.json()["order_id"]), changed=["abc1234 Change checkout"])
    assert evidence.code_change_suspected
    assert evidence.orders_saved_but_failed == ["chk_evidence_0001"]

    fixed = ce.recommend(evidence, {"outcome": "verified", "root_cause": "checkout reports failure after saving"})
    assert fixed["action"] == "propose_code_fix" and "human" in fixed["detail"]
    unproven = ce.recommend(evidence, {"outcome": "needs_review", "reason": "the suite does not reproduce it"})
    assert unproven["action"] == "ask_for_review" and unproven["detail"] == "the suite does not reproduce it"


def test_the_evidence_given_to_the_model_does_not_name_the_injected_fault(services):
    dropped, _ = _interrupted(services)
    evidence = _collect(services, _live("chk_evidence_0001", dropped.headers["X-Request-ID"],
                                        dropped.json()["order_id"]), changed=["abc1234 Change checkout"])
    text = evidence.text() + "\n".join(evidence.markdown())
    assert not [w for w in FORBIDDEN if w in text]


def test_other_shopping_failures_have_no_checkout_evidence():
    assert ce.collect({"probe_failures": [{"check": "discount_math"}]}) is None

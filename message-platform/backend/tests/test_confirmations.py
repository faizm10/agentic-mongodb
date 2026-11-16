"""Order confirmations: one per stable ID, delivery read back, and the controlled timeout fault."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.opsrelay(component="confirmations", rule="one confirmation per ID; delivery is read back")


def _payload(checkout_id="chk_confirm_0001", order_id="NM-20261114-AAAAAA", **overrides):
    return {"confirmation_id": f"cnf-{checkout_id}", "checkout_id": checkout_id, "order_id": order_id,
            "recipient": "ada@example.com", "body": f"Your NovaMart order {order_id} is confirmed.", **overrides}


def _fault(db_path, mode, delay=0.0):
    (Path(db_path).parent / "opsrelay-outage.json").write_text(
        json.dumps({"scenario": "confirmation_timeout", "mode": mode, "delay_seconds": delay}))


def test_confirmation_is_recorded_delivered_and_readable(client):
    created = client.post("/api/confirmations", json=_payload())
    assert created.status_code == 201 and created.headers["idempotent-replay"] == "false"
    assert created.json()["status"] == "delivered"
    read = client.get("/api/confirmations/cnf-chk_confirm_0001")
    assert read.status_code == 200 and read.json()["delivered_at"]


def test_resend_with_the_same_id_never_records_twice(client):
    client.post("/api/confirmations", json=_payload())
    again = client.post("/api/confirmations", json=_payload())
    assert again.status_code == 200 and again.headers["idempotent-replay"] == "true"
    listed = client.get("/api/confirmations", params={"order_id": "NM-20261114-AAAAAA"}).json()
    assert listed["count"] == 1


@pytest.mark.parametrize("change", [{"order_id": "NM-20261114-BBBBBB"},
                                    {"confirmation_id": "cnf-chk_confirm_0002"}])
def test_same_id_or_checkout_with_different_details_is_a_conflict(client, change):
    client.post("/api/confirmations", json=_payload())
    conflict = client.post("/api/confirmations", json=_payload(**change))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CONFIRMATION_CONFLICT"


def test_unknown_confirmation_is_a_structured_404(client):
    missing = client.get("/api/confirmations/cnf-chk_missing_001")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "CONFIRMATION_NOT_FOUND"


def test_drop_fault_fails_without_recording(client, db_path):
    _fault(db_path, "drop")
    assert client.post("/api/confirmations", json=_payload()).status_code == 503
    assert client.get("/api/confirmations/cnf-chk_confirm_0001").status_code == 404


def test_lost_response_fault_records_before_the_sender_gives_up(client, db_path):
    _fault(db_path, "lost_response")
    assert client.post("/api/confirmations", json=_payload()).status_code == 201
    assert client.get("/api/confirmations/cnf-chk_confirm_0001").json()["status"] == "delivered"


def test_pending_fault_acknowledges_without_delivering(client, db_path):
    _fault(db_path, "pending")
    ack = client.post("/api/confirmations", json=_payload())
    assert ack.status_code == 201 and ack.json()["status"] == "accepted"
    assert client.get("/api/confirmations/cnf-chk_confirm_0001").json()["delivered_at"] is None


def test_confirmation_fault_does_not_affect_health_or_the_messaging_probe(client, db_path):
    _fault(db_path, "mixed")
    assert client.get("/health").status_code == 200
    assert client.get("/api/monitor/probe").status_code == 200

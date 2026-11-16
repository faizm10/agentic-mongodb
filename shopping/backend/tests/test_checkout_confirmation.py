"""Checkout IDs, order confirmations and the records OpsRelay uses to recover interrupted checkouts."""

import pytest

from app.errors import OrderStoreError

from ._assertions import expect_equal, expect_status

pytestmark = pytest.mark.opsrelay(component="checkout", capability="confirmation")

ORGANIC_TEE = "nm-app-002"
TERRA_MUGS = "nm-hom-002"


def _checkout(client, checkout_payload, checkout_id, items=((ORGANIC_TEE, 1),)):
    return client.post("/api/checkout", json={**checkout_payload(list(items)), "checkout_id": checkout_id})


def test_checkout_id_is_carried_into_order_and_confirmation(client, checkout_payload, confirmations):
    body = expect_status(_checkout(client, checkout_payload, "chk_carry_0001"), 201, check="api.checkout",
                         rule="a new checkout creates its order")
    assert body["checkout_id"] == "chk_carry_0001"
    assert body["confirmation_id"] == "cnf-chk_carry_0001"
    assert body["confirmation"] == {"id": "cnf-chk_carry_0001", "status": "delivered"}
    assert [s.confirmation_id for s in confirmations.sends] == ["cnf-chk_carry_0001"]
    assert confirmations.sends[0].order_id == body["order_id"]


def test_retry_with_same_checkout_id_returns_the_same_order(client, checkout_payload, confirmations):
    first = expect_status(_checkout(client, checkout_payload, "chk_retry_0001"), 201, check="api.checkout",
                          rule="first attempt creates the order")
    again = expect_status(_checkout(client, checkout_payload, "chk_retry_0001"), 200, check="api.checkout.retry",
                          rule="a retry of the same checkout must replay, not create a second order")
    expect_equal(again["order_id"], first["order_id"], check="api.checkout.retry",
                 rule="a retry returns the original order", function="app.store.DemoStore.order_for_checkout")
    assert again["replayed"] is True
    assert len(client.get("/api/orders").json()["orders"]) == 1
    assert len(confirmations.sends) == 1, "a delivered confirmation is not sent again"


def test_same_checkout_id_with_a_different_cart_is_a_conflict(client, checkout_payload):
    expect_status(_checkout(client, checkout_payload, "chk_conflict_01"), 201, check="api.checkout", rule="created")
    body = expect_status(_checkout(client, checkout_payload, "chk_conflict_01", items=((TERRA_MUGS, 2),)), 409,
                         check="api.checkout.conflict", rule="a checkout ID names one cart")
    assert body["error"]["code"] == "CHECKOUT_ID_CONFLICT"


def test_confirmation_timeout_after_the_order_is_saved_still_completes_checkout(client, checkout_payload,
                                                                                 confirmations):
    confirmations.fail_next = 1
    response = _checkout(client, checkout_payload, "chk_timeout_001")
    body = expect_status(response, 201, check="api.checkout.confirmation_timeout",
                         rule="once the order is saved, a confirmation timeout must not fail the checkout")
    assert body["confirmation"] == {"id": "cnf-chk_timeout_001", "status": "pending"}

    record = client.get("/api/checkouts/chk_timeout_001").json()
    assert record["order_state"] == "saved" and record["order"]["order_id"] == body["order_id"]
    attempt = record["attempt"]
    assert attempt["state"] == "order_saved"
    assert attempt["confirmation"]["status"] == "unknown"
    assert attempt["confirmation"]["error"]["kind"] == "timeout"
    assert attempt["response"]["status_code"] == 201
    assert response.headers["x-request-id"] in attempt["request_ids"]


def test_client_retry_after_a_timeout_resends_the_same_confirmation(client, checkout_payload, confirmations):
    confirmations.fail_next = 1
    confirmations.record_before_failing = True  # NovaConnect stored it; only the response was lost
    first = _checkout(client, checkout_payload, "chk_lostresp_01").json()
    again = expect_status(_checkout(client, checkout_payload, "chk_lostresp_01"), 200, check="api.checkout.retry",
                          rule="retry after a lost confirmation response")
    assert again["order_id"] == first["order_id"]
    assert again["confirmation"]["status"] == "delivered"
    assert {s.confirmation_id for s in confirmations.sends} == {"cnf-chk_lostresp_01"}
    assert len(confirmations.records) == 1


def test_order_write_failure_is_recorded_before_any_order_exists(client, checkout_payload, monkeypatch):
    store = client.app.state.service.store

    def broken_add_order(data, order):
        raise OrderStoreError("The local order store could not be written.", details={"error": "OSError: disk full"})

    monkeypatch.setattr(store, "add_order", broken_add_order)
    body = expect_status(_checkout(client, checkout_payload, "chk_nowrite_001"), 503, check="api.checkout.store",
                         rule="an order store failure is reported")
    assert body["error"]["code"] == "ORDER_STORE_UNAVAILABLE"
    monkeypatch.undo()

    record = client.get("/api/checkouts/chk_nowrite_001").json()
    assert record["order_state"] == "absent"
    assert record["attempt"]["state"] == "order_failed"
    unfinished = client.get("/api/checkouts", params={"unfinished": True}).json()["checkouts"]
    assert [a["checkout_id"] for a in unfinished] == ["chk_nowrite_001"]


def test_resend_sends_only_the_confirmation_under_its_stable_id(client, checkout_payload, confirmations):
    confirmations.fail_next = 1
    order = _checkout(client, checkout_payload, "chk_resend_0001").json()
    resent = expect_status(client.post("/api/checkouts/chk_resend_0001/confirmation"), 200,
                           check="api.checkouts.resend", rule="resend after a missing confirmation")
    assert resent == {"checkout_id": "chk_resend_0001", "order_id": order["order_id"],
                      "confirmation_id": "cnf-chk_resend_0001", "status": "delivered", "replayed": False}
    assert len(client.get("/api/orders").json()["orders"]) == 1
    body = expect_status(client.post("/api/checkouts/chk_unknown_001/confirmation"), 404,
                         check="api.checkouts.resend.missing", rule="no order, nothing to confirm")
    assert body["error"]["code"] == "ORDER_NOT_FOUND"


def test_resend_reports_novaconnect_timeouts(client, checkout_payload, confirmations):
    confirmations.fail_next = 2
    _checkout(client, checkout_payload, "chk_resendto_01")
    body = expect_status(client.post("/api/checkouts/chk_resendto_01/confirmation"), 504,
                         check="api.checkouts.resend.timeout", rule="a resend that times out is not a success")
    assert body["error"]["code"] == "CONFIRMATION_UNAVAILABLE"


def test_reconciliation_outcomes_are_checked_against_the_order(client, checkout_payload, confirmations, monkeypatch):
    confirmations.fail_next = 1
    _checkout(client, checkout_payload, "chk_recon_00001")
    body = expect_status(client.put("/api/checkouts/chk_recon_00001/reconciliation",
                                    json={"incident_id": "UPT-x", "outcome": "failed"}), 409,
                         check="api.checkouts.reconcile", rule="a checkout with an order cannot be marked failed")
    assert body["error"]["code"] == "CHECKOUT_ID_CONFLICT"
    done = expect_status(client.put("/api/checkouts/chk_recon_00001/reconciliation",
                                    json={"incident_id": "UPT-x", "outcome": "completed",
                                          "evidence": {"confirmation": "delivered"}}), 200,
                         check="api.checkouts.reconcile", rule="verified completion is recorded")
    assert done["state"] == "completed" and done["reconciliation"]["incident_id"] == "UPT-x"
    assert client.get("/api/checkouts", params={"unfinished": True}).json()["count"] == 0


def test_probe_runs_a_fresh_checkout_and_counts_interrupted_ones(client, checkout_payload, confirmations):
    confirmations.fail_next = 1
    _checkout(client, checkout_payload, "chk_probecount1")
    report = client.get("/api/health/checkout").json()
    flow = next(c for c in report["checks"] if c["name"] == "checkout_completes")
    assert flow["status"] == "pass", flow
    assert flow["actual"]["checkout_id"].startswith("probe-")
    assert report["interrupted_checkouts"] == 1
    assert report["side_effects"]["orders_created"] == 0
    assert len(client.get("/api/orders").json()["orders"]) == 1, "the probe's order never reaches the real store"

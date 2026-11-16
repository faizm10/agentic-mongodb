"""Order creation and local persistence."""

import json
import re

import pytest

from app.checkout import CartItem
from app.store import DemoStore

from ._assertions import expect_status

pytestmark = pytest.mark.opsrelay(component="orders", capability="persistence")

ORGANIC_TEE = "nm-app-002"
TERRA_MUGS = "nm-hom-002"
ORDER_ID_RE = re.compile(r"^NM-\d{8}-[0-9A-F]{6}$")


def _place(service, customer, address, items=((ORGANIC_TEE, 1),)):
    return service.place_order([CartItem(p, q) for p, q in items], None, "standard", customer, address)


def test_order_is_persisted_to_disk_and_survives_restart(service, customer, address, data_dir):
    order = _place(service, customer, address)
    assert ORDER_ID_RE.match(order["order_id"]), order["order_id"]

    on_disk = json.loads((data_dir / "novamart-store.json").read_text())
    assert [o["order_id"] for o in on_disk["orders"]] == [order["order_id"]]
    assert on_disk["sold"] == {ORGANIC_TEE: 1}

    reloaded = DemoStore(data_dir)  # simulates a server restart
    assert reloaded.get_order(order["order_id"]) == order


def test_order_contains_customer_items_and_simulated_payment(service, customer, address):
    order = _place(service, customer, address, items=((ORGANIC_TEE, 2), (TERRA_MUGS, 1)))
    assert order["status"] == "confirmed"
    assert order["customer"]["email"] == customer["email"]
    assert order["shipping_address"]["city"] == "San Francisco"
    assert [(i["product_id"], i["quantity"]) for i in order["items"]] == [(ORGANIC_TEE, 2), (TERRA_MUGS, 1)]
    assert order["payment"] == {
        "method": "simulated",
        "status": "not_charged",
        "message": "Demo checkout: no payment was requested, authorized or captured.",
    }
    assert order["estimated_delivery"]["from"] <= order["estimated_delivery"]["to"]


def test_orders_are_listed_newest_first(client, checkout_payload):
    ids = [client.post("/api/checkout", json=checkout_payload([(ORGANIC_TEE, 1)])).json()["order_id"] for _ in range(3)]
    listed = [o["order_id"] for o in client.get("/api/orders").json()["orders"]]
    assert listed == list(reversed(ids))


def test_get_order_by_id(client, checkout_payload):
    created = client.post("/api/checkout", json=checkout_payload([(ORGANIC_TEE, 1)])).json()
    fetched = client.get(f"/api/orders/{created['order_id']}")
    created.pop("replayed")  # describes the checkout response, not the order
    assert expect_status(fetched, 200, check="api.get_order", rule="created order must be retrievable") == created


def test_unknown_order_returns_structured_404(client):
    body = expect_status(client.get("/api/orders/NM-00000000-FFFFFF"), 404, check="api.get_order.missing",
                         rule="unknown order ids return 404")
    assert body["error"]["code"] == "ORDER_NOT_FOUND"
    assert body["request_id"]


def test_corrupt_store_returns_structured_503(client, data_dir, checkout_payload):
    data_dir.mkdir(parents=True)
    (data_dir / "novamart-store.json").write_text("{not json")
    body = expect_status(client.post("/api/checkout", json=checkout_payload([(ORGANIC_TEE, 1)])), 503,
                         check="api.checkout.corrupt_store", rule="unreadable store must fail loudly, not silently")
    assert body["error"]["code"] == "ORDER_STORE_UNAVAILABLE"
    assert "JSONDecodeError" in body["error"]["details"]["error"]

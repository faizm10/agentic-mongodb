"""Inventory validation: never sell more than we have."""

import json

import pytest

from app import inventory
from app.checkout import CartItem
from app.errors import InsufficientStockError

from ._assertions import expect_equal, expect_status

pytestmark = pytest.mark.opsrelay(component="inventory", capability="stock_validation")

POLAR_CAMERA = "nm-wsp-001"  # stock 3
FORM_CHAIR = "nm-hom-001"  # stock 4
SUNGLASSES = "nm-acc-001"  # stock 0 (sold out)
ORGANIC_TEE = "nm-app-002"  # stock 100


def _shortages(requested, available):
    return [(s.product_id, s.requested, s.available) for s in inventory.find_shortages(requested, available)]


@pytest.mark.parametrize(
    ("requested", "available", "expected"),
    [
        ({"sku": 3}, {"sku": 2}, [("sku", 3, 2)]),
        ({"sku": 10}, {"sku": 9}, [("sku", 10, 9)]),
        ({"sku": 2}, {"sku": 2}, []),
        ({"sku": 1}, {"sku": 50}, []),
        ({"sku": 1}, {"sku": 0}, [("sku", 1, 0)]),
        ({"sku": 1}, {}, [("sku", 1, 0)]),
    ],
)
def test_find_shortages(requested, available, expected):
    expect_equal(
        _shortages(requested, available),
        expected,
        check="inventory.find_shortages",
        rule="any requested quantity above available stock must be reported as a shortage",
        function="app.inventory.find_shortages",
        context={"requested": requested, "available": available},
    )


def test_duplicate_lines_are_aggregated_before_validation():
    requested = inventory.aggregate_quantities([("sku", 2), ("sku", 2)])
    assert requested == {"sku": 4}
    expect_equal(
        _shortages(requested, {"sku": 3}), [("sku", 4, 3)], check="inventory.aggregate",
        rule="two lines of 2 against stock 3 is an oversell", function="app.inventory.find_shortages",
    )


def test_ensure_available_raises_structured_error():
    with pytest.raises(InsufficientStockError) as info:
        inventory.ensure_available({"sku": 5}, {"sku": 1})
    err = info.value.to_dict()
    assert err["code"] == "INSUFFICIENT_STOCK"
    assert err["component"] == "inventory"
    assert err["details"]["shortages"][0] == {
        "code": "INSUFFICIENT_STOCK", "product_id": "sku", "requested": 5, "available": 1,
        "message": "Only 1 left in stock (you requested 5).",
    }


def test_quote_flags_over_stock_lines(service):
    quote = service.quote([CartItem(POLAR_CAMERA, 4)], None)
    expect_equal(
        [s.to_dict()["product_id"] for s in quote.shortages], [POLAR_CAMERA], check="checkout.quote.stock",
        rule="quoting 4 cameras with 3 in stock must surface a shortage",
        function="app.checkout.CheckoutService.quote -> app.inventory.find_shortages",
    )
    assert quote.can_checkout is False


def test_checkout_rejects_oversell_and_leaves_stock_untouched(client, checkout_payload, data_dir):
    response = client.post("/api/checkout", json=checkout_payload([(POLAR_CAMERA, 4)]))
    body = expect_status(response, 409, check="api.checkout.oversell",
                         rule="ordering 4 cameras with only 3 in stock must be rejected")
    assert body["error"]["code"] == "INSUFFICIENT_STOCK"
    assert body["error"]["details"]["shortages"][0]["available"] == 3
    product = client.get(f"/api/products/{POLAR_CAMERA}").json()
    assert product["available"] == 3
    on_disk = json.loads((data_dir / "novamart-store.json").read_text())
    assert on_disk["orders"] == [] and on_disk["sold"] == {}, "a rejected checkout must not create an order or sell stock"
    assert [a["state"] for a in on_disk["checkouts"].values()] == ["rejected"], "the attempt is kept as rejected"


def test_checkout_rejects_sold_out_product(client, checkout_payload):
    response = client.post("/api/checkout", json=checkout_payload([(SUNGLASSES, 1)]))
    body = expect_status(response, 409, check="api.checkout.sold_out", rule="sold-out items cannot be purchased")
    assert body["error"]["details"]["shortages"][0]["message"] == "This item is sold out."


def test_split_lines_cannot_bypass_stock_check(client, checkout_payload):
    response = client.post("/api/checkout", json=checkout_payload([(POLAR_CAMERA, 2), (POLAR_CAMERA, 2)]))
    expect_status(response, 409, check="api.checkout.split_lines",
                  rule="2 + 2 cameras with only 3 in stock must be rejected")


def test_stock_is_decremented_and_enforced_across_orders(client, checkout_payload):
    first = client.post("/api/checkout", json=checkout_payload([(FORM_CHAIR, 3)]))
    expect_status(first, 201, check="api.checkout.within_stock", rule="3 of 4 chairs must be purchasable")
    assert client.get(f"/api/products/{FORM_CHAIR}").json()["available"] == 1

    second = client.post("/api/checkout", json=checkout_payload([(FORM_CHAIR, 2)]))
    expect_status(second, 409, check="api.checkout.depleted_stock",
                  rule="2 chairs with only 1 remaining must be rejected")

    last = client.post("/api/checkout", json=checkout_payload([(FORM_CHAIR, 1)]))
    expect_status(last, 201, check="api.checkout.exact_remaining", rule="the final chair must be purchasable")
    product = client.get(f"/api/products/{FORM_CHAIR}").json()
    assert product["available"] == 0 and product["in_stock"] is False


def test_unsold_products_show_their_full_stock(client):
    products = {p["id"]: p for p in client.get("/api/products").json()["products"]}
    assert products[POLAR_CAMERA]["available"] == 3
    assert products[ORGANIC_TEE]["available"] == 100
    assert products[SUNGLASSES]["available"] == 0

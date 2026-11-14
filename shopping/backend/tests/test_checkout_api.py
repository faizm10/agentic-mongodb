"""End-to-end checkout through the HTTP API."""

import pytest

from ._assertions import expect_equal, expect_status

pytestmark = pytest.mark.opsrelay(component="checkout", capability="place_order")

AURA_HEADPHONES = "nm-aud-001"  # $249.00
ORGANIC_TEE = "nm-app-002"  # $35.00


def test_full_checkout_golden_order(client, checkout_payload):
    # subtotal 249.00 + 2 x 35.00 = 319.00; WELCOME10 -31.90 = 287.10
    # shipping free (>= $75); tax 8.25% of 287.10 = 23.68575 -> 23.69; total 310.79
    response = client.post("/api/checkout", json=checkout_payload([(AURA_HEADPHONES, 1), (ORGANIC_TEE, 2)], "WELCOME10"))
    order = expect_status(response, 201, check="api.checkout.happy_path", rule="a valid order must be created")
    expect_equal(
        {k: order[k] for k in ("item_count", "subtotal_cents", "discount_cents", "shipping_cents", "tax_cents",
                               "total_cents")},
        {"item_count": 3, "subtotal_cents": 31_900, "discount_cents": 3_190, "shipping_cents": 0,
         "tax_cents": 2_369, "total_cents": 31_079},
        check="checkout.golden_order",
        rule="golden order (1x Aura, 2x Tee, WELCOME10, standard) must total $310.79",
        function="POST /api/checkout -> app.checkout.CheckoutService.place_order",
    )
    assert order["promo_code"] == "WELCOME10"
    assert order["payment"]["status"] == "not_charged"


def test_quote_and_checkout_agree(client, checkout_payload):
    payload = checkout_payload([(AURA_HEADPHONES, 1), (ORGANIC_TEE, 3)], "SAVE15", "express")
    quote = client.post("/api/cart/quote", json={k: payload[k] for k in ("items", "promo_code", "shipping_method")})
    order = client.post("/api/checkout", json=payload)
    quote_body = expect_status(quote, 200, check="api.cart_quote", rule="quote must succeed")
    order_body = expect_status(order, 201, check="api.checkout", rule="checkout must succeed")
    for key in ("subtotal_cents", "discount_cents", "shipping_cents", "tax_cents", "total_cents"):
        expect_equal(order_body[key], quote_body[key], check=f"checkout.quote_parity.{key}",
                     rule="the customer must be charged exactly what they were quoted",
                     function="app.checkout.CheckoutService", money=True)
    assert order_body["shipping_cents"] == 1_995


def test_empty_cart_is_rejected(client, checkout_payload):
    body = expect_status(client.post("/api/checkout", json=checkout_payload([])), 400,
                         check="api.checkout.empty", rule="empty carts cannot be checked out")
    assert body["error"]["code"] == "EMPTY_CART"


def test_unknown_product_is_rejected(client, checkout_payload):
    body = expect_status(client.post("/api/checkout", json=checkout_payload([("nm-nope-999", 1)])), 404,
                         check="api.checkout.unknown_product", rule="unknown products are rejected")
    assert body["error"] == {
        "code": "PRODUCT_NOT_FOUND",
        "component": "catalog",
        "message": "Product 'nm-nope-999' does not exist.",
        "details": {"product_id": "nm-nope-999"},
        "hint": "The item may have been removed from the catalog; remove it from the cart.",
    }


def test_invalid_customer_details_return_field_errors(client, checkout_payload):
    payload = checkout_payload([(ORGANIC_TEE, 1)])
    payload["customer"]["email"] = "not-an-email"
    payload["shipping_address"]["postal_code"] = ""
    body = expect_status(client.post("/api/checkout", json=payload), 422, check="api.checkout.validation",
                         rule="invalid shipping details must be rejected")
    assert body["error"]["code"] == "VALIDATION_FAILED"
    fields = {f["field"] for f in body["error"]["details"]["fields"]}
    assert {"customer.email", "shipping_address.postal_code"} <= fields


def test_card_data_is_never_accepted_or_stored(client, checkout_payload):
    payload = checkout_payload([(ORGANIC_TEE, 1)])
    payload["card_number"] = "4242424242424242"
    order = expect_status(client.post("/api/checkout", json=payload), 201, check="api.checkout",
                          rule="checkout must succeed")
    assert "4242" not in str(order)
    assert order["payment"]["method"] == "simulated"


def test_request_id_is_echoed_for_tracing(client):
    response = client.get("/api/health", headers={"X-Request-ID": "trace-abc"})
    assert response.headers["X-Request-ID"] == "trace-abc"

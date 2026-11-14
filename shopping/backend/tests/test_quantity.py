"""Quantity calculations: line totals, item counts and quantity validation."""

import pytest

from app import pricing
from app.checkout import CartItem
from app.errors import InvalidQuantityError

from ._assertions import expect_equal, expect_status

pytestmark = pytest.mark.opsrelay(component="pricing", capability="quantity")

TERRA_MUGS = "nm-hom-002"  # $42.00
ORGANIC_TEE = "nm-app-002"  # $35.00
AURA_HEADPHONES = "nm-aud-001"  # $249.00


@pytest.mark.parametrize(
    ("unit_price_cents", "quantity", "expected"),
    [
        (4_200, 1, 4_200),
        (4_200, 2, 8_400),
        (3_500, 3, 10_500),
        (24_900, 5, 124_500),
        (999, 10, 9_990),
    ],
)
def test_line_total_multiplies_unit_price_by_quantity(unit_price_cents, quantity, expected):
    expect_equal(
        pricing.line_total(unit_price_cents, quantity),
        expected,
        check="pricing.line_total",
        rule="line total must equal unit price x quantity",
        function="app.pricing.line_total",
        context={"unit_price_cents": unit_price_cents, "quantity": quantity},
        money=True,
    )


@pytest.mark.parametrize("bad_quantity", [0, -1, 11, 100, 2.5, True, "3"])
def test_invalid_quantities_are_rejected(bad_quantity):
    with pytest.raises(InvalidQuantityError) as info:
        pricing.validate_quantity(bad_quantity)
    assert info.value.code == "INVALID_QUANTITY"
    assert info.value.details["quantity"] == bad_quantity


def test_item_count_and_subtotal_across_lines():
    lines = [
        pricing.CartLine(TERRA_MUGS, "Terra Stoneware Mug Set", 4_200, 2),
        pricing.CartLine(ORGANIC_TEE, "Essential Organic Tee", 3_500, 3),
    ]
    breakdown = pricing.price_cart(lines)
    expect_equal(
        breakdown.item_count, 5, check="pricing.item_count", rule="item count is the sum of line quantities",
        function="app.pricing.price_cart", context={"quantities": [2, 3]},
    )
    expect_equal(
        breakdown.subtotal_cents,
        18_900,
        check="pricing.subtotal",
        rule="subtotal is the sum of (unit price x quantity) across lines",
        function="app.pricing.price_cart",
        context={"lines": [[TERRA_MUGS, 4_200, 2], [ORGANIC_TEE, 3_500, 3]]},
        money=True,
    )


def test_quote_scales_with_quantity(service):
    single = service.quote([CartItem(AURA_HEADPHONES, 1)], None).breakdown.subtotal_cents
    triple = service.quote([CartItem(AURA_HEADPHONES, 3)], None).breakdown.subtotal_cents
    expect_equal(
        triple,
        single * 3,
        check="checkout.quote_quantity_scaling",
        rule="tripling the quantity must triple the subtotal",
        function="app.checkout.CheckoutService.quote",
        context={"product_id": AURA_HEADPHONES, "single_subtotal_cents": single},
        money=True,
    )


def test_api_quote_reports_line_totals(client):
    response = client.post(
        "/api/cart/quote",
        json={"items": [{"product_id": ORGANIC_TEE, "quantity": 4}, {"product_id": TERRA_MUGS, "quantity": 2}]},
    )
    body = expect_status(response, 200, check="api.cart_quote", rule="a valid cart must be quotable")
    totals = {line["product_id"]: line["line_total_cents"] for line in body["lines"]}
    expect_equal(
        totals,
        {ORGANIC_TEE: 14_000, TERRA_MUGS: 8_400},
        check="api.cart_quote.line_totals",
        rule="each line total must equal unit price x quantity",
        function="POST /api/cart/quote -> app.pricing.line_total",
    )
    expect_equal(body["item_count"], 6, check="api.cart_quote.item_count", rule="item count is the sum of quantities",
                 function="POST /api/cart/quote")


def test_api_rejects_out_of_range_quantity_with_structured_error(client):
    response = client.post("/api/cart/quote", json={"items": [{"product_id": ORGANIC_TEE, "quantity": 0}]})
    body = expect_status(response, 422, check="api.cart_quote.invalid_quantity", rule="quantity 0 must be rejected")
    assert body["error"]["code"] == "INVALID_QUANTITY"
    assert body["error"]["component"] == "pricing"
    assert body["error"]["details"]["max"] == pricing.MAX_QUANTITY_PER_LINE

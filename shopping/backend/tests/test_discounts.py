"""Promotion codes and discount calculation."""

import pytest

from app import pricing
from app.checkout import CartItem
from app.errors import InvalidPromoCodeError

from ._assertions import expect_equal, expect_status

pytestmark = pytest.mark.opsrelay(component="pricing", capability="discounts")

AURA_HEADPHONES = "nm-aud-001"  # $249.00
ORGANIC_TEE = "nm-app-002"  # $35.00
PROMOS = pricing.PROMOTIONS


@pytest.mark.parametrize(
    ("code", "subtotal", "expected"),
    [
        ("WELCOME10", 10_000, 1_000),
        ("WELCOME10", 18_900, 1_890),
        ("WELCOME10", 9_997, 999),  # floor to whole cents
        ("NOVA20", 25_000, 5_000),
        ("NOVA20", 20_000, 4_000),  # exactly at minimum
        ("NOVA20", 19_999, 0),  # below minimum
        ("SAVE15", 12_000, 1_500),
        ("SAVE15", 9_999, 0),  # below minimum
        ("FREESHIP", 5_000, 0),  # shipping promo, no price discount
    ],
)
def test_discount_amounts(code, subtotal, expected):
    promo = PROMOS[code]
    expect_equal(
        pricing.compute_discount(subtotal, promo),
        expected,
        check=f"pricing.discount.{code}",
        rule=f"{code} must give '{promo.description}'",
        function="app.pricing.compute_discount",
        context={"code": code, "kind": promo.kind, "value": promo.value, "subtotal_cents": subtotal,
                 "min_subtotal_cents": promo.min_subtotal_cents},
        money=True,
    )


def test_discount_never_exceeds_subtotal():
    big_fixed = pricing.Promotion("BIG", "fixed", 5_000, "$50 off")
    assert pricing.compute_discount(3_000, big_fixed) == 3_000


def test_percent_discount_is_proportional():
    promo = PROMOS["WELCOME10"]
    for subtotal in (1_000, 5_000, 40_000, 123_400):
        discount = pricing.compute_discount(subtotal, promo)
        expect_equal(
            discount * 10, subtotal, check="pricing.discount.proportional",
            rule="a 10% promotion must remove exactly one tenth of the subtotal",
            function="app.pricing.compute_discount", context={"subtotal_cents": subtotal, "discount_cents": discount},
            money=True,
        )


def test_promo_codes_are_case_and_whitespace_insensitive():
    assert pricing.resolve_promotion("  welcome10 ") is PROMOS["WELCOME10"]
    assert pricing.resolve_promotion(None) is None
    assert pricing.resolve_promotion("   ") is None


def test_unknown_promo_code_raises_structured_error():
    with pytest.raises(InvalidPromoCodeError) as info:
        pricing.resolve_promotion("FREEMONEY")
    assert info.value.details["promo_code"] == "FREEMONEY"
    assert "WELCOME10" in info.value.details["valid_codes"]


def test_discount_is_applied_before_tax(service):
    # $249.00 - 10% ($24.90) = $224.10; tax 8.25% = 18.48825 -> $18.49; free shipping
    b = service.quote([CartItem(AURA_HEADPHONES, 1)], "WELCOME10").breakdown
    expect_equal(
        {"discount": b.discount_cents, "shipping": b.shipping_cents, "tax": b.tax_cents, "total": b.total_cents},
        {"discount": 2_490, "shipping": 0, "tax": 1_849, "total": 24_259},
        check="checkout.golden_welcome10",
        rule="WELCOME10 on Aura headphones must total $242.59",
        function="app.checkout.CheckoutService.quote -> app.pricing.compute_discount",
    )


def test_free_shipping_promo_only_waives_standard_shipping(service):
    standard = service.quote([CartItem(ORGANIC_TEE, 1)], "FREESHIP", "standard").breakdown
    express = service.quote([CartItem(ORGANIC_TEE, 1)], "FREESHIP", "express").breakdown
    assert standard.shipping_cents == 0
    assert express.shipping_cents == pricing.SHIPPING_RATES_CENTS["express"]
    assert standard.discount_cents == 0


def test_ineligible_promo_reports_how_much_more_to_spend(client):
    response = client.post("/api/cart/quote", json={"items": [{"product_id": ORGANIC_TEE, "quantity": 1}],
                                                    "promo_code": "NOVA20"})
    body = expect_status(response, 200, check="api.cart_quote.promo", rule="ineligible promo must still quote")
    assert body["discount_cents"] == 0
    assert body["promo"]["applied"] is False
    assert "$165.00 more" in body["promo"]["message"]


def test_quote_with_invalid_promo_flags_error_but_still_prices(client):
    response = client.post("/api/cart/quote", json={"items": [{"product_id": ORGANIC_TEE, "quantity": 1}],
                                                    "promo_code": "bogus"})
    body = expect_status(response, 200, check="api.cart_quote.invalid_promo", rule="quote must degrade gracefully")
    assert body["promo_error"]["code"] == "INVALID_PROMO_CODE"
    assert body["can_checkout"] is False
    assert body["subtotal_cents"] == 3_500


def test_checkout_with_invalid_promo_is_rejected(client, checkout_payload):
    response = client.post("/api/checkout", json=checkout_payload([(ORGANIC_TEE, 1)], promo_code="bogus"))
    body = expect_status(response, 400, check="api.checkout.invalid_promo", rule="unknown promo must block checkout")
    assert body["error"]["code"] == "INVALID_PROMO_CODE"

"""Pricing: tax, shipping and order-total reconciliation."""

import pytest

from app import pricing
from app.checkout import CartItem

from ._assertions import expect_equal

pytestmark = pytest.mark.opsrelay(component="pricing", capability="totals")

ORGANIC_TEE = "nm-app-002"  # $35.00
AURA_HEADPHONES = "nm-aud-001"  # $249.00


@pytest.mark.parametrize(
    ("taxable", "expected"),
    [(10_000, 825), (1_000, 83), (61, 5), (1, 0), (0, 0), (3_500, 289)],
)
def test_tax_is_8_25_percent_rounded_half_up(taxable, expected):
    expect_equal(
        pricing.compute_tax(taxable), expected, check="pricing.tax", rule="tax = round_half_up(taxable x 8.25%)",
        function="app.pricing.compute_tax", context={"taxable_cents": taxable}, money=True,
    )


@pytest.mark.parametrize(
    ("discounted_subtotal", "method", "expected"),
    [
        (7_499, "standard", 595),
        (7_500, "standard", 0),
        (50_000, "standard", 0),
        (3_000, "express", 1_995),
        (50_000, "express", 1_995),
    ],
)
def test_shipping_rates_and_free_shipping_threshold(discounted_subtotal, method, expected):
    expect_equal(
        pricing.compute_shipping(discounted_subtotal, method, None), expected, check="pricing.shipping",
        rule="standard is free at $75+, otherwise $5.95; express is always $19.95",
        function="app.pricing.compute_shipping", context={"subtotal": discounted_subtotal, "method": method},
        money=True,
    )


def test_unknown_shipping_method_is_rejected():
    from app.errors import InvalidShippingMethodError

    with pytest.raises(InvalidShippingMethodError):
        pricing.compute_shipping(1_000, "teleport", None)


def test_single_item_golden_total(service):
    # $35.00 + $5.95 shipping + $2.89 tax (3500 * 8.25% = 288.75 -> 289) = $43.84
    b = service.quote([CartItem(ORGANIC_TEE, 1)], None).breakdown
    expect_equal(
        {"subtotal": b.subtotal_cents, "discount": b.discount_cents, "shipping": b.shipping_cents,
         "tax": b.tax_cents, "total": b.total_cents},
        {"subtotal": 3_500, "discount": 0, "shipping": 595, "tax": 289, "total": 4_384},
        check="checkout.golden_single_item",
        rule="single tee order must total $43.84",
        function="app.checkout.CheckoutService.quote",
    )


@pytest.mark.parametrize(
    ("items", "promo", "method"),
    [
        ([(ORGANIC_TEE, 1)], None, "standard"),
        ([(ORGANIC_TEE, 2), (AURA_HEADPHONES, 1)], "WELCOME10", "express"),
        ([(AURA_HEADPHONES, 2)], "NOVA20", "standard"),
        ([(ORGANIC_TEE, 3)], "SAVE15", "standard"),
        ([(ORGANIC_TEE, 1)], "FREESHIP", "standard"),
    ],
)
def test_total_reconciles_with_components(service, items, promo, method):
    b = service.quote([CartItem(pid, qty) for pid, qty in items], promo, method).breakdown
    expect_equal(
        b.total_cents,
        b.subtotal_cents - b.discount_cents + b.shipping_cents + b.tax_cents,
        check="pricing.total_reconciliation",
        rule="total = subtotal - discount + shipping + tax",
        function="app.pricing.price_cart",
        context={"items": items, "promo": promo, "method": method},
        money=True,
    )
    assert 0 <= b.discount_cents <= b.subtotal_cents

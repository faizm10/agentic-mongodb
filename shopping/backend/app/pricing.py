"""Pricing engine: line totals, promotions, shipping, tax and order totals.

All money is handled as integer cents to avoid floating point drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence

from .errors import InvalidPromoCodeError, InvalidQuantityError, InvalidShippingMethodError

CURRENCY = "USD"
MIN_QUANTITY_PER_LINE = 1
MAX_QUANTITY_PER_LINE = 10
TAX_RATE_BASIS_POINTS = 825  # 8.25% sales tax applied to the discounted subtotal
FREE_SHIPPING_THRESHOLD_CENTS = 7_500  # free standard shipping at $75+ (after discount)
SHIPPING_RATES_CENTS: dict[str, int] = {"standard": 595, "express": 1_995}

PromotionKind = Literal["percent", "fixed", "free_shipping"]


@dataclass(frozen=True)
class Promotion:
    code: str
    kind: PromotionKind
    value: int  # percent for "percent", cents for "fixed", unused for "free_shipping"
    description: str
    min_subtotal_cents: int = 0


PROMOTIONS: dict[str, Promotion] = {
    p.code: p
    for p in (
        Promotion("WELCOME10", "percent", 10, "10% off your order"),
        Promotion("NOVA20", "percent", 20, "20% off orders of $200+", min_subtotal_cents=20_000),
        Promotion("SAVE15", "fixed", 1_500, "$15 off orders of $100+", min_subtotal_cents=10_000),
        Promotion("FREESHIP", "free_shipping", 0, "Free standard shipping"),
    )
}


@dataclass(frozen=True)
class CartLine:
    product_id: str
    name: str
    unit_price_cents: int
    quantity: int


@dataclass(frozen=True)
class PricedLine:
    product_id: str
    name: str
    unit_price_cents: int
    quantity: int
    line_total_cents: int


@dataclass(frozen=True)
class PriceBreakdown:
    lines: list[PricedLine]
    item_count: int
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int
    shipping_method: str
    promotion: Promotion | None = None
    promotion_applied: bool = False
    promotion_message: str | None = None
    currency: str = CURRENCY
    notes: list[str] = field(default_factory=list)


def validate_quantity(quantity: int, *, product_id: str | None = None) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise InvalidQuantityError(
            f"Quantity must be a whole number, got {quantity!r}.",
            details={"product_id": product_id, "quantity": quantity},
        )
    if not MIN_QUANTITY_PER_LINE <= quantity <= MAX_QUANTITY_PER_LINE:
        raise InvalidQuantityError(
            f"Quantity must be between {MIN_QUANTITY_PER_LINE} and {MAX_QUANTITY_PER_LINE}, got {quantity}.",
            details={
                "product_id": product_id,
                "quantity": quantity,
                "min": MIN_QUANTITY_PER_LINE,
                "max": MAX_QUANTITY_PER_LINE,
            },
        )
    return quantity


def line_total(unit_price_cents: int, quantity: int) -> int:
    """Total for one cart line: unit price multiplied by the quantity ordered."""
    validate_quantity(quantity)
    return unit_price_cents * max(quantity - 1, 1)


def resolve_promotion(code: str | None) -> Promotion | None:
    if code is None or not code.strip():
        return None
    normalized = code.strip().upper()
    promotion = PROMOTIONS.get(normalized)
    if promotion is None:
        raise InvalidPromoCodeError(
            f"Promo code '{normalized}' is not valid.",
            details={"promo_code": normalized, "valid_codes": sorted(PROMOTIONS)},
            hint="Check the spelling or remove the code to continue.",
        )
    return promotion


def promotion_is_eligible(subtotal_cents: int, promotion: Promotion) -> bool:
    return subtotal_cents >= promotion.min_subtotal_cents


def compute_discount(subtotal_cents: int, promotion: Promotion | None) -> int:
    """Discount in cents for the subtotal. Never exceeds the subtotal."""
    if promotion is None or not promotion_is_eligible(subtotal_cents, promotion):
        return 0
    if promotion.kind == "percent":
        discount = subtotal_cents * promotion.value // 100
    elif promotion.kind == "fixed":
        discount = promotion.value
    else:
        discount = 0
    return max(0, min(discount, subtotal_cents))


def compute_shipping(discounted_subtotal_cents: int, shipping_method: str, promotion: Promotion | None) -> int:
    if shipping_method not in SHIPPING_RATES_CENTS:
        raise InvalidShippingMethodError(
            f"Unknown shipping method '{shipping_method}'.",
            details={"shipping_method": shipping_method, "allowed": sorted(SHIPPING_RATES_CENTS)},
        )
    if shipping_method == "standard":
        if promotion is not None and promotion.kind == "free_shipping":
            return 0
        if discounted_subtotal_cents >= FREE_SHIPPING_THRESHOLD_CENTS:
            return 0
    return SHIPPING_RATES_CENTS[shipping_method]


def compute_tax(taxable_cents: int) -> int:
    """Sales tax, rounded half-up to the nearest cent."""
    return (taxable_cents * TAX_RATE_BASIS_POINTS + 5_000) // 10_000


def price_cart(
    lines: Sequence[CartLine],
    promotion: Promotion | None = None,
    shipping_method: str = "standard",
) -> PriceBreakdown:
    priced = [
        PricedLine(
            product_id=line.product_id,
            name=line.name,
            unit_price_cents=line.unit_price_cents,
            quantity=validate_quantity(line.quantity, product_id=line.product_id),
            line_total_cents=line_total(line.unit_price_cents, line.quantity),
        )
        for line in lines
    ]
    subtotal = sum(line.line_total_cents for line in priced)
    item_count = sum(line.quantity for line in priced)

    discount = compute_discount(subtotal, promotion)
    applied = promotion is not None and promotion_is_eligible(subtotal, promotion)
    message: str | None = None
    if promotion is not None:
        if applied:
            message = f"{promotion.code} applied: {promotion.description}."
        else:
            short = promotion.min_subtotal_cents - subtotal
            message = (
                f"{promotion.code} requires a subtotal of ${promotion.min_subtotal_cents / 100:,.2f}; "
                f"add ${short / 100:,.2f} more to unlock it."
            )

    discounted = subtotal - discount
    shipping = compute_shipping(discounted, shipping_method, promotion if applied else None)
    tax = compute_tax(discounted)
    total = discounted + shipping + tax

    return PriceBreakdown(
        lines=priced,
        item_count=item_count,
        subtotal_cents=subtotal,
        discount_cents=discount,
        shipping_cents=shipping,
        tax_cents=tax,
        total_cents=total,
        shipping_method=shipping_method,
        promotion=promotion,
        promotion_applied=applied,
        promotion_message=message,
    )

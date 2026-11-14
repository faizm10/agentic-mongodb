"""Checkout service: turns a cart into a validated quote and a persisted order.

Payments are *simulated*. No card data is accepted and nothing is ever charged.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from . import demo_outage, inventory, pricing
from .catalog import Catalog, Product
from .errors import EmptyCartError, InvalidPromoCodeError, NovaMartError, PaymentUnavailableError
from .store import DemoStore

DELIVERY_WINDOWS_DAYS = {"standard": (4, 7), "express": (1, 2)}
PAYMENT_NOTICE = "Demo checkout: no payment was requested, authorized or captured."


@dataclass(frozen=True)
class CartItem:
    product_id: str
    quantity: int


@dataclass
class Quote:
    breakdown: pricing.PriceBreakdown
    products: dict[str, Product]
    availability: dict[str, int]
    shortages: list[inventory.Shortage]
    promo_error: dict[str, Any] | None = None
    requested: dict[str, int] = field(default_factory=dict)

    @property
    def can_checkout(self) -> bool:
        return not self.shortages and self.promo_error is None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_order_id(now: datetime) -> str:
    return f"NM-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"


class CheckoutService:
    def __init__(self, catalog: Catalog, store: DemoStore, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self.catalog = catalog
        self.store = store
        self.clock = clock

    # ---------------------------------------------------------- availability
    def availability(self, sold: Mapping[str, int] | None = None) -> dict[str, int]:
        if demo_outage.mode(self.store.data_dir) == "inventory":
            return {p.id: 0 for p in self.catalog.products}
        sold = self.store.sold_counts() if sold is None else sold
        return {p.id: p.stock - int(sold.get(p.id, 0)) for p in self.catalog.products}

    # ---------------------------------------------------------------- quotes
    def build_quote(
        self,
        items: Sequence[CartItem],
        promo_code: str | None,
        shipping_method: str,
        *,
        availability: Mapping[str, int],
        catalog: Catalog | None = None,
    ) -> Quote:
        """Pure checkout evaluation: catalog lookup, quantity, pricing and stock checks.

        Shared by the cart quote, real order placement and the side-effect-free health probe.
        """
        catalog = catalog or self.catalog
        if not items:
            raise EmptyCartError("Your cart is empty.", hint="Add at least one product before checking out.")

        products: dict[str, Product] = {}
        lines: list[pricing.CartLine] = []
        for item in items:
            product = catalog.get(item.product_id)
            pricing.validate_quantity(item.quantity, product_id=product.id)
            products[product.id] = product
            lines.append(pricing.CartLine(product.id, product.name, product.price_cents, item.quantity))

        promotion = pricing.resolve_promotion(promo_code)
        breakdown = pricing.price_cart(lines, promotion, shipping_method)

        requested = inventory.aggregate_quantities((line.product_id, line.quantity) for line in lines)
        shortages = inventory.find_shortages(requested, availability)
        return Quote(
            breakdown=breakdown,
            products=products,
            availability={pid: max(0, availability.get(pid, 0)) for pid in products},
            shortages=shortages,
            requested=requested,
        )

    def quote(self, items: Sequence[CartItem], promo_code: str | None, shipping_method: str = "standard") -> Quote:
        availability = self.availability()
        try:
            return self.build_quote(items, promo_code, shipping_method, availability=availability)
        except InvalidPromoCodeError as exc:
            # Keep the cart usable: price without the code and surface the problem.
            quote = self.build_quote(items, None, shipping_method, availability=availability)
            quote.promo_error = exc.to_dict()
            return quote

    # ---------------------------------------------------------------- orders
    def place_order(
        self,
        items: Sequence[CartItem],
        promo_code: str | None,
        shipping_method: str,
        customer: Mapping[str, Any],
        shipping_address: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self.store.transaction() as data:
            availability = self.availability(data["sold"])
            quote = self.build_quote(items, promo_code, shipping_method, availability=availability)
            inventory.ensure_available(quote.requested, availability)

            if demo_outage.mode(self.store.data_dir) == "payment":
                raise PaymentUnavailableError("Payment processing is temporarily unavailable.")

            now = self.clock()
            order = self._build_order(quote, now, customer, shipping_address)
            data["orders"].append(order)
            for product_id, qty in quote.requested.items():
                data["sold"][product_id] = int(data["sold"].get(product_id, 0)) + qty
        return order

    def _build_order(
        self,
        quote: Quote,
        now: datetime,
        customer: Mapping[str, Any],
        shipping_address: Mapping[str, Any],
    ) -> dict[str, Any]:
        b = quote.breakdown
        lo, hi = DELIVERY_WINDOWS_DAYS[b.shipping_method]
        totals = breakdown_to_dict(b)
        if b.total_cents != b.subtotal_cents - b.discount_cents + b.shipping_cents + b.tax_cents:
            raise NovaMartError("Order totals do not reconcile.", details=totals)  # defensive invariant
        return {
            "order_id": new_order_id(now),
            "status": "confirmed",
            "created_at": now.isoformat(),
            "customer": dict(customer),
            "shipping_address": dict(shipping_address),
            "items": lines_to_dicts(quote),
            **{k: v for k, v in totals.items() if k != "lines"},
            "estimated_delivery": {
                "from": (now + timedelta(days=lo)).date().isoformat(),
                "to": (now + timedelta(days=hi)).date().isoformat(),
            },
            "payment": {"method": "simulated", "status": "not_charged", "message": PAYMENT_NOTICE},
        }


# ------------------------------------------------------------------ serializers
def breakdown_to_dict(b: pricing.PriceBreakdown) -> dict[str, Any]:
    return {
        "item_count": b.item_count,
        "subtotal_cents": b.subtotal_cents,
        "discount_cents": b.discount_cents,
        "shipping_cents": b.shipping_cents,
        "tax_cents": b.tax_cents,
        "total_cents": b.total_cents,
        "currency": b.currency,
        "shipping_method": b.shipping_method,
        "promo_code": b.promotion.code if b.promotion and b.promotion_applied else None,
    }


def lines_to_dicts(quote: Quote) -> list[dict[str, Any]]:
    out = []
    for line in quote.breakdown.lines:
        product = quote.products[line.product_id]
        out.append(
            {
                "product_id": line.product_id,
                "slug": product.slug,
                "name": line.name,
                "image": product.image,
                "unit_price_cents": line.unit_price_cents,
                "quantity": line.quantity,
                "line_total_cents": line.line_total_cents,
            }
        )
    return out


def quote_to_dict(quote: Quote) -> dict[str, Any]:
    b = quote.breakdown
    discounted = b.subtotal_cents - b.discount_cents
    promo = None
    if b.promotion is not None:
        promo = {
            "code": b.promotion.code,
            "description": b.promotion.description,
            "applied": b.promotion_applied,
            "message": b.promotion_message,
        }
    lines = lines_to_dicts(quote)
    for line in lines:
        line["available"] = quote.availability.get(line["product_id"], 0)
    return {
        "lines": lines,
        **breakdown_to_dict(b),
        "promo": promo,
        "promo_error": quote.promo_error,
        "free_shipping_threshold_cents": pricing.FREE_SHIPPING_THRESHOLD_CENTS,
        "amount_to_free_shipping_cents": max(0, pricing.FREE_SHIPPING_THRESHOLD_CENTS - discounted),
        "issues": [s.to_dict() for s in quote.shortages],
        "can_checkout": quote.can_checkout,
    }

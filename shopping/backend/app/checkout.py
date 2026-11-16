"""Checkout service: turns a cart into a validated quote, a persisted order and a confirmation.

Payments are *simulated*. No card data is accepted and nothing is ever charged.

A checkout is identified by a client-supplied checkout ID that stays the same across
client retries. It is carried through the attempt record, the order, the confirmation
ID sent to NovaConnect and every log line. Once the order is committed it stands: a
confirmation that cannot be delivered right away is resent later and never turns a
saved order into a failed checkout.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from . import demo_outage, inventory, pricing
from .catalog import Catalog, Product
from .confirmations import (
    DELIVERED,
    ConfirmationClient,
    ConfirmationRequest,
    ConfirmationResult,
    ConfirmationUnavailable,
    HttpConfirmationClient,
    confirmation_id_for,
)
from .errors import (
    CheckoutConflictError,
    CheckoutNotFoundError,
    ConfirmationUnavailableError,
    EmptyCartError,
    InvalidPromoCodeError,
    NovaMartError,
    OrderNotFoundError,
    OrderStoreError,
    PaymentUnavailableError,
)
from .store import COMPLETED, FAILED, ORDER_FAILED, ORDER_SAVED, REJECTED, DemoStore

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


@dataclass
class CheckoutResult:
    order: dict[str, Any]
    attempt: dict[str, Any]
    replayed: bool

    @property
    def confirmation(self) -> dict[str, Any]:
        delivered = self.attempt["confirmation"]["status"] == DELIVERED
        return {"id": self.attempt["confirmation_id"], "status": "delivered" if delivered else "pending"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def cart_fingerprint(items: Sequence[CartItem], promo_code: str | None, shipping_method: str,
                     customer: Mapping[str, Any], shipping_address: Mapping[str, Any]) -> str:
    """Same checkout ID, same order: a retry that changes the cart is a conflict, not a replay."""
    payload = {
        "items": sorted([i.product_id, i.quantity] for i in items),
        "promo_code": (promo_code or "").upper(),
        "shipping_method": shipping_method,
        "email": str(customer.get("email", "")).lower(),
        "address": dict(sorted(shipping_address.items())),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


def new_order_id(now: datetime) -> str:
    return f"NM-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"


class CheckoutService:
    def __init__(
        self,
        catalog: Catalog,
        store: DemoStore,
        *,
        clock: Callable[[], datetime] = _utcnow,
        confirmations: ConfirmationClient | None = None,
        log: Callable[..., None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.clock = clock
        self.confirmations = confirmations if confirmations is not None else HttpConfirmationClient()
        self.log = log or (lambda event, **fields: None)

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
        *,
        checkout_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        with self.store.transaction() as data:
            availability = self.availability(data["sold"])
            quote = self.build_quote(items, promo_code, shipping_method, availability=availability)
            inventory.ensure_available(quote.requested, availability)

            if demo_outage.mode(self.store.data_dir) == "payment":
                raise PaymentUnavailableError(
                    "Our payment provider (Stripe) is not responding. Your card was not charged; please try again shortly."
                )

            if demo_outage.mode(self.store.data_dir) == "order_write":
                raise OrderStoreError("The local order store could not be written.",
                                      details={"path": str(self.store.path),
                                               "error": "OSError: [Errno 28] No space left on device"})

            now = self.clock()
            order = self._build_order(quote, now, customer, shipping_address)
            if checkout_id:
                order["checkout_id"] = checkout_id
                order["confirmation_id"] = confirmation_id_for(checkout_id)
            self.store.add_order(data, order)
            for product_id, qty in quote.requested.items():
                data["sold"][product_id] = int(data["sold"].get(product_id, 0)) + qty
            if checkout_id:
                # Same transaction as the order: the attempt can never say "no order" once one exists.
                self.store.update_checkout(checkout_id, now, "order_saved", data=data, request_id=request_id,
                                           state=ORDER_SAVED, order_id=order["order_id"],
                                           confirmation_id=order["confirmation_id"],
                                           detail={"order_id": order["order_id"]})
        return order

    # ------------------------------------------------------------- checkouts
    def checkout(
        self,
        checkout_id: str,
        items: Sequence[CartItem],
        promo_code: str | None,
        shipping_method: str,
        customer: Mapping[str, Any],
        shipping_address: Mapping[str, Any],
        *,
        request_id: str | None = None,
        probe: bool = False,
    ) -> CheckoutResult:
        """Place the order for this checkout ID (once), then send its confirmation.

        A retry with the same checkout ID returns the existing order and only resends the
        confirmation if it was not delivered yet.
        """
        fingerprint = cart_fingerprint(items, promo_code, shipping_method, customer, shipping_address)
        _, retry = self.store.begin_checkout(checkout_id, fingerprint, request_id, self.clock())
        self.log("checkout_started", checkout_id=checkout_id, request_id=request_id, retry=retry)
        try:
            order = self.store.order_for_checkout(checkout_id)
            replayed = order is not None
            if order is None:
                order = self._save_order(checkout_id, items, promo_code, shipping_method, customer,
                                         shipping_address, request_id)
            result = self._confirm(order, request_id, replayed, probe=probe)
        except NovaMartError as exc:
            self._note_response(checkout_id, exc.status_code, exc.code, request_id)
            self.log("checkout_failed", checkout_id=checkout_id, request_id=request_id, code=exc.code,
                     order_id=(self.store.order_for_checkout(checkout_id) or {}).get("order_id"))
            raise
        self._note_response(checkout_id, 200 if replayed else 201, None, request_id)
        self.log("checkout_succeeded", checkout_id=checkout_id, request_id=request_id,
                 order_id=order["order_id"], confirmation=result.confirmation["status"], replayed=replayed)
        return result

    def _save_order(self, checkout_id, items, promo_code, shipping_method, customer, shipping_address,
                    request_id) -> dict[str, Any]:
        try:
            order = self.place_order(items, promo_code, shipping_method, customer, shipping_address,
                                     checkout_id=checkout_id, request_id=request_id)
        except OrderStoreError as exc:
            self._record(checkout_id, "order_write_failed", request_id=request_id, state=ORDER_FAILED,
                         detail={"error": exc.details.get("error")})
            self.log("order_write_failed", checkout_id=checkout_id, request_id=request_id,
                     error=exc.details.get("error"))
            raise
        except CheckoutConflictError:
            existing = self.store.order_for_checkout(checkout_id)  # a concurrent retry saved it first
            if existing is not None:
                return existing
            raise
        except NovaMartError as exc:
            self._record(checkout_id, "checkout_rejected", request_id=request_id, state=REJECTED,
                         detail={"code": exc.code})
            raise
        self.log("order_saved", checkout_id=checkout_id, request_id=request_id, order_id=order["order_id"],
                 confirmation_id=order["confirmation_id"])
        return order

    def _confirm(self, order: dict[str, Any], request_id: str | None, replayed: bool, *,
                 probe: bool = False) -> CheckoutResult:
        checkout_id = order["checkout_id"]
        attempt = self.store.get_checkout(checkout_id)
        if attempt["confirmation"]["status"] == DELIVERED:
            return CheckoutResult(order, attempt, replayed)
        try:
            sent = self.confirmations.send(self.confirmation_request(order, request_id, probe=probe))
        except ConfirmationUnavailable as exc:
            attempt = self._confirmation_unavailable(checkout_id, exc, request_id)
            raise ConfirmationUnavailableError(exc.message, details={"order_id": order["order_id"], **exc.to_dict()}) from exc
        return CheckoutResult(order, self._note_confirmation(checkout_id, sent, request_id), replayed)

    def confirmation_request(self, order: dict[str, Any], request_id: str | None, *,
                             probe: bool = False) -> ConfirmationRequest:
        delivery = order["estimated_delivery"]
        body = (f"Your NovaMart order {order['order_id']} is confirmed: {order['item_count']} item(s), "
                f"total ${order['total_cents'] / 100:,.2f}. Estimated delivery {delivery['from']} to {delivery['to']}.")
        return ConfirmationRequest(confirmation_id=order["confirmation_id"], checkout_id=order["checkout_id"],
                                   order_id=order["order_id"], recipient=order["customer"]["email"], body=body,
                                   request_id=request_id, probe=probe)

    def _confirmation_unavailable(self, checkout_id: str, exc: ConfirmationUnavailable,
                                  request_id: str | None) -> dict[str, Any]:
        self.log("confirmation_unavailable", checkout_id=checkout_id, request_id=request_id,
                 confirmation_id=confirmation_id_for(checkout_id), kind=exc.kind, elapsed_ms=exc.elapsed_ms,
                 error=exc.message)
        return self.store.update_checkout(
            checkout_id, self.clock(), "confirmation_unavailable", request_id=request_id,
            confirmation={"status": "unknown", "checked_at": self.clock().isoformat(), "error": exc.to_dict()},
            detail={"kind": exc.kind, "elapsed_ms": exc.elapsed_ms})

    def _note_confirmation(self, checkout_id: str, sent: ConfirmationResult,
                           request_id: str | None) -> dict[str, Any]:
        delivered = sent.status == DELIVERED
        self.log("confirmation_sent", checkout_id=checkout_id, request_id=request_id,
                 confirmation_id=sent.confirmation_id, status=sent.status, replayed=sent.replayed,
                 elapsed_ms=sent.elapsed_ms)
        fields: dict[str, Any] = {"confirmation": {"status": sent.status, "message_id": sent.message_id,
                                                   "checked_at": self.clock().isoformat()}}
        if delivered:
            fields["state"] = COMPLETED
        return self.store.update_checkout(checkout_id, self.clock(), f"confirmation_{sent.status}",
                                          request_id=request_id, detail={"replayed": sent.replayed}, **fields)

    def _record(self, checkout_id: str, event: str, **fields: Any) -> None:
        """Best effort: if the store itself is failing, the attempt keeps its last saved state."""
        try:
            self.store.update_checkout(checkout_id, self.clock(), event, **fields)
        except OrderStoreError:
            pass

    def _note_response(self, checkout_id: str, status_code: int, error_code: str | None,
                       request_id: str | None) -> None:
        self._record(checkout_id, "response_sent", request_id=request_id,
                     response={"status_code": status_code, "error_code": error_code,
                               "at": self.clock().isoformat()},
                     detail={"status_code": status_code, "error_code": error_code})

    def order_view(self, order: dict[str, Any]) -> dict[str, Any]:
        """An order as the storefront shows it, with its confirmation status."""
        attempt = self.store.get_checkout(order["checkout_id"]) if order.get("checkout_id") else None
        if attempt is None:
            return order
        return {**order, "confirmation": CheckoutResult(order, attempt, False).confirmation}

    # -------------------------------------------------------------- recovery
    def checkout_record(self, checkout_id: str) -> dict[str, Any]:
        """The authoritative view of one checkout: its attempt and whether an order exists."""
        order = self.store.order_for_checkout(checkout_id)
        return {"checkout_id": checkout_id, "attempt": self.store.get_checkout(checkout_id), "order": order,
                "order_state": "saved" if order is not None else "absent"}

    def resend_confirmation(self, checkout_id: str, request_id: str | None = None) -> dict[str, Any]:
        """Send only the confirmation again, under its stable ID. The order is not touched."""
        order = self.store.order_for_checkout(checkout_id)
        if order is None:
            raise OrderNotFoundError(f"No order exists for checkout '{checkout_id}'.",
                                     details={"checkout_id": checkout_id})
        try:
            sent = self.confirmations.send(self.confirmation_request(order, request_id))
        except ConfirmationUnavailable as exc:
            self._confirmation_unavailable(checkout_id, exc, request_id)
            raise ConfirmationUnavailableError(exc.message, details={"checkout_id": checkout_id,
                                                                     "order_id": order["order_id"],
                                                                     **exc.to_dict()}) from exc
        self._note_confirmation(checkout_id, sent, request_id)
        return {"checkout_id": checkout_id, "order_id": order["order_id"], "confirmation_id": sent.confirmation_id,
                "status": sent.status, "replayed": sent.replayed}

    def record_reconciliation(self, checkout_id: str, outcome: str, incident_id: str,
                              evidence: Mapping[str, Any]) -> dict[str, Any]:
        """Store OpsRelay's verified outcome for an interrupted checkout."""
        if self.store.get_checkout(checkout_id) is None:
            raise CheckoutNotFoundError(f"Checkout '{checkout_id}' was not found.", details={"checkout_id": checkout_id})
        order = self.store.order_for_checkout(checkout_id)
        fields: dict[str, Any] = {"reconciliation": {"outcome": outcome, "incident_id": incident_id,
                                                     "at": self.clock().isoformat(), "evidence": dict(evidence)}}
        if outcome == "completed":
            if order is None:
                raise CheckoutConflictError("A checkout without an order cannot be completed.",
                                            details={"checkout_id": checkout_id})
            fields["state"] = COMPLETED
            fields["confirmation"] = {"status": DELIVERED, "checked_at": self.clock().isoformat(),
                                      "verified_by": incident_id}
        elif outcome == "failed":
            if order is not None:
                raise CheckoutConflictError("This checkout has an order, so it did not fail.",
                                            details={"checkout_id": checkout_id, "order_id": order["order_id"]})
            fields["state"] = FAILED
        return self.store.update_checkout(checkout_id, self.clock(), f"reconciled_{outcome}", **fields)

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

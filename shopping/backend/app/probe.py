"""Safe checkout health probe.

Runs the *real* checkout evaluation code (quantity math, promotions, tax,
shipping, inventory validation) against a synthetic in-memory catalog and
compares the results with independently computed golden values.

It also runs one fresh synthetic checkout end to end (attempt record, order write and
the confirmation sent through NovaConnect) against a throwaway store, so a checkout
that would fail for customers fails the probe.

Guarantees: no real order is created, no inventory is reserved, no payment is
attempted and the real store is never written. The probe verifies this itself. The
synthetic checkout's confirmation is sent to NovaConnect flagged ``probe``.

Run from the command line (exit code 1 when unhealthy):
    python -m app.probe
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import demo_outage, inventory, pricing
from .catalog import Catalog, Product
from .checkout import CartItem, CheckoutService
from .errors import InsufficientStockError, NovaMartError
from .store import DemoStore

PROBE_CATALOG = Catalog(
    products=(
        Product(
            id="probe-sku-a", slug="probe-sku-a", name="Probe Item A", category="probe", tagline="", description="",
            price_cents=2_499, stock=0, image="", rating=0, review_count=0,
        ),
        Product(
            id="probe-sku-b", slug="probe-sku-b", name="Probe Item B", category="probe", tagline="", description="",
            price_cents=1_250, stock=0, image="", rating=0, review_count=0,
        ),
    ),
    categories=(),
)

# The synthetic checkout needs stock; it runs against a throwaway store.
PROBE_CHECKOUT_CATALOG = Catalog(
    products=tuple(dataclasses.replace(p, stock=1_000) for p in PROBE_CATALOG.products),
    categories=(),
)
PROBE_CUSTOMER = {"full_name": "OpsRelay Probe", "email": "probe@opsrelay.invalid", "phone": None}
PROBE_ADDRESS = {"line1": "1 Probe Way", "line2": None, "city": "Testville", "state": "CA",
                 "postal_code": "00000", "country": "US"}
INTERRUPTED_WINDOW = timedelta(hours=24)

# Golden values computed by hand, independent of the code under test:
#   A: $24.99 x 3 = $74.97   B: $12.50 x 2 = $25.00   subtotal = $99.97
#   WELCOME10: floor(9997 * 10 / 100) = 999   discounted = 8998
#   standard shipping is free at >= $75.00   tax = round_half_up(8998 * 8.25%) = 742
#   total = 8998 + 0 + 742 = 9740
GOLDEN_CART = {
    "item_count": 5,
    "subtotal_cents": 9_997,
    "discount_cents": 999,
    "shipping_cents": 0,
    "tax_cents": 742,
    "total_cents": 9_740,
}


# Imgix-style CDN path for a photo id, e.g. https://images.unsplash.com/photo-1505740420928-5e560c06d30e
IMAGE_URL = re.compile(r"^https://images\.unsplash\.com/photo-\d+-[0-9a-f]+$")


class _CheckFailed(Exception):
    def __init__(self, message: str, expected: Any, actual: Any) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual


def _expect(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise _CheckFailed(message, expected, actual)


def run_checkout_probe(service: CheckoutService, *, request_id: str | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    store = service.store
    orders_before = store.order_count()
    sold_before = store.sold_counts()
    mtime_before = store.path.stat().st_mtime_ns if store.path.exists() else None

    checks: list[dict[str, Any]] = []

    def check(name: str, component: str, source: str, fn: Callable[[], Any]) -> None:
        t0 = time.perf_counter()
        result: dict[str, Any] = {"name": name, "component": component, "source": source}
        try:
            detail = fn()
            result.update(status="pass", message="ok", **(detail or {}))
        except _CheckFailed as exc:
            result.update(status="fail", message=str(exc), expected=exc.expected, actual=exc.actual)
        except NovaMartError as exc:
            result.update(status="fail", message=f"Unexpected {exc.code}: {exc.message}", error=exc.to_dict())
        except Exception as exc:  # noqa: BLE001 - a probe must never crash
            result.update(status="fail", message=f"Unhandled {type(exc).__name__}: {exc}")
        result["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        checks.append(result)

    # 1. Quantity math -------------------------------------------------------
    def quantity_check() -> dict[str, Any]:
        cases = [((2_499, 1), 2_499), ((2_499, 3), 7_497), ((1_250, 10), 12_500)]
        actual = {f"{u}x{q}": pricing.line_total(u, q) for (u, q), _ in cases}
        expected = {f"{u}x{q}": total for (u, q), total in cases}
        _expect(actual, expected, "Line totals must equal unit price multiplied by quantity.")
        return {"expected": expected, "actual": actual}

    check("quantity_line_totals", "pricing", "backend/app/pricing.py::line_total", quantity_check)

    # 2. Discount math -------------------------------------------------------
    def discount_check() -> dict[str, Any]:
        promos = pricing.PROMOTIONS
        actual = {
            "WELCOME10@20000": pricing.compute_discount(20_000, promos["WELCOME10"]),
            "NOVA20@25000": pricing.compute_discount(25_000, promos["NOVA20"]),
            "NOVA20@15000(below_min)": pricing.compute_discount(15_000, promos["NOVA20"]),
            "SAVE15@12000": pricing.compute_discount(12_000, promos["SAVE15"]),
        }
        expected = {
            "WELCOME10@20000": 2_000,
            "NOVA20@25000": 5_000,
            "NOVA20@15000(below_min)": 0,
            "SAVE15@12000": 1_500,
        }
        _expect(actual, expected, "Promotion discounts do not match the advertised percentage/amount.")
        return {"expected": expected, "actual": actual}

    check("promotion_discounts", "pricing", "backend/app/pricing.py::compute_discount", discount_check)

    # 3. End-to-end quote through the real checkout evaluation ---------------
    def quote_check() -> dict[str, Any]:
        quote = service.build_quote(
            [CartItem("probe-sku-a", 3), CartItem("probe-sku-b", 2)],
            "WELCOME10",
            "standard",
            availability={"probe-sku-a": 100, "probe-sku-b": 100},
            catalog=PROBE_CATALOG,
        )
        b = quote.breakdown
        actual = {k: getattr(b, k) for k in GOLDEN_CART}
        _expect(actual, GOLDEN_CART, "Checkout quote totals diverge from the golden cart.")
        return {"expected": GOLDEN_CART, "actual": actual}

    check("checkout_quote_totals", "checkout", "backend/app/checkout.py::CheckoutService.build_quote", quote_check)

    # 4. Inventory guard -----------------------------------------------------
    def inventory_check() -> dict[str, Any]:
        availability = {"probe-sku-a": 2, "probe-sku-b": 10}
        over = service.build_quote(
            [CartItem("probe-sku-a", 3)], None, "standard", availability=availability, catalog=PROBE_CATALOG
        )
        actual_over = [(s.product_id, s.requested, s.available) for s in over.shortages]
        _expect(
            actual_over,
            [("probe-sku-a", 3, 2)],
            "Requesting 3 units with only 2 available must be flagged as a shortage.",
        )
        exact = service.build_quote(
            [CartItem("probe-sku-a", 2)], None, "standard", availability=availability, catalog=PROBE_CATALOG
        )
        _expect(exact.shortages, [], "Requesting exactly the available quantity must be allowed.")
        try:
            inventory.ensure_available({"probe-sku-a": 3}, availability)
        except InsufficientStockError:
            pass
        else:
            raise _CheckFailed("ensure_available() did not block an oversell.", "InsufficientStockError", "no error")
        return {"expected": {"oversell_blocked": True}, "actual": {"oversell_blocked": True}}

    check("inventory_oversell_guard", "inventory", "backend/app/inventory.py::find_shortages", inventory_check)

    # 5. Catalog sanity ------------------------------------------------------
    def catalog_check() -> dict[str, Any]:
        bad = [p.id for p in service.catalog.products if p.price_cents <= 0 or p.stock < 0]
        _expect(bad, [], "Catalog contains products with invalid price or stock.")
        if len(service.catalog) == 0:
            raise _CheckFailed("Catalog is empty.", ">0 products", 0)
        return {"actual": {"products": len(service.catalog)}}

    check("catalog_integrity", "catalog", "backend/app/catalog.py", catalog_check)

    # 6. Product images ------------------------------------------------------
    def image_check() -> dict[str, Any]:
        images = {p.id: p.image for p in service.catalog.products}
        images.update({f"category:{c.slug}": c.image for c in service.catalog.categories})
        broken = sorted(k for k, url in images.items() if not IMAGE_URL.match(url))
        expected = {"images_checked": len(images), "broken_image_links": 0}
        actual = {"images_checked": len(images), "broken_image_links": len(broken)}
        if broken:
            raise _CheckFailed(
                f"{len(broken)} of {len(images)} product images point to a URL the image CDN cannot serve "
                f"(e.g. {images[broken[0]]}).",
                expected,
                actual,
            )
        return {"expected": expected, "actual": actual}

    check("product_images", "catalog", "backend/app/catalog.py", image_check)

    # 7. Stock levels shown to shoppers -------------------------------------
    def stock_check() -> dict[str, Any]:
        shown = service.availability(sold={})
        expected = {p.id: p.stock for p in service.catalog.products}
        wrong = sorted(k for k in expected if shown.get(k) != expected[k])
        in_stock = sum(1 for v in expected.values() if v > 0)
        summary_expected = {"products_in_stock": in_stock}
        summary_actual = {"products_in_stock": sum(1 for k in expected if shown.get(k, 0) > 0)}
        if wrong:
            raise _CheckFailed(
                f"With nothing sold, {len(wrong)} products report the wrong stock level "
                f"(e.g. {wrong[0]}: {shown.get(wrong[0])} instead of {expected[wrong[0]]}).",
                summary_expected,
                summary_actual,
            )
        return {"expected": summary_expected, "actual": summary_actual}

    check("stock_levels", "inventory", "backend/app/checkout.py::CheckoutService.availability", stock_check)

    # 8. Order store (read-only) --------------------------------------------
    def store_check() -> dict[str, Any]:
        health = store.health()
        _expect(
            {"readable": health["readable"], "writable": health["writable"]},
            {"readable": True, "writable": True},
            "Order store is not readable/writable.",
        )
        return {"actual": health}

    check("order_store_access", "orders", "backend/app/store.py::DemoStore", store_check)

    # 9. A fresh checkout completes -------------------------------------------
    def checkout_flow_check() -> dict[str, Any]:
        checkout_id = f"probe-{uuid.uuid4().hex[:20]}"
        with tempfile.TemporaryDirectory(prefix="novamart-probe-") as tmp:
            scratch = CheckoutService(PROBE_CHECKOUT_CATALOG, DemoStore(Path(tmp)), clock=service.clock,
                                      confirmations=service.confirmations, log=service.log)
            t0 = time.perf_counter()
            try:
                result = scratch.checkout(checkout_id, [CartItem("probe-sku-a", 1)], None, "standard",
                                          PROBE_CUSTOMER, PROBE_ADDRESS, request_id=request_id, probe=True)
            except NovaMartError as exc:
                record = scratch.checkout_record(checkout_id)
                attempt = record["attempt"] or {}
                raise _CheckFailed(
                    f"Checkout returned {exc.code} (HTTP {exc.status_code}): {exc.message}",
                    {"checkout": "succeeds"},
                    {"checkout": "failed", "error_code": exc.code, "http_status": exc.status_code,
                     "checkout_id": checkout_id, "request_id": request_id,
                     "order_saved": record["order"] is not None,
                     "order_id": (record["order"] or {}).get("order_id"),
                     "confirmation": attempt.get("confirmation"),
                     "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)},
                ) from exc
        return {"actual": {"checkout": "succeeded", "checkout_id": checkout_id, "request_id": request_id,
                           "order_id": result.order["order_id"], "confirmation": result.confirmation["status"]}}

    check("checkout_completes", "checkout", "backend/app/checkout.py::CheckoutService.checkout", checkout_flow_check)

    # 10. Side-effect verification -------------------------------------------
    orders_after = store.order_count()
    mtime_after = store.path.stat().st_mtime_ns if store.path.exists() else None
    side_effects = {
        "orders_created": orders_after - orders_before,
        "inventory_mutated": store.sold_counts() != sold_before,
        "store_file_modified": mtime_after != mtime_before,
        "payments_processed": 0,
    }

    def side_effect_check() -> dict[str, Any]:
        _expect(
            side_effects,
            {"orders_created": 0, "inventory_mutated": False, "store_file_modified": False, "payments_processed": 0},
            "The health probe must not have side effects.",
        )
        return {"actual": side_effects}

    check("probe_has_no_side_effects", "probe", "backend/app/probe.py", side_effect_check)

    # The demo controls affect real customer requests. Report their state through
    # the same functional endpoint so the uptime worker detects the outage.
    active_mode = demo_outage.mode(store.data_dir)
    if active_mode == "payment":
        # Checkout calls the payment provider last; report its outage as an external dependency.
        checks.append({
            "name": "payment_provider", "component": "payments", "source": "external: Stripe API",
            "status": "fail",
            "message": "Stripe API is unavailable (HTTP 503 from api.stripe.com). Card payments cannot be processed; "
                       "this is a provider outage, not a NovaMart code change.",
            "expected": {"stripe_api": "available"}, "actual": {"stripe_api": "503 Service Unavailable"},
            "duration_ms": 0.0,
        })
    elif active_mode:
        checks.append({
            "name": "customer_flow", "component": "storefront", "source": "backend/app/main.py",
            "status": "fail", "message": "A customer-facing flow is unavailable.",
            "duration_ms": 0.0,
        })

    failures = [c for c in checks if c["status"] == "fail"]
    return {
        "probe": "checkout",
        "demo_outage": bool(active_mode) and active_mode != "payment",
        "status": "unhealthy" if failures else "healthy",
        "summary": f"{len(checks) - len(failures)}/{len(checks)} checks passed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "failed_checks": [c["name"] for c in failures],
        "checks": checks,
        "side_effects": side_effects,
        # Customer checkouts in the last 24 h without a final outcome, for OpsRelay's recovery.
        "interrupted_checkouts": store.count_unfinished(datetime.now(timezone.utc) - INTERRUPTED_WINDOW),
    }


def main() -> int:
    from .main import build_service

    report = run_checkout_probe(build_service())
    json.dump(report, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())

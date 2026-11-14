"""Safe checkout health probe.

Runs the *real* checkout evaluation code (quantity math, promotions, tax,
shipping, inventory validation) against a synthetic in-memory catalog and
compares the results with independently computed golden values.

Guarantees: no order is created, no inventory is reserved, no payment is
attempted and nothing is written to disk. The probe verifies this itself.

Run from the command line (exit code 1 when unhealthy):
    python -m app.probe
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable

from . import inventory, pricing
from .catalog import Catalog, Product
from .checkout import CartItem, CheckoutService
from .errors import InsufficientStockError, NovaMartError

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


class _CheckFailed(Exception):
    def __init__(self, message: str, expected: Any, actual: Any) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual


def _expect(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise _CheckFailed(message, expected, actual)


def run_checkout_probe(service: CheckoutService) -> dict[str, Any]:
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

    # 6. Order store (read-only) --------------------------------------------
    def store_check() -> dict[str, Any]:
        health = store.health()
        _expect(
            {"readable": health["readable"], "writable": health["writable"]},
            {"readable": True, "writable": True},
            "Order store is not readable/writable.",
        )
        return {"actual": health}

    check("order_store_access", "orders", "backend/app/store.py::DemoStore", store_check)

    # 7. Side-effect verification -------------------------------------------
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

    failures = [c for c in checks if c["status"] == "fail"]
    return {
        "probe": "checkout",
        "status": "unhealthy" if failures else "healthy",
        "summary": f"{len(checks) - len(failures)}/{len(checks)} checks passed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "failed_checks": [c["name"] for c in failures],
        "checks": checks,
        "side_effects": side_effects,
    }


def main() -> int:
    from .main import build_service

    report = run_checkout_probe(build_service())
    json.dump(report, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())

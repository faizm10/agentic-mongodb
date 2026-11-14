"""Health endpoints, the safe checkout probe, and catalog browsing."""

import pytest

from app.checkout import CartItem
from app.probe import run_checkout_probe

from ._assertions import expect_status

pytestmark = pytest.mark.opsrelay(component="health", capability="probe")


def test_liveness(client):
    body = expect_status(client.get("/api/health"), 200, check="api.health", rule="liveness must be ok")
    assert body["status"] == "ok"
    assert body["service"] == "novamart-shopping-api"


def test_checkout_probe_is_healthy(client):
    body = expect_status(client.get("/api/health/checkout"), 200, check="api.health.checkout",
                         rule="checkout probe must pass on healthy code")
    assert body["status"] == "healthy", body["failed_checks"]
    assert {c["name"] for c in body["checks"]} == {
        "quantity_line_totals",
        "promotion_discounts",
        "checkout_quote_totals",
        "inventory_oversell_guard",
        "catalog_integrity",
        "order_store_access",
        "probe_has_no_side_effects",
    }


def test_probe_has_no_side_effects(service, customer, address, data_dir):
    # Fresh store: the probe must not even create the file.
    run_checkout_probe(service)
    assert not (data_dir / "novamart-store.json").exists()

    service.place_order([CartItem("nm-app-002", 2)], None, "standard", customer, address)
    path = data_dir / "novamart-store.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    report = run_checkout_probe(service)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert report["side_effects"] == {
        "orders_created": 0,
        "inventory_mutated": False,
        "store_file_modified": False,
        "payments_processed": 0,
    }


def test_probe_reports_failures_with_expected_and_actual(service, monkeypatch):
    from app import pricing

    monkeypatch.setattr(pricing, "compute_tax", lambda taxable: 0)
    report = run_checkout_probe(service)
    assert report["status"] == "unhealthy"
    failed = {c["name"]: c for c in report["checks"] if c["status"] == "fail"}
    assert "checkout_quote_totals" in failed
    assert failed["checkout_quote_totals"]["expected"]["tax_cents"] == 742
    assert failed["checkout_quote_totals"]["actual"]["tax_cents"] == 0


# ------------------------------------------------------------------ catalog
@pytest.mark.opsrelay(component="catalog", capability="browse")
def test_catalog_has_realistic_products(client):
    products = client.get("/api/products").json()["products"]
    assert 12 <= len(products) <= 20
    assert len({p["id"] for p in products}) == len(products)
    assert len({p["slug"] for p in products}) == len(products)
    assert all(p["price_cents"] > 0 and p["image"].startswith("https://") for p in products)


@pytest.mark.opsrelay(component="catalog", capability="browse")
def test_search_filter_and_sort(client):
    hits = client.get("/api/products", params={"q": "headphones"}).json()["products"]
    assert {p["id"] for p in hits} == {"nm-aud-001", "nm-aud-002"}

    home = client.get("/api/products", params={"category": "home", "sort": "price_asc"}).json()["products"]
    assert {p["category"] for p in home} == {"home"}
    prices = [p["price_cents"] for p in home]
    assert prices == sorted(prices)

    in_stock = client.get("/api/products", params={"in_stock": True}).json()["products"]
    assert "nm-acc-001" not in {p["id"] for p in in_stock}


@pytest.mark.opsrelay(component="catalog", capability="browse")
def test_product_detail_by_slug_includes_related(client):
    body = client.get("/api/products/aura-studio-wireless-headphones").json()
    assert body["id"] == "nm-aud-001"
    assert body["related"] and all(r["category"] == "audio" for r in body["related"])
    missing = client.get("/api/products/does-not-exist")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

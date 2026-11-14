"""Demo controls must affect customer requests and the monitor, then recover."""

import json

import pytest

pytestmark = pytest.mark.opsrelay(component="demo", capability="outage_controls")


@pytest.mark.parametrize("scenario", ["payment", "images", "inventory"])
def test_demo_outage_and_restore(client, data_dir, checkout_payload, scenario):
    fault = data_dir / "opsrelay-outage.json"
    assert client.get("/api/health/checkout").status_code == 200
    data_dir.mkdir(parents=True, exist_ok=True)
    fault.write_text(json.dumps({"scenario": scenario}))

    assert client.get("/api/health/checkout").status_code == 503
    assert client.get("/api/health").status_code == 200
    if scenario == "payment":
        response = client.post("/api/checkout", json=checkout_payload([("nm-aud-001", 1)]))
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PAYMENT_UNAVAILABLE"
        assert client.get("/api/orders").json()["count"] == 0
    elif scenario == "images":
        image = client.get("/api/products").json()["products"][0]["image"]
        assert image == "/api/demo/unavailable-image"
        assert client.get(image).status_code == 404
    else:
        products = client.get("/api/products").json()["products"]
        assert all(product["available"] == 0 for product in products)
        quote = client.post("/api/cart/quote", json={"items": [{"product_id": "nm-aud-001", "quantity": 1}]}).json()
        assert quote["can_checkout"] is False

    fault.unlink()
    assert client.get("/api/health/checkout").status_code == 200

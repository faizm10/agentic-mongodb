"""Demo customer checkouts, placed through NovaMart's public checkout API like the storefront does.

Each customer gets one checkout ID and retries once with the same ID if the first try fails,
as the storefront does. During the checkout-confirmation incident these become the interrupted
checkouts that OpsRelay's recovery worker reconciles.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from .models import ServiceConfig

CUSTOMERS = [
    ("Ada Lovelace", "ada@example.com"), ("Grace Hopper", "grace@example.com"),
    ("Alan Turing", "alan@example.com"), ("Katherine Johnson", "katherine@example.com"),
    ("Edsger Dijkstra", "edsger@example.com"), ("Barbara Liskov", "barbara@example.com"),
]
PRODUCTS = ["nm-app-002", "nm-hom-002", "nm-app-001", "nm-hom-001"]
ADDRESS = {"line1": "500 Market Street", "city": "San Francisco", "state": "CA", "postal_code": "94105",
           "country": "US"}
TIMEOUT_SECONDS = 15


async def place_checkouts(service: ServiceConfig, count: int,
                          transport: httpx.AsyncBaseTransport | None = None) -> list[dict[str, Any]]:
    url = service.backend_url.rstrip("/") + "/api/checkout"
    results = []
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=transport) as client:
        for i in range(count):
            name, email = CUSTOMERS[i % len(CUSTOMERS)]
            checkout_id = f"chk_{uuid.uuid4().hex[:20]}"
            payload = {"checkout_id": checkout_id, "items": [{"product_id": PRODUCTS[i % len(PRODUCTS)], "quantity": 1}],
                       "shipping_method": "standard", "customer": {"full_name": name, "email": email},
                       "shipping_address": ADDRESS}
            tries = []
            for _ in range(2):
                try:
                    response = await client.post(url, json=payload)
                    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                    tries.append({"status": response.status_code, "order_id": body.get("order_id"),
                                  "error_code": (body.get("error") or {}).get("code")})
                    if response.status_code < 500:
                        break
                except httpx.HTTPError as exc:
                    tries.append({"status": None, "error_code": type(exc).__name__})
            results.append({"checkout_id": checkout_id, "customer": name, "tries": tries})
    return results

"""NovaMart FastAPI application.

Run locally:
    uvicorn app.main:app --reload --port 8100
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, demo_outage, pricing
from .catalog import Catalog, Product
from .checkout import CartItem, CheckoutService, quote_to_dict
from .errors import NovaMartError
from .probe import run_checkout_probe
from .schemas import CheckoutRequest, QuoteRequest
from .store import DemoStore

SERVICE_NAME = "novamart-shopping-api"
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

logger = logging.getLogger("novamart")


def _log(level: int, event: str, **fields: Any) -> None:
    """Emit one JSON log line so log shippers / AI investigators can parse it."""
    logger.log(level, json.dumps({"event": event, "service": SERVICE_NAME, **fields}, default=str))


def build_service(data_dir: Path | str | None = None) -> CheckoutService:
    directory = Path(data_dir or os.environ.get("NOVAMART_DATA_DIR") or DEFAULT_DATA_DIR)
    return CheckoutService(Catalog(), DemoStore(directory))


def product_to_dict(product: Product, available: int, data_dir: Path | None = None) -> dict[str, Any]:
    return {
        "id": product.id,
        "slug": product.slug,
        "name": product.name,
        "category": product.category,
        "tagline": product.tagline,
        "description": product.description,
        "price_cents": product.price_cents,
        "compare_at_cents": product.compare_at_cents,
        "image": demo_outage.image_url(product.image, data_dir) if data_dir else product.image,
        "rating": product.rating,
        "review_count": product.review_count,
        "features": list(product.features),
        "colors": list(product.colors),
        "badge": product.badge,
        "featured": product.featured,
        "available": max(0, available),
        "in_stock": available > 0,
    }


def create_app(data_dir: Path | str | None = None) -> FastAPI:
    logging.basicConfig(level=os.environ.get("NOVAMART_LOG_LEVEL", "INFO"), format="%(message)s")
    service = build_service(data_dir)

    app = FastAPI(
        title="NovaMart Shopping API",
        version=__version__,
        description="Demo e-commerce backend for the OpsRelay hackathon. Payments are simulated.",
    )
    app.state.service = service

    origins = os.environ.get("NOVAMART_CORS_ORIGINS", "http://localhost:3100,http://127.0.0.1:3100")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------ middleware
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api"):
            _log(
                logging.INFO,
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                request_id=request_id,
            )
        return response

    def _error_response(request: Request, status: int, error: dict[str, Any]) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        _log(logging.WARNING, "api_error", status=status, path=request.url.path, request_id=request_id, error=error)
        return JSONResponse(status_code=status, content={"error": error, "request_id": request_id})

    @app.exception_handler(NovaMartError)
    async def handle_domain_error(request: Request, exc: NovaMartError):
        return _error_response(request, exc.status_code, exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(p) for p in err["loc"] if p != "body"), "message": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        return _error_response(
            request,
            422,
            {
                "code": "VALIDATION_FAILED",
                "component": "api",
                "message": "; ".join(f"{f['field']}: {f['message']}" for f in fields) or "Invalid request.",
                "details": {"fields": fields},
            },
        )

    # ---------------------------------------------------------------- health
    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "version": __version__,
            "time": datetime.now(timezone.utc).isoformat(),
            "products": len(service.catalog),
        }

    @app.get("/api/health/checkout", tags=["health"])
    def checkout_health() -> JSONResponse:
        report = run_checkout_probe(service)
        if report["status"] != "healthy":
            _log(logging.ERROR, "checkout_probe_failed", failed_checks=report["failed_checks"], report=report)
        return JSONResponse(status_code=200 if report["status"] == "healthy" else 503, content=report)

    # --------------------------------------------------------------- catalog
    @app.get("/api/categories", tags=["catalog"])
    def categories() -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for p in service.catalog.products:
            counts[p.category] = counts.get(p.category, 0) + 1
        return [
            {"slug": c.slug, "name": c.name, "blurb": c.blurb,
             "image": demo_outage.image_url(c.image, service.store.data_dir), "product_count": counts.get(c.slug, 0)}
            for c in service.catalog.categories
        ]

    @app.get("/api/products", tags=["catalog"])
    def list_products(
        q: str | None = Query(default=None, max_length=80),
        category: str | None = None,
        sort: str = "featured",
        in_stock: bool = False,
        min_price: int | None = Query(default=None, ge=0, description="cents"),
        max_price: int | None = Query(default=None, ge=0, description="cents"),
        featured: bool = False,
    ) -> dict[str, Any]:
        available = service.availability()
        sort_key = sort if sort in {"featured", "price_asc", "price_desc", "rating", "name"} else "featured"
        products = service.catalog.search(
            query=q,
            category=category,
            sort=sort_key,  # type: ignore[arg-type]
            in_stock_only=in_stock,
            min_price_cents=min_price,
            max_price_cents=max_price,
            availability=lambda p: available[p.id],
        )
        if featured:
            products = [p for p in products if p.featured]
        return {"count": len(products), "products": [product_to_dict(p, available[p.id], service.store.data_dir) for p in products]}

    @app.get("/api/products/{product_id}", tags=["catalog"])
    def get_product(product_id: str) -> dict[str, Any]:
        product = service.catalog.get(product_id)
        available = service.availability()
        related = [p for p in service.catalog.products if p.category == product.category and p.id != product.id][:4]
        return {
            **product_to_dict(product, available[product.id], service.store.data_dir),
            "related": [product_to_dict(p, available[p.id], service.store.data_dir) for p in related],
        }

    @app.get("/api/promotions", tags=["catalog"])
    def promotions() -> list[dict[str, Any]]:
        return [
            {"code": p.code, "description": p.description, "min_subtotal_cents": p.min_subtotal_cents}
            for p in pricing.PROMOTIONS.values()
        ]

    # ------------------------------------------------------------- checkout
    @app.post("/api/cart/quote", tags=["checkout"])
    def quote(body: QuoteRequest) -> dict[str, Any]:
        items = [CartItem(i.product_id, i.quantity) for i in body.items]
        result = quote_to_dict(service.quote(items, body.promo_code, body.shipping_method))
        for line in result["lines"]:
            line["image"] = demo_outage.image_url(line["image"], service.store.data_dir)
        return result

    @app.post("/api/checkout", status_code=201, tags=["checkout"])
    def checkout(body: CheckoutRequest, request: Request) -> dict[str, Any]:
        items = [CartItem(i.product_id, i.quantity) for i in body.items]
        order = service.place_order(
            items,
            body.promo_code,
            body.shipping_method,
            customer=body.customer.model_dump(),
            shipping_address=body.shipping_address.model_dump(),
        )
        _log(
            logging.INFO,
            "order_created",
            order_id=order["order_id"],
            total_cents=order["total_cents"],
            item_count=order["item_count"],
            request_id=request.state.request_id,
        )
        return order

    # ---------------------------------------------------------------- orders
    @app.get("/api/orders", tags=["orders"])
    def list_orders(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        orders = service.store.list_orders(limit)
        return {"count": len(orders), "orders": orders}

    @app.get("/api/orders/{order_id}", tags=["orders"])
    def get_order(order_id: str) -> dict[str, Any]:
        return service.store.get_order(order_id)

    return app


app = create_app()

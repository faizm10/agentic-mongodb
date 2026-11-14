# NovaMart: Demo Shopping App for OpsRelay

NovaMart is a premium-looking demo e-commerce store built for the **OpsRelay** hackathon. It provides:

- a polished **Next.js + TypeScript + Tailwind** storefront (homepage, search, filters, product pages, cart, checkout, order confirmation)
- a **FastAPI** backend with product, cart-quote, checkout, order and health endpoints, persisting demo orders to a local JSON file
- **85 Pytest tests** covering pricing, quantity, discounts, inventory, orders and checkout
- a **side-effect-free checkout health probe** that exercises the real checkout logic
- **`inject_bug.py`**, which injects and resets three reproducible production bugs for OpsRelay's AI investigator to diagnose

> **Payments are simulated.** No card details are collected and nothing is ever charged. Orders are demo records only.

The app is fully self-contained inside `shopping/`. It does not depend on `agents/`, `admin-dashboard/` or `message-platform/`.

---

## Contents

1. [Architecture](#architecture)
2. [Quick start](#quick-start)
3. [Development commands](#development-commands)
4. [API reference](#api-reference)
5. [Business rules](#business-rules)
6. [Tests](#tests)
7. [Checkout health probe](#checkout-health-probe)
8. [Bug injection](#bug-injection)
9. [Structured failure output for OpsRelay](#structured-failure-output-for-opsrelay)
10. [GitHub Actions integration](#github-actions-integration)
11. [Demo script](#demo-script)
12. [Troubleshooting](#troubleshooting)

---

## Architecture

```
shopping/
├── backend/                    FastAPI service (port 8100)
│   ├── app/
│   │   ├── main.py             HTTP API, error handlers, JSON logging, request IDs
│   │   ├── catalog.py          19 products across 6 categories
│   │   ├── pricing.py          line totals, promotions, shipping, tax      ← bugs 1 & 2
│   │   ├── inventory.py        stock validation / oversell guard           ← bug 3
│   │   ├── checkout.py         quote + order placement (shared evaluation path)
│   │   ├── store.py            atomic JSON persistence for orders + sold stock
│   │   ├── probe.py            safe checkout health probe (also a CLI)
│   │   ├── schemas.py          request validation (Pydantic)
│   │   └── errors.py           structured domain errors
│   ├── tests/                  Pytest suite + OpsRelay reporting plugin
│   ├── requirements.txt
│   └── pytest.ini
├── frontend/                   Next.js 15 App Router storefront (port 3100)
│   └── src/
│       ├── app/                /, /products, /products/[slug], /cart, /checkout, /orders/[orderId]
│       ├── components/         header, cart drawer, product cards, gallery, order summary…
│       └── lib/                API client, persistent cart (localStorage), server-priced quotes
├── ci/shopping-ci.yml          GitHub Actions workflow template
├── inject_bug.py               bug injection / reset tool
└── Makefile                    shortcuts for common commands
```

**Request flow:** the browser talks only to Next.js. Requests to `/api/*` are proxied by a Next.js rewrite to the FastAPI backend (`BACKEND_URL`, default `http://127.0.0.1:8100`), so there is no CORS setup. Server-rendered pages call the backend directly.

**Pricing is server-authoritative.** The cart and checkout pages display totals from `POST /api/cart/quote`, so an injected backend bug is visible in the UI, not just in tests.

---

## Quick start

Prerequisites: **Python 3.11+** (tested on 3.14) and **Node.js 18.18+** (tested on 20.19).

```bash
cd shopping

# 1. Backend
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt

# 2. Frontend
cd frontend && npm ci && cd ..
```

Run in **two terminals**:

```bash
# Terminal 1: API on http://localhost:8100 (docs at /docs)
cd shopping/backend
.venv/bin/uvicorn app.main:app --reload --port 8100
```

```bash
# Terminal 2: storefront on http://localhost:3100
cd shopping/frontend
npm run dev
```

Open **http://localhost:3100**.

If the backend is not running, the storefront shows a friendly "store is waking up" page with the command to start it.

### Environment variables

| Variable | Default | Used by | Purpose |
| --- | --- | --- | --- |
| `BACKEND_URL` | `http://127.0.0.1:8100` | frontend | Where Next.js proxies `/api/*` and fetches server-side data |
| `NOVAMART_DATA_DIR` | `backend/data` | backend | Directory holding `novamart-store.json` (orders + sold stock) |
| `NOVAMART_CORS_ORIGINS` | `http://localhost:3100,http://127.0.0.1:3100` | backend | Allowed origins for direct browser calls |
| `NOVAMART_LOG_LEVEL` | `INFO` | backend | Log level for JSON log lines |

---

## Development commands

All commands run from `shopping/`. Each has a `make` shortcut.

| Task | Command | Make |
| --- | --- | --- |
| Run API (hot reload) | `cd backend && .venv/bin/uvicorn app.main:app --reload --port 8100` | `make backend` |
| Run storefront (dev) | `cd frontend && npm run dev` | `make frontend` |
| Backend tests | `cd backend && .venv/bin/pytest` | `make test` |
| Checkout probe (CLI) | `cd backend && .venv/bin/python -m app.probe` | `make probe` |
| TypeScript check | `cd frontend && npm run typecheck` | `make typecheck` |
| Production build | `cd frontend && npm run build` then `npm run start` | `make build` |
| Bug status | `python3 inject_bug.py status` | `make bugs` |
| Reset all bugs | `python3 inject_bug.py reset all` | `make reset-bugs` |
| Reset demo orders/stock | `rm backend/data/novamart-store.json` | `make reset-data` |

---

## API reference

Interactive OpenAPI docs: **http://localhost:8100/docs**

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness: service, version, product count |
| `GET` | `/api/health/checkout` | Safe checkout probe. `200` healthy, `503` unhealthy |
| `GET` | `/api/products` | List products. Query: `q`, `category`, `sort` (`featured`/`price_asc`/`price_desc`/`rating`/`name`), `in_stock`, `featured`, `min_price`, `max_price` |
| `GET` | `/api/products/{id-or-slug}` | Product detail plus related products |
| `GET` | `/api/categories` | Categories with product counts |
| `GET` | `/api/promotions` | Available promo codes |
| `POST` | `/api/cart/quote` | Price a cart without placing an order: line totals, discount, shipping, tax, stock issues |
| `POST` | `/api/checkout` | Place a simulated order (`201`) |
| `GET` | `/api/orders` | Recent orders (newest first) |
| `GET` | `/api/orders/{order_id}` | One order |

Example checkout:

```bash
curl -s -X POST http://localhost:8100/api/checkout -H 'content-type: application/json' -d '{
  "items": [{"product_id": "nm-aud-001", "quantity": 1}, {"product_id": "nm-app-002", "quantity": 2}],
  "promo_code": "WELCOME10",
  "shipping_method": "standard",
  "customer": {"full_name": "Jordan Rivera", "email": "jordan@example.com"},
  "shipping_address": {"line1": "500 Market St", "city": "San Francisco", "state": "CA", "postal_code": "94105"}
}'
# -> total_cents: 31079 ($310.79), payment.status: "not_charged"
```

### Error format

Every error uses the same envelope, with a stable `code` and the `component` that raised it:

```json
{
  "error": {
    "code": "INSUFFICIENT_STOCK",
    "component": "inventory",
    "message": "Not enough stock for nm-wsp-001: requested 4, available 3.",
    "details": {"shortages": [{"product_id": "nm-wsp-001", "requested": 4, "available": 3, "...": "..."}]},
    "hint": "Reduce the quantity or remove the item from your cart."
  },
  "request_id": "5f2c9a0e1b7d4c3a"
}
```

Error codes: `EMPTY_CART`, `INVALID_QUANTITY`, `INVALID_PROMO_CODE`, `INVALID_SHIPPING_METHOD`, `PRODUCT_NOT_FOUND`, `INSUFFICIENT_STOCK`, `ORDER_NOT_FOUND`, `ORDER_STORE_UNAVAILABLE`, `VALIDATION_FAILED`.

Every response carries an `X-Request-ID` header (pass your own to correlate). The backend logs one JSON line per API request, error and order (`http_request`, `api_error`, `order_created`, `checkout_probe_failed`).

---

## Business rules

All money is handled as **integer cents**.

| Rule | Value |
| --- | --- |
| Quantity per line | 1–10 (duplicate lines for the same product are aggregated for stock checks) |
| Line total | `unit_price × quantity` |
| Tax | 8.25% of `(subtotal − discount)`, rounded half-up |
| Standard shipping | $5.95, **free** when `subtotal − discount ≥ $75` |
| Express shipping | $19.95 (always charged) |
| Total | `subtotal − discount + shipping + tax` |

| Promo code | Effect |
| --- | --- |
| `WELCOME10` | 10% off |
| `NOVA20` | 20% off orders of $200+ |
| `SAVE15` | $15 off orders of $100+ |
| `FREESHIP` | Free standard shipping |

Discounts never exceed the subtotal. Inventory comes from the catalog stock minus units sold, which are persisted. Some products are deliberately **low-stock** (Polar camera: 3, Form chair: 4, Ember candle: 5) and one is **sold out** (Solstice sunglasses), so you can demo the stock guard.

---

## Tests

```bash
cd shopping/backend
.venv/bin/pytest            # 85 tests, ~0.3s
```

| File | Component / capability | Covers |
| --- | --- | --- |
| `test_quantity.py` | pricing / quantity | line totals, item counts, quantity bounds, quote scaling, API line totals |
| `test_pricing.py` | pricing / totals | tax rounding, shipping thresholds, golden single-item total, total reconciliation |
| `test_discounts.py` | pricing / discounts | every promo, minimums, proportionality, case-insensitivity, invalid codes, discount before tax |
| `test_inventory.py` | inventory / stock_validation | shortages, exact-stock purchases, split-line bypass, sold-out, stock depletion across orders |
| `test_orders.py` | orders / persistence | disk persistence across restart, order shape, listing, 404s, corrupt-store handling |
| `test_checkout_api.py` | checkout / place_order | golden end-to-end order, quote/checkout parity, validation errors, no card data stored |
| `test_health_and_catalog.py` | health, catalog | liveness, probe health, probe has no side effects, catalog search/filter/sort |

Tests use a temporary data directory, so they never touch your demo orders.

---

## Checkout health probe

`GET /api/health/checkout` (or `python -m app.probe` from `backend/`) runs the **real** checkout evaluation code (`CheckoutService.build_quote`, `pricing.*`, `inventory.*`) against a synthetic in-memory catalog and compares the results with **hand-computed golden values**:

| Check | Verifies |
| --- | --- |
| `quantity_line_totals` | `line_total` for several unit-price × quantity cases |
| `promotion_discounts` | WELCOME10 / NOVA20 / SAVE15 amounts, including below-minimum |
| `checkout_quote_totals` | a full golden cart: subtotal, discount, shipping, tax, total |
| `inventory_oversell_guard` | 3 requested with 2 available is blocked; exactly 2 is allowed |
| `catalog_integrity` | prices > 0, stock ≥ 0 |
| `order_store_access` | store readable and writable (checked with `os.access`, no write) |
| `probe_has_no_side_effects` | no order created, no stock changed, store file mtime unchanged, no payment |

**Safety guarantees:** the probe never creates orders, reserves stock, writes to disk or attempts payment, and it verifies this itself in the last check. It is safe to poll in production.

Healthy response (abridged):

```json
{ "probe": "checkout", "status": "healthy", "summary": "7/7 checks passed", "failed_checks": [],
  "side_effects": {"orders_created": 0, "inventory_mutated": false, "store_file_modified": false, "payments_processed": 0} }
```

When unhealthy, it returns HTTP **503**, and each failed check includes `component`, `source` (file and function), `expected` and `actual`.

---

## Bug injection

`inject_bug.py` makes **one-line changes to application source** in `backend/app/`. It never edits `backend/tests/` and refuses to. The existing regression tests and the live health probe catch every bug.

| Bug | File | Change | Customer impact |
| --- | --- | --- | --- |
| `quantity` | `backend/app/pricing.py` | `unit_price_cents * quantity` → `unit_price_cents * max(quantity - 1, 1)` | Buying 2+ units under-charges by one unit per line |
| `discount` | `backend/app/pricing.py` | `promotion.value // 100` → `promotion.value // 10` | Percent codes give 10× the discount; WELCOME10 makes orders free |
| `inventory` | `backend/app/inventory.py` | `requested_qty > available_qty` → `available_qty <= 0` | Orders above remaining stock are accepted (overselling) |

```bash
cd shopping
python3 inject_bug.py list                 # describe bugs, files, expected failing tests
python3 inject_bug.py status               # clean / injected per bug
python3 inject_bug.py inject discount      # inject one bug (prints the diff)
python3 inject_bug.py reset discount       # reset one bug
python3 inject_bug.py reset all            # reset everything
python3 inject_bug.py verify inventory     # inject → run pytest (must fail) → reset → run pytest (must pass)
python3 inject_bug.py --json status        # machine-readable output for automation
```

Injection is idempotent and guarded: it requires the exact original line to appear exactly once, and otherwise exits with code 2 and a clear message. `git diff shopping/backend/app` always shows exactly what was injected.

If the uvicorn server runs with `--reload`, an injected bug goes live immediately, and `/api/health/checkout` flips to `503`.

---

## Structured failure output for OpsRelay

Failures are designed to be diagnosable by an AI investigator without reading stack traces.

**1. Assertion messages** name the check, the business rule, the function under test, expected vs. actual (with money formatting and delta) and the inputs:

```
[OpsRelay] pricing.discount.WELCOME10 FAILED: WELCOME10 must give '10% off your order'
  function under test: app.pricing.compute_discount
  expected: 1000 cents ($10.00)
  actual:   10000 cents ($100.00)
  delta:    9000 cents (+$90.00)
  context:  {"code": "WELCOME10", "kind": "percent", "min_subtotal_cents": 0, "subtotal_cents": 10000, "value": 10}
```

**2. A terminal summary** groups failures by component and capability:

```
=========================== OpsRelay failure summary ===========================
[pricing/discounts] tests/test_discounts.py::test_discount_amounts[WELCOME10-10000-1000] :: AssertionError: ...
```

**3. A machine-readable report** is written on every run to `backend/test-results/opsrelay-report.json`:

```json
{
  "service": "novamart-shopping-api",
  "status": "failed",
  "counts": {"passed": 76, "failed": 9},
  "failed_components": {"pricing": 7, "checkout": 1, "health": 1},
  "failures": [
    {"test": "tests/test_discounts.py::test_discount_amounts[WELCOME10-10000-1000]",
     "component": "pricing", "capability": "discounts", "phase": "call", "message": "AssertionError: [OpsRelay] ..."}
  ]
}
```

**4. Runtime signals:** the `/api/health/checkout` failure details, structured API errors with `request_id`, and JSON log lines.

---

## GitHub Actions integration

GitHub only runs workflows from `.github/workflows/` at the repository root. To keep this app self-contained, the workflow ships as a template at **`shopping/ci/shopping-ci.yml`**. To enable it:

```bash
mkdir -p .github/workflows
cp shopping/ci/shopping-ci.yml .github/workflows/shopping-ci.yml
```

It runs on changes under `shopping/**` and has two jobs:

- **backend:** installs requirements, runs Pytest with JUnit output, runs the checkout probe CLI (the step fails if the probe is unhealthy), and uploads `opsrelay-report.json`, `junit.xml` and `checkout-probe.json` as the `novamart-test-results` artifact.
- **frontend:** `npm ci`, `npm run typecheck`, `npm run build`.

**OpsRelay hook-up:** OpsRelay can watch for failed `shopping-ci` runs, download the `novamart-test-results` artifact and feed `opsrelay-report.json` and `checkout-probe.json` to the investigator. Both name the failing component, the function under test, and expected vs. actual values.

**Failure-scenario demo in CI:** commit an injected bug on a throwaway branch (`python3 inject_bug.py inject quantity && git commit -am "demo: quantity regression"`) and push. CI goes red with structured diagnostics. Run `python3 inject_bug.py reset all` before merging anything.

---

## Demo script

1. Start backend and frontend, open http://localhost:3100.
2. Browse, search ("lamp"), filter (Home, in stock, price sort), open a product, add to cart. The cart drawer shows a free-shipping progress bar.
3. In the cart, apply `WELCOME10` and switch to express shipping. Totals come from the backend.
4. Checkout → **Fill demo details** → **Place order** → order confirmation with order ID and delivery window.
5. Show `curl -s localhost:8100/api/health/checkout | jq .status` → `"healthy"`.
6. **Break it:** `python3 inject_bug.py inject discount`. With `--reload` the change is live: the cart now shows WELCOME10 taking 100% off, and the probe returns `503` with `promotion_discounts` failing.
7. Run `make test` → 9 failures with structured messages and `opsrelay-report.json`.
8. Let OpsRelay investigate, then `python3 inject_bug.py reset all` → all green.

Inventory bug demo: add 3 Polar cameras (stock 3), then raise the quantity to 4 in the cart. Healthy code shows "Only 3 left" and blocks checkout. With the bug injected, the order goes through and oversells.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Storefront shows "The store is waking up" | Backend isn't running on `BACKEND_URL`; start uvicorn on port 8100 |
| `EADDRINUSE :3100` / `:8100` | Another process holds the port: `lsof -nP -iTCP:3100 -sTCP:LISTEN`, then stop it |
| Product images don't load | Images come from `images.unsplash.com`; offline, a styled placeholder is shown instead |
| `inject_bug.py` says "expected exactly 1 occurrence" | The file was edited by hand; restore it with `git checkout -- shopping/backend/app/<file>` |
| Want a fresh store | `make reset-data` (deletes demo orders and restores stock) |
| `ORDER_STORE_UNAVAILABLE` | `backend/data/novamart-store.json` is corrupt or unwritable; delete it or fix permissions |

"""Mock incident history for training the RL fix policy.

Built from the bugs that actually exist in our apps (shopping/inject_bug.py and
message-platform/inject_bug.py: same files, tests, probe checks and symptoms), plus two
infrastructure failure types where the right move is *not* a code fix. Each incident looks
like a real CI failure or live-check failure, with varied log wording, numbers and noise, and
keeps its hidden `ground_truth` (bug class + correct playbook) for the simulator and scoring.

Stored in the `mock_incidents` collection (never in `incidents`, which the dashboard reads).
"""
from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from pymongo.database import Database

from agents import mongodb as m


# bug -> (app, correct playbook, weight, failing tests, probe check, assertion templates)
BUG_CLASSES: dict[str, dict[str, Any]] = {
    "quantity": dict(
        app="shopping", action="fix_quantity_multiplier", weight=14, probe="quantity_line_totals",
        tests=["tests/test_quantity.py::test_line_total_multiplies_quantity",
               "tests/test_pricing.py::test_line_total", "tests/test_checkout_api.py::test_quote_totals"],
        errors=["AssertionError: assert {a} == {b}  # line_total_cents for qty={q}",
                "checkout quote mismatch: expected subtotal {b} got {a} (quantity {q})",
                "E   assert line_total(unit_price_cents={u}, quantity={q}) == {b}"]),
    "discount": dict(
        app="shopping", action="fix_discount_divisor", weight=14, probe="promotion_discounts",
        tests=["tests/test_discounts.py::test_percentage_promo", "tests/test_discounts.py::test_welcome10",
               "tests/test_checkout_api.py::test_quote_totals"],
        errors=["AssertionError: discount_cents {a} != {b} for WELCOME10",
                "E   assert quote.total_cents == {b}  (got {a}; promo applied 10x)",
                "checkout total is 0 after promo code WELCOME10 (expected {b})"]),
    "inventory": dict(
        app="shopping", action="fix_stock_comparison", weight=14, probe="inventory_oversell_guard",
        tests=["tests/test_inventory.py::test_rejects_quantity_above_stock",
               "tests/test_inventory.py::test_low_stock_oversell"],
        errors=["AssertionError: order for {q} units accepted with only {s} in stock",
                "E   Failed: DID NOT RAISE InsufficientStock (requested_qty={q}, available_qty={s})",
                "oversold product sku-{u}: stock went to -{q}"]),
    "flaky_checkout": dict(
        app="shopping", action="retry_flaky_tests", weight=8, probe=None,
        tests=["tests/test_checkout_api.py::test_concurrent_checkouts"],
        errors=["E   httpx.ReadTimeout: timed out after 5.0s (passes on re-run)",
                "intermittent: test_concurrent_checkouts failed 1/{q} runs, port already in use",
                "E   ConnectionResetError during test teardown (flaky, CI runner overloaded)"]),
    "duplicate": dict(
        app="messaging", action="fix_idempotency_guard", weight=14, probe="idempotent_retry",
        tests=["tests/test_idempotency.py::test_retry_with_same_key_returns_original_message",
               "tests/test_idempotency.py::test_many_retries_store_exactly_one_message",
               "tests/test_idempotency.py::test_api_replay_returns_200_with_same_message"],
        errors=["AssertionError: assert {a} == 1  # messages stored for one idempotency key",
                "E   duplicate message delivered {q} times for idempotency_key=retry-{u}",
                "replay returned 201 Created (expected 200 with the original message)"]),
    "status": dict(
        app="messaging", action="fix_recipient_delivery", weight=14, probe="delivery_status",
        tests=["tests/test_delivery_status.py::test_sender_online_does_not_deliver_to_offline_recipient",
               "tests/test_delivery_status.py::test_group_status_is_least_advanced_recipient",
               "tests/test_delivery_status.py::test_api_status_lifecycle_with_websockets"],
        errors=["AssertionError: status 'delivered' but recipient ben is offline",
                "E   assert is_delivered(recipient_id='u{u}', online_user_ids={{'u{q}'}}) is False",
                "group message marked delivered while {q} recipients offline"]),
    "ordering": dict(
        app="messaging", action="fix_full_timestamp_sort", weight=14, probe="message_ordering",
        tests=["tests/test_ordering_and_isolation.py::test_history_is_oldest_first_across_midnight",
               "tests/test_ordering_and_isolation.py::test_sort_timeline_orders_shuffled_multi_day_input",
               "tests/test_ordering_and_isolation.py::test_seeded_history_spanning_days_is_chronological"],
        errors=["AssertionError: history not chronological: 2026-11-{d}T23:5{q} after 2026-11-{e}T08:1{q}",
                "E   assert [m['seq'] for m in history] == sorted(...)  # out of order across midnight",
                "yesterday's evening messages shown after this morning's in conversation c{u}"]),
    "ws_timeout": dict(
        app="messaging", action="increase_timeout", weight=8, probe=None,
        tests=["tests/test_persistence_realtime_monitoring.py::test_websocket_broadcast_latency"],
        errors=["E   asyncio.TimeoutError: websocket receive timed out after 2.0s",
                "probe step read_back timed out after {q}s waiting for broadcast",
                "E   TimeoutError: no broadcast within 2s (slow CI runner, latency {u}ms)"]),
}

NOISE = [
    "PytestDeprecationWarning: asyncio_default_fixture_loop_scope is unset",
    "INFO  uvicorn: Application startup complete.",
    "collected {n} items", "warnings summary: 3 warnings", "DEBUG pool size=5 overflow=10",
    "INFO  GET /api/health 200 OK", "Pydantic serializer warning: expected int",
]

LIVE_URL = {"shopping": "GET /api/health/checkout", "messaging": "GET /api/monitor/probe"}


def _fill(tpl: str, rng: random.Random) -> str:
    q = rng.randint(2, 9)
    return tpl.format(a=rng.randint(100, 9000), b=rng.randint(100, 9000), q=q, s=max(0, q - rng.randint(1, 3)),
                      u=rng.randint(10, 999), d=rng.randint(10, 13), e=rng.randint(14, 16), n=rng.randint(40, 160))


def make_incident(bug: str, rng: random.Random, idx: int, now=None) -> dict[str, Any]:
    spec = BUG_CLASSES[bug]
    app = spec["app"]
    live = spec["probe"] is not None and rng.random() < 0.4
    tests = rng.sample(spec["tests"], k=rng.randint(1, len(spec["tests"])))
    lines = [_fill(rng.choice(NOISE), rng) for _ in range(rng.randint(1, 3))]
    if live:
        source = "live"
        lines.append(f"live check failed for {app}: step synthetic {LIVE_URL[app]} http_error HTTP 503 "
                     f"failed_checks=['{spec['probe']}']")
        lines.append(_fill(rng.choice(spec["errors"]), rng))
        tests = []
    else:
        source = "ci"
        lines += [f"FAILED {t}" for t in tests]
        lines.append(_fill(rng.choice(spec["errors"]), rng))
        lines.append(f"=========== {len(tests)} failed, {rng.randint(40, 160)} passed in {rng.uniform(2, 30):.2f}s ==========")
    occurred = (now or m.utcnow()) - timedelta(days=60) + timedelta(minutes=idx * 60 * 24 * 60 / 100)
    return {
        "_id": f"mock:{idx:03d}", "app": app, "source": source, "mock": True,
        "failed_tests": tests, "failed_logs": "\n".join(lines),
        "occurred_at": occurred,
        "ground_truth": {"bug": bug, "action": spec["action"]},
    }


def generate(n: int = 100, seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    bugs = list(BUG_CLASSES)
    weights = [BUG_CLASSES[b]["weight"] for b in bugs]
    return [make_incident(rng.choices(bugs, weights)[0], rng, i) for i in range(n)]


def store(n: int = 100, seed: int = 42, db: Database | None = None) -> int:
    from agents.rl_policy import mock_incidents

    coll = mock_incidents(db)
    coll.delete_many({})
    docs = generate(n, seed)
    coll.insert_many(docs)
    coll.create_index("occurred_at", name="by_time")
    return len(docs)


# --------------------------------------------------------------------------- #
# Simulator                                                                   #
# --------------------------------------------------------------------------- #
CODE_BUGS = {b for b, s in BUG_CLASSES.items() if s["probe"]}


def success_probability(incident: dict, action: str) -> float:
    """How likely `action` is to fix this incident: the environment the policy learns in."""
    truth = incident["ground_truth"]
    if action == truth["action"]:
        return 0.95
    if action == "revert_last_commit":       # works if the bad change is the latest one
        return 0.45 if truth["bug"] in CODE_BUGS else 0.05
    if action == "add_defensive_guard":
        return 0.10 if truth["bug"] in CODE_BUGS else 0.02
    if action in ("retry_flaky_tests", "increase_timeout") and truth["bug"] not in CODE_BUGS:
        return 0.30                           # the other infra remedy sometimes helps
    return 0.02


def simulate(incident: dict, action: str, rng: random.Random) -> float:
    return 1.0 if rng.random() < success_probability(incident, action) else 0.0

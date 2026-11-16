"""Shared fixtures plus an OpsRelay reporting plugin.

Every test module declares ``pytestmark = pytest.mark.opsrelay(component=..., capability=...)``.
When a test fails, its component, capability and the structured assertion message
are collected into ``test-results/opsrelay-report.json`` so that OpsRelay's AI
investigator can triage failures without parsing console output.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.catalog import Catalog
from app.checkout import CheckoutService
from app.confirmations import ConfirmationRequest, ConfirmationResult, ConfirmationUnavailable
from app.main import create_app
from app.store import DemoStore

REPORT_DIR = Path(__file__).resolve().parent.parent / "test-results"


class FakeConfirmations:
    """In-memory NovaConnect: one record per confirmation ID, like the real service.

    ``fail_next`` makes upcoming sends fail; ``record_before_failing`` controls whether
    the failed send was still recorded (a lost response) or never arrived.
    """

    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.sends: list[ConfirmationRequest] = []
        self.fail_next = 0
        self.record_before_failing = False
        self.deliver = True

    def send(self, request: ConfirmationRequest) -> ConfirmationResult:
        self.sends.append(request)
        replayed = request.confirmation_id in self.records
        if self.fail_next and self.record_before_failing:
            self._record(request)
        if self.fail_next:
            self.fail_next -= 1
            raise ConfirmationUnavailable("timeout", "NovaConnect did not answer POST /api/confirmations within 2.0s.",
                                          elapsed_ms=2000.0)
        record = self._record(request)
        return ConfirmationResult(request.confirmation_id, record["status"], record["message_id"], replayed, 3.0)

    def _record(self, request: ConfirmationRequest) -> dict[str, Any]:
        return self.records.setdefault(request.confirmation_id, {
            "confirmation_id": request.confirmation_id, "checkout_id": request.checkout_id,
            "order_id": request.order_id, "status": "delivered" if self.deliver else "accepted",
            "message_id": f"cmsg-{len(self.records) + 1}",
        })

    def lookup(self, confirmation_id: str) -> dict[str, Any] | None:
        return self.records.get(confirmation_id)


# ---------------------------------------------------------------- fixtures
@pytest.fixture
def confirmations() -> FakeConfirmations:
    return FakeConfirmations()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def catalog() -> Catalog:
    return Catalog()


@pytest.fixture
def store(data_dir: Path) -> DemoStore:
    return DemoStore(data_dir)


@pytest.fixture
def service(catalog: Catalog, store: DemoStore, confirmations: FakeConfirmations) -> CheckoutService:
    return CheckoutService(catalog, store, confirmations=confirmations)


@pytest.fixture
def client(data_dir: Path, confirmations: FakeConfirmations) -> TestClient:
    return TestClient(create_app(data_dir=data_dir, confirmations=confirmations))


@pytest.fixture
def customer() -> dict[str, Any]:
    return {"full_name": "Ada Lovelace", "email": "ada@example.com", "phone": "+1 555 0100"}


@pytest.fixture
def address() -> dict[str, Any]:
    return {
        "line1": "500 Market Street",
        "line2": "Suite 12",
        "city": "San Francisco",
        "state": "CA",
        "postal_code": "94105",
        "country": "US",
    }


@pytest.fixture
def checkout_payload(customer, address):
    def build(items, promo_code=None, shipping_method="standard"):
        return {
            "items": [{"product_id": pid, "quantity": qty} for pid, qty in items],
            "promo_code": promo_code,
            "shipping_method": shipping_method,
            "customer": customer,
            "shipping_address": address,
        }

    return build


# ------------------------------------------------------------ OpsRelay plugin
def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "opsrelay(component, capability): tag a test with the system component and capability it guards"
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        marker = item.get_closest_marker("opsrelay")
        item.user_properties.append(("opsrelay", dict(marker.kwargs) if marker else {}))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    store = _PLUGIN_STATE
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        store["counts"][report.outcome] = store["counts"].get(report.outcome, 0) + 1
    if report.failed:
        meta = dict(report.user_properties).get("opsrelay", {})
        message = ""
        if report.longrepr is not None:
            crash = getattr(report.longrepr, "reprcrash", None)
            message = crash.message if crash is not None else str(report.longrepr)
        store["failures"].append(
            {
                "test": report.nodeid,
                "phase": report.when,
                "component": meta.get("component", "unknown"),
                "capability": meta.get("capability", "unknown"),
                "message": message,
            }
        )


_PLUGIN_STATE: dict[str, Any] = {"failures": [], "counts": {}}


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if os.environ.get("NOVAMART_DISABLE_REPORT"):
        return
    failures = _PLUGIN_STATE["failures"]
    by_component: dict[str, int] = {}
    for f in failures:
        by_component[f["component"]] = by_component.get(f["component"], 0) + 1
    report = {
        "service": "novamart-shopping-api",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if failures else "passed",
        "exit_status": int(exitstatus),
        "counts": _PLUGIN_STATE["counts"],
        "failed_components": by_component,
        "failures": failures,
    }
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "opsrelay-report.json").write_text(json.dumps(report, indent=2))


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ANN001
    failures = _PLUGIN_STATE["failures"]
    if not failures:
        return
    terminalreporter.section("OpsRelay failure summary")
    for f in failures:
        first_line = f["message"].splitlines()[0] if f["message"] else ""
        terminalreporter.write_line(f"[{f['component']}/{f['capability']}] {f['test']} :: {first_line}")
    terminalreporter.write_line(f"Machine-readable report: {REPORT_DIR / 'opsrelay-report.json'}")

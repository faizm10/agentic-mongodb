"""Fixtures and the OpsRelay reporting plugin.

Every test module declares ``pytestmark = pytest.mark.opsrelay(component=..., rule=...)``.
Failures are collected into ``test-results/opsrelay-report.json`` with service id
``messaging`` so OpsRelay can triage them without parsing console output.
"""

from __future__ import annotations

import os

os.environ.setdefault("NOVACONNECT_SKIP_APP", "1")  # never build the default app (and its DB) on import

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import conversations, db
from app.main import create_app

REPORT_DIR = Path(__file__).resolve().parent.parent / "test-results"
START = datetime(2026, 3, 14, 21, 0, tzinfo=timezone.utc)


class FakeClock:
    """Deterministic clock: each call returns a moment one second after the previous one."""

    def __init__(self, start: datetime = START, step: timedelta = timedelta(seconds=1)) -> None:
        self.current = start
        self.step = step

    def __call__(self) -> datetime:
        value = self.current
        self.current += self.step
        return value

    def jump(self, delta: timedelta) -> None:
        self.current += delta


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def conn(clock):
    """Isolated in-memory database with three users and two direct conversations."""
    connection = db.connect(":memory:")
    with connection:
        connection.executemany(
            "INSERT INTO users(id, name, handle) VALUES (?,?,?)",
            [("ana", "Ana", "ana"), ("ben", "Ben", "ben"), ("cy", "Cy", "cy"), ("dee", "Dee", "dee")],
        )
    conversations.create_conversation(connection, creator_id="ana", kind="direct", participant_ids_in=["ben"],
                                      title=None, now=clock(), conversation_id="c_ana_ben")
    conversations.create_conversation(connection, creator_id="ana", kind="direct", participant_ids_in=["cy"],
                                      title=None, now=clock(), conversation_id="c_ana_cy")
    conversations.create_conversation(connection, creator_id="ana", kind="group", participant_ids_in=["ben", "cy"],
                                      title="Trio", now=clock(), conversation_id="c_trio")
    yield connection
    connection.close()


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "novaconnect-test.db")


@pytest.fixture
def client(db_path, clock) -> TestClient:
    """API client backed by an isolated SQLite file seeded with the demo data."""
    with TestClient(create_app(db_path, clock=clock)) as c:
        yield c


def as_user(user_id: str) -> dict[str, str]:
    return {"X-Demo-User": user_id}


# ------------------------------------------------------------- OpsRelay plugin
_STATE: dict[str, Any] = {"failures": [], "counts": {}}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "opsrelay(component, rule): component and business rule a test guards")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        marker = item.get_closest_marker("opsrelay")
        item.user_properties.append(("opsrelay", dict(marker.kwargs) if marker else {}))


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        _STATE["counts"][report.outcome] = _STATE["counts"].get(report.outcome, 0) + 1
    if report.failed:
        meta = dict(report.user_properties).get("opsrelay", {})
        crash = getattr(report.longrepr, "reprcrash", None)
        _STATE["failures"].append({
            "service": "messaging",
            "test": report.nodeid,
            "phase": report.when,
            "component": meta.get("component", "unknown"),
            "rule": meta.get("rule", "unknown"),
            "message": crash.message if crash is not None else str(report.longrepr),
        })


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    failures = _STATE["failures"]
    by_rule: dict[str, int] = {}
    for f in failures:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "opsrelay-report.json").write_text(json.dumps({
        "service": "messaging",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "failed" if failures else "passed",
        "exit_status": int(exitstatus),
        "counts": _STATE["counts"],
        "failed_rules": by_rule,
        "failures": failures,
    }, indent=2))


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: ANN001
    if not _STATE["failures"]:
        return
    terminalreporter.section("OpsRelay failure summary (service: messaging)")
    for f in _STATE["failures"]:
        first = f["message"].splitlines()[0] if f["message"] else ""
        terminalreporter.write_line(f"[{f['component']} | {f['rule']}] {f['test']} :: {first}")
    terminalreporter.write_line(f"Machine-readable report: {REPORT_DIR / 'opsrelay-report.json'}")

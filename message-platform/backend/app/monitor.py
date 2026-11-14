"""Functional monitoring probe for OpsRelay (service id: ``messaging``).

Availability (``GET /health``) only says the process is up. This probe says
whether messaging actually *works*. It runs the real business-logic functions
(send, idempotency, delivery status, ordering, isolation) against a private
in-memory SQLite database seeded with deterministic probe data, so it never
sends real user messages or writes to the persistent database. The persistent
database is only opened read-only to confirm it is reachable.

CLI (exit code 1 when unhealthy):  python -m app.monitor
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import conversations, db, delivery, messages, status
from .errors import InvalidMessageError, InvalidStatusTransitionError, MessagingError

SERVICE_ID = "messaging"
PROBE_EPOCH = datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)  # deliberately just before midnight


class CheckFailed(Exception):
    def __init__(self, reason: str, *, expected: Any = None, actual: Any = None, **extra: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = {"expected": expected, "actual": actual, **extra}


def expect(actual: Any, expected: Any, reason: str, **extra: Any) -> None:
    if actual != expected:
        raise CheckFailed(reason, expected=expected, actual=actual, **extra)


class ProbeClock:
    """Deterministic clock that advances by a fixed step on every call."""

    def __init__(self, start: datetime = PROBE_EPOCH, step: timedelta = timedelta(minutes=1)) -> None:
        self.current = start
        self.step = step

    def __call__(self) -> datetime:
        value = self.current
        self.current += self.step
        return value


def _probe_db() -> tuple[sqlite3.Connection, ProbeClock]:
    conn = db.connect(":memory:")
    clock = ProbeClock()
    with conn:
        conn.executemany(
            "INSERT INTO users(id, name, handle) VALUES (?,?,?)",
            [("probe_ana", "Probe Ana", "probe-ana"), ("probe_ben", "Probe Ben", "probe-ben"),
             ("probe_cy", "Probe Cy", "probe-cy")],
        )
    for cid, members in (("probe_dm", ["probe_ana", "probe_ben"]), ("probe_other", ["probe_ana", "probe_cy"])):
        conversations.create_conversation(
            conn, creator_id=members[0], kind="direct", participant_ids_in=members, title=None, now=clock(),
            conversation_id=cid,
        )
    return conn, clock


# ----------------------------------------------------------------------- checks
def check_send_and_receive() -> dict[str, Any]:
    """Sending and receiving are checked separately so the evidence says which side broke."""
    conn, clock = _probe_db()
    sent_id, send_error = None, None
    try:
        sent, created = messages.send_message(
            conn, conversation_id="probe_dm", sender_id="probe_ana", body="probe: hello", now=clock(),
            idempotency_key="probe-send-1",
        )
        sent_id = sent["id"] if created else None
    except MessagingError as exc:
        send_error = f"{exc.code}: {exc.message}"
    if sent_id is None:
        # Seed the message directly so receiving is still tested when sending is broken.
        sent_id = "probe_seeded_msg"
        with conn:
            conn.execute(
                "INSERT INTO messages(id, conversation_id, sender_id, body, idempotency_key, reply_to_id, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (sent_id, "probe_dm", "probe_ana", "probe: hello", None, None, db.iso(clock())),
            )
            conn.execute("INSERT INTO receipts(message_id, user_id, status) VALUES (?,?,?)",
                         (sent_id, "probe_ben", status.SENT))
    receive_error = None
    try:
        history = messages.list_history(conn, "probe_dm", "probe_ben")
        received = [m["id"] for m in history] == [sent_id]
        if not received:
            receive_error = f"recipient's history held {len(history)} messages instead of the sent one"
    except MessagingError as exc:
        received, receive_error = False, f"{exc.code}: {exc.message}"
    actual = {"message_sent": send_error is None, "message_received": received}
    expected = {"message_sent": True, "message_received": True}
    if actual != expected:
        broken = [side for side, ok in (("sending", actual["message_sent"]), ("receiving", received)) if not ok]
        errors = "; ".join(e for e in (send_error, receive_error) if e)
        raise CheckFailed(f"Message {' and '.join(broken)} failed ({errors}).", expected=expected, actual=actual)
    return {"rule": "send/receive", "message_id": sent_id}


def check_idempotent_retry() -> dict[str, Any]:
    conn, clock = _probe_db()
    first, _ = messages.send_message(
        conn, conversation_id="probe_dm", sender_id="probe_ana", body="probe: retry me", now=clock(),
        idempotency_key="probe-retry-1",
    )
    second, created = messages.send_message(
        conn, conversation_id="probe_dm", sender_id="probe_ana", body="probe: retry me", now=clock(),
        idempotency_key="probe-retry-1",
    )
    count = len(messages.list_history(conn, "probe_dm", "probe_ana"))
    expect(
        {"stored_messages": count, "same_id": second["id"] == first["id"], "replay_created": created},
        {"stored_messages": 1, "same_id": True, "replay_created": False},
        "Rule 1 violated: retrying with the same idempotency key must not create a duplicate message.",
        rule="Rule 1 - no duplicate messages",
        source="backend/app/messages.py::find_duplicate",
    )
    return {"rule": "Rule 1 - no duplicate messages", "stored_messages": count}


def check_delivery_status() -> dict[str, Any]:
    conn, clock = _probe_db()
    msg, _ = messages.send_message(
        conn, conversation_id="probe_dm", sender_id="probe_ana", body="probe: status", now=clock()
    )
    # Only the *sender* is online: the recipient must NOT be marked delivered.
    delivered = delivery.record_delivery(conn, msg["id"], ["probe_ben"], {"probe_ana"}, clock())
    receipt = messages.get_message(conn, msg["id"])["receipts"][0]["status"]
    expect(
        {"delivered_to": delivered, "recipient_status": receipt},
        {"delivered_to": [], "recipient_status": "sent"},
        "Rule 2 violated: message marked delivered although the recipient is offline.",
        rule="Rule 2 - correct delivery status",
        source="backend/app/delivery.py::is_delivery_confirmed",
        online_users=["probe_ana"],
    )
    # The recipient comes online: now it is delivered, then read.
    delivery.record_delivery(conn, msg["id"], ["probe_ben"], {"probe_ben"}, clock())
    expect(messages.get_message(conn, msg["id"])["status"], "delivered", "Online recipient must be delivered.")
    delivery.mark_conversation_read(conn, "probe_dm", "probe_ben", clock())
    expect(messages.get_message(conn, msg["id"])["status"], "read", "Read conversation must mark message read.")
    try:
        delivery.set_receipt_status(conn, msg["id"], "probe_ben", status.DELIVERED, clock())
    except InvalidStatusTransitionError:
        pass
    else:
        raise CheckFailed("Receipt moved backwards from 'read' to 'delivered'.", expected="rejected", actual="allowed")
    return {"rule": "Rule 2 - correct delivery status", "lifecycle": ["sent", "delivered", "read"]}


def check_message_ordering() -> dict[str, Any]:
    conn, _ = _probe_db()
    # Messages straddle midnight: 23:58, 00:05, 08:00.
    stamps = [PROBE_EPOCH + timedelta(minutes=28), PROBE_EPOCH + timedelta(minutes=35), PROBE_EPOCH + timedelta(hours=8, minutes=30)]
    bodies = ["probe: one", "probe: two", "probe: three"]
    for body, at in zip(bodies, stamps):
        messages.send_message(conn, conversation_id="probe_dm", sender_id="probe_ana", body=body, now=at)
    history = messages.list_history(conn, "probe_dm", "probe_ben")
    expect(
        [m["body"] for m in history],
        bodies,
        "Rule 3 violated: history is not in chronological (oldest -> newest) order.",
        rule="Rule 3 - chronological ordering",
        source="backend/app/messages.py::sort_timeline",
        created_at=[m["created_at"] for m in history],
    )
    return {"rule": "Rule 3 - chronological ordering", "order": bodies}


def check_conversation_isolation() -> dict[str, Any]:
    conn, clock = _probe_db()
    a, _ = messages.send_message(conn, conversation_id="probe_dm", sender_id="probe_ana", body="probe: for ben", now=clock())
    messages.send_message(conn, conversation_id="probe_other", sender_id="probe_ana", body="probe: for cy", now=clock())
    other = [m["body"] for m in messages.list_history(conn, "probe_other", "probe_cy")]
    expect(other, ["probe: for cy"], "Rule 5 violated: a message leaked into another conversation.",
           rule="Rule 5 - conversation isolation")
    try:
        messages.send_message(
            conn, conversation_id="probe_other", sender_id="probe_ana", body="probe: bad reply", now=clock(),
            reply_to_id=a["id"],
        )
    except InvalidMessageError:
        pass
    else:
        raise CheckFailed("A reply referenced a message from another conversation.", expected="rejected", actual="allowed")
    try:
        messages.list_history(conn, "probe_dm", "probe_cy")
    except MessagingError:
        pass
    else:
        raise CheckFailed("A non-participant could read a conversation.", expected="rejected", actual="allowed")
    return {"rule": "Rule 5 - conversation isolation"}


def check_database(db_path: str) -> dict[str, Any]:
    """Rule 4 (persistence): the persistent database is reachable and has the expected schema. Read-only."""
    if db_path == ":memory:" or not Path(db_path).exists():
        raise CheckFailed("Persistent database file not found.", expected="existing SQLite file", actual=db_path)
    conn = db.connect_readonly(db_path)
    try:
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        expect(version["value"] if version else None, str(db.SCHEMA_VERSION), "Unexpected schema version.")
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("users", "conversations", "messages")}
    finally:
        conn.close()
    return {"rule": "Rule 4 - persistence", "path": db_path, "counts": counts}


CHECKS: list[tuple[str, str, Callable[..., dict[str, Any]]]] = [
    ("database_reachable", "persistence", check_database),
    ("send_and_receive", "messages", check_send_and_receive),
    ("idempotent_retry", "messages", check_idempotent_retry),
    ("delivery_status", "delivery", check_delivery_status),
    ("message_ordering", "messages", check_message_ordering),
    ("conversation_isolation", "conversations", check_conversation_isolation),
]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def run_probe(db_path: str) -> dict[str, Any]:
    started = time.perf_counter()
    persistent_before = _message_count(db_path)
    results = []
    for name, component, fn in CHECKS:
        t0 = time.perf_counter()
        result: dict[str, Any] = {"service": SERVICE_ID, "check": name, "component": component}
        try:
            details = fn(db_path) if name == "database_reachable" else fn()
            result.update(status="pass", failure_reason=None, details=details)
        except CheckFailed as exc:
            result.update(status="fail", failure_reason=exc.reason, details=exc.details)
        except Exception as exc:  # noqa: BLE001 - the probe reports, it never crashes
            result.update(status="fail", failure_reason=f"{type(exc).__name__}: {exc}", details={})
        result["timestamp"] = _timestamp()
        result["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        results.append(result)

    persistent_after = _message_count(db_path)
    side_effects = {"persistent_messages_written": (persistent_after or 0) - (persistent_before or 0)}
    if side_effects["persistent_messages_written"] != 0:
        results.append(
            {"service": SERVICE_ID, "check": "probe_has_no_side_effects", "component": "monitor", "status": "fail",
             "failure_reason": "The probe changed the persistent database.", "details": side_effects,
             "timestamp": _timestamp(), "duration_ms": 0}
        )
    failed = [r for r in results if r["status"] == "fail"]
    return {
        "service": SERVICE_ID,
        "probe": "functional",
        "status": "unhealthy" if failed else "healthy",
        "timestamp": _timestamp(),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "summary": f"{len(results) - len(failed)}/{len(results)} checks passed",
        "failed_checks": [r["check"] for r in failed],
        "checks": results,
        "side_effects": side_effects,
    }


def _message_count(db_path: str) -> int | None:
    if db_path == ":memory:" or not Path(db_path).exists():
        return None
    conn = db.connect_readonly(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = db.resolve_db_path()
    if "--init-db" in args and not Path(path).exists():
        # Explicit opt-in for CI: create and seed a fresh database before probing it.
        from .seed import seed_if_empty

        conn = db.connect(path)
        seed_if_empty(conn, datetime.now(timezone.utc))
        conn.close()
    report = run_probe(path)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())

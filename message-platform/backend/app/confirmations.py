"""Order confirmations: NovaMart asks NovaConnect to tell a customer their order is confirmed.

Business rules implemented here:

* One confirmation per confirmation ID and per checkout ID. A resend with the same ID
  returns the stored confirmation instead of recording (and delivering) a second one.
  The same ID with a different checkout or order is a conflict, never an overwrite.
* ``accepted`` means recorded; ``delivered`` means handed to the customer. A sender must
  read the confirmation back to know it was delivered: the send acknowledgement alone
  is not proof of delivery.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from .db import iso
from .errors import ConfirmationConflictError, ConfirmationNotFoundError

ACCEPTED = "accepted"
DELIVERED = "delivered"
PROBE_RETENTION = timedelta(hours=1)  # the uptime probe sends one every 30 s


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "confirmation_id": row["confirmation_id"],
        "checkout_id": row["checkout_id"],
        "order_id": row["order_id"],
        "recipient": row["recipient"],
        "probe": bool(row["probe"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "delivered_at": row["delivered_at"],
    }


def get_confirmation(conn: sqlite3.Connection, confirmation_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM confirmations WHERE confirmation_id = ?", (confirmation_id,)).fetchone()
    if row is None:
        raise ConfirmationNotFoundError(f"Confirmation '{confirmation_id}' does not exist.",
                                        details={"confirmation_id": confirmation_id})
    return _row(row)


def list_for_order(conn: sqlite3.Connection, order_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM confirmations WHERE order_id = ? ORDER BY created_at", (order_id,)).fetchall()
    return [_row(r) for r in rows]


def record_confirmation(
    conn: sqlite3.Connection,
    *,
    confirmation_id: str,
    checkout_id: str,
    order_id: str,
    recipient: str,
    body: str,
    probe: bool,
    now: datetime,
    request_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Store the confirmation once. Returns (confirmation, created)."""
    with conn:
        if probe:
            conn.execute("DELETE FROM confirmations WHERE probe = 1 AND created_at < ?", (iso(now - PROBE_RETENTION),))
        try:
            conn.execute(
                "INSERT INTO confirmations(confirmation_id, checkout_id, order_id, recipient, body, probe, status, "
                "request_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (confirmation_id, checkout_id, order_id, recipient, body, int(probe), ACCEPTED, request_id, iso(now)),
            )
            created = True
        except sqlite3.IntegrityError:
            created = False
    if created:
        return get_confirmation(conn, confirmation_id), True
    existing = conn.execute(
        "SELECT * FROM confirmations WHERE confirmation_id = ? OR checkout_id = ?", (confirmation_id, checkout_id)
    ).fetchone()
    if existing is None or (existing["confirmation_id"], existing["checkout_id"], existing["order_id"]) != (
        confirmation_id, checkout_id, order_id
    ):
        raise ConfirmationConflictError(
            "A different confirmation already exists for this ID or checkout.",
            details={"confirmation_id": confirmation_id, "checkout_id": checkout_id, "order_id": order_id,
                     "existing": _row(existing) if existing is not None else None},
        )
    return _row(existing), False


def mark_delivered(conn: sqlite3.Connection, confirmation_id: str, now: datetime) -> dict[str, Any]:
    with conn:
        conn.execute(
            "UPDATE confirmations SET status = ?, delivered_at = ? WHERE confirmation_id = ? AND status = ?",
            (DELIVERED, iso(now), confirmation_id, ACCEPTED),
        )
    return get_confirmation(conn, confirmation_id)

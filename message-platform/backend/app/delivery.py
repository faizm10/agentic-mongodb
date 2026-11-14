"""Delivery logic: when a receipt may move to 'delivered' or 'read' (Rule 2).

A message is *delivered* to a recipient only when that recipient's client has
actually received it: either it was pushed to one of their open WebSocket
connections, or they fetched the conversation history. It is *read* only after
the recipient explicitly marks the conversation as read.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from . import status
from .conversations import ensure_participant
from .db import iso


def is_delivery_confirmed(recipient_id: str, online_user_ids: set[str]) -> bool:
    """True only if the *recipient* has a live connection that received the push."""
    return recipient_id in online_user_ids


def _advance(conn: sqlite3.Connection, message_id: str, user_id: str, current: str, target: str, at: str) -> None:
    status.ensure_transition(current, target, message_id=message_id, user_id=user_id)
    column = "delivered_at" if target == status.DELIVERED else "read_at"
    conn.execute(
        f"UPDATE receipts SET status = ?, {column} = ? WHERE message_id = ? AND user_id = ?",
        (target, at, message_id, user_id),
    )


def record_delivery(
    conn: sqlite3.Connection,
    message_id: str,
    recipient_ids: Iterable[str],
    online_user_ids: set[str],
    now: datetime,
) -> list[str]:
    """After a new message is pushed, mark it delivered for recipients whose delivery is confirmed.

    Returns the recipient ids whose receipt moved to 'delivered'.
    """
    delivered: list[str] = []
    stamp = iso(now)
    with conn:
        for recipient_id in recipient_ids:
            if not is_delivery_confirmed(recipient_id, online_user_ids):
                continue
            row = conn.execute(
                "SELECT status FROM receipts WHERE message_id = ? AND user_id = ?", (message_id, recipient_id)
            ).fetchone()
            if row is not None and row["status"] == status.SENT:
                _advance(conn, message_id, recipient_id, status.SENT, status.DELIVERED, stamp)
                delivered.append(recipient_id)
    return delivered


def deliver_pending(
    conn: sqlite3.Connection, user_id: str, now: datetime, conversation_id: str | None = None
) -> list[dict[str, Any]]:
    """The user's client is present (connected or fetched history): deliver everything still 'sent'."""
    sql = (
        "SELECT r.message_id, m.conversation_id, m.sender_id FROM receipts r "
        "JOIN messages m ON m.id = r.message_id WHERE r.user_id = ? AND r.status = 'sent'"
    )
    params: list[Any] = [user_id]
    if conversation_id is not None:
        sql += " AND m.conversation_id = ?"
        params.append(conversation_id)
    rows = conn.execute(sql, params).fetchall()
    stamp = iso(now)
    changes = []
    with conn:
        for r in rows:
            _advance(conn, r["message_id"], user_id, status.SENT, status.DELIVERED, stamp)
            changes.append(
                {
                    "message_id": r["message_id"],
                    "conversation_id": r["conversation_id"],
                    "sender_id": r["sender_id"],
                    "user_id": user_id,
                    "status": status.DELIVERED,
                    "at": stamp,
                }
            )
    return changes


def mark_conversation_read(
    conn: sqlite3.Connection, conversation_id: str, user_id: str, now: datetime
) -> list[dict[str, Any]]:
    """Mark every unread message in the conversation as read for this user (delivering first if needed)."""
    ensure_participant(conn, conversation_id, user_id)
    rows = conn.execute(
        """
        SELECT r.message_id, r.status, m.sender_id FROM receipts r
        JOIN messages m ON m.id = r.message_id
        WHERE m.conversation_id = ? AND r.user_id = ? AND r.status != 'read'
        """,
        (conversation_id, user_id),
    ).fetchall()
    stamp = iso(now)
    changes = []
    with conn:
        for r in rows:
            current = r["status"]
            if current == status.SENT:
                _advance(conn, r["message_id"], user_id, status.SENT, status.DELIVERED, stamp)
                current = status.DELIVERED
            _advance(conn, r["message_id"], user_id, current, status.READ, stamp)
            changes.append(
                {
                    "message_id": r["message_id"],
                    "conversation_id": conversation_id,
                    "sender_id": r["sender_id"],
                    "user_id": user_id,
                    "status": status.READ,
                    "at": stamp,
                }
            )
    return changes


def set_receipt_status(
    conn: sqlite3.Connection, message_id: str, user_id: str, target: str, now: datetime
) -> None:
    """Explicit single-step transition (validated). Used by tests and the probe."""
    row = conn.execute(
        "SELECT status FROM receipts WHERE message_id = ? AND user_id = ?", (message_id, user_id)
    ).fetchone()
    current = row["status"] if row else "missing"
    with conn:
        _advance(conn, message_id, user_id, current, target, iso(now))

"""Messages: sending (with idempotency), history, editing, deletion, reactions and pins.

Business rules implemented here:

* Rule 1 - No duplicates: a retry with the same idempotency key returns the
  original message instead of inserting a new one (``find_duplicate``).
* Rule 3 - Ordering: history is always oldest -> newest (``sort_timeline``).
* Rule 5 - Isolation: history only contains the requested conversation, and
  replies may only reference messages from the same conversation.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Any

from . import status
from .conversations import ensure_participant, participant_ids
from .db import iso
from .errors import ForbiddenMessageActionError, InvalidMessageError, MessageNotFoundError

MAX_BODY_LENGTH = 4000
MAX_EMOJI_LENGTH = 16
MAX_IDEMPOTENCY_KEY_LENGTH = 100
DEFAULT_PAGE_SIZE = 100


def new_message_id() -> str:
    return f"m_{uuid.uuid4().hex}"


# --------------------------------------------------------------------------- rules
def validate_body(body: str) -> str:
    if not isinstance(body, str) or not body.strip():
        raise InvalidMessageError("Message body cannot be empty.", details={"field": "body"})
    if len(body) > MAX_BODY_LENGTH:
        raise InvalidMessageError(
            f"Message body exceeds {MAX_BODY_LENGTH} characters.",
            details={"field": "body", "length": len(body), "max": MAX_BODY_LENGTH},
        )
    return body.strip("\n")


def find_duplicate(
    conn: sqlite3.Connection, conversation_id: str, sender_id: str, idempotency_key: str | None
) -> sqlite3.Row | None:
    """Rule 1: return the message previously stored for this idempotency key, if any."""
    if not idempotency_key:
        return None
    return conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? AND sender_id = ? AND idempotency_key = ? "
        "ORDER BY seq LIMIT 1",
        (conversation_id, sender_id, idempotency_key),
    ).fetchone()


def sort_timeline(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rule 3: chronological order, oldest first. ``seq`` breaks timestamp ties."""
    return sorted(messages, key=lambda m: (m["created_at"], m["seq"]))


# ----------------------------------------------------------------------- reading
def get_message_row(conn: sqlite3.Connection, message_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if row is None:
        raise MessageNotFoundError(f"Message '{message_id}' does not exist.", details={"message_id": message_id})
    return row


def _serialize(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    marks = ",".join("?" * len(ids))
    reactions: dict[str, dict[str, list[str]]] = {}
    for r in conn.execute(
        f"SELECT message_id, emoji, user_id FROM reactions WHERE message_id IN ({marks}) ORDER BY created_at, user_id",
        ids,
    ):
        reactions.setdefault(r["message_id"], {}).setdefault(r["emoji"], []).append(r["user_id"])
    receipts: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(
        f"SELECT * FROM receipts WHERE message_id IN ({marks}) ORDER BY user_id", ids
    ):
        receipts.setdefault(r["message_id"], []).append(
            {"user_id": r["user_id"], "status": r["status"], "delivered_at": r["delivered_at"], "read_at": r["read_at"]}
        )
    reply_ids = sorted({r["reply_to_id"] for r in rows if r["reply_to_id"]})
    replies: dict[str, dict[str, Any]] = {}
    if reply_ids:
        rmarks = ",".join("?" * len(reply_ids))
        for r in conn.execute(f"SELECT * FROM messages WHERE id IN ({rmarks})", reply_ids):
            replies[r["id"]] = {
                "id": r["id"],
                "sender_id": r["sender_id"],
                "body": "" if r["deleted_at"] else r["body"][:200],
                "deleted": r["deleted_at"] is not None,
            }

    out = []
    for r in rows:
        deleted = r["deleted_at"] is not None
        message_receipts = receipts.get(r["id"], [])
        out.append(
            {
                "id": r["id"],
                "seq": r["seq"],
                "conversation_id": r["conversation_id"],
                "sender_id": r["sender_id"],
                "body": "" if deleted else r["body"],
                "idempotency_key": r["idempotency_key"],
                "reply_to": replies.get(r["reply_to_id"]) if r["reply_to_id"] else None,
                "created_at": r["created_at"],
                "edited_at": r["edited_at"],
                "deleted": deleted,
                "deleted_at": r["deleted_at"],
                "pinned": r["pinned_at"] is not None,
                "pinned_at": r["pinned_at"],
                "pinned_by": r["pinned_by"],
                "reactions": [
                    {"emoji": emoji, "count": len(users), "user_ids": users}
                    for emoji, users in ([] if deleted else reactions.get(r["id"], {}).items())
                ],
                "receipts": message_receipts,
                "status": status.aggregate_status([x["status"] for x in message_receipts]),
            }
        )
    return out


def get_message(conn: sqlite3.Connection, message_id: str) -> dict[str, Any]:
    return _serialize(conn, [get_message_row(conn, message_id)])[0]


def list_history(
    conn: sqlite3.Connection,
    conversation_id: str,
    viewer_id: str,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    before_seq: int | None = None,
) -> list[dict[str, Any]]:
    """Return the newest ``limit`` messages of one conversation, oldest first."""
    ensure_participant(conn, conversation_id, viewer_id)
    sql = "SELECT * FROM messages WHERE conversation_id = ?"
    params: list[Any] = [conversation_id]
    if before_seq is not None:
        sql += " AND seq < ?"
        params.append(before_seq)
    sql += " ORDER BY seq DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    rows = conn.execute(sql, params).fetchall()
    return sort_timeline(_serialize(conn, rows))


def list_pinned(conn: sqlite3.Connection, conversation_id: str, viewer_id: str) -> list[dict[str, Any]]:
    ensure_participant(conn, conversation_id, viewer_id)
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? AND pinned_at IS NOT NULL AND deleted_at IS NULL "
        "ORDER BY pinned_at DESC",
        (conversation_id,),
    ).fetchall()
    return _serialize(conn, rows)


def search_messages(conn: sqlite3.Connection, viewer_id: str, query: str, limit: int = 30) -> list[dict[str, Any]]:
    needle = query.strip()
    if not needle:
        return []
    rows = conn.execute(
        """
        SELECT m.* FROM messages m
        JOIN participants p ON p.conversation_id = m.conversation_id AND p.user_id = ?
        WHERE m.deleted_at IS NULL AND m.body LIKE ? ESCAPE '\\'
        ORDER BY m.created_at DESC, m.seq DESC
        LIMIT ?
        """,
        (viewer_id, "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", limit),
    ).fetchall()
    return _serialize(conn, rows)


# ----------------------------------------------------------------------- writing
def send_message(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    sender_id: str,
    body: str,
    now: datetime,
    idempotency_key: str | None = None,
    reply_to_id: str | None = None,
    message_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Store a message. Returns (message, created). ``created`` is False for an idempotent replay."""
    ensure_participant(conn, conversation_id, sender_id)
    body = validate_body(body)
    if idempotency_key is not None and len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise InvalidMessageError(
            "Idempotency key is too long.", details={"field": "idempotency_key", "max": MAX_IDEMPOTENCY_KEY_LENGTH}
        )

    existing = find_duplicate(conn, conversation_id, sender_id, idempotency_key)
    if existing is not None:
        return _serialize(conn, [existing])[0], False

    if reply_to_id is not None:
        target = get_message_row(conn, reply_to_id)
        if target["conversation_id"] != conversation_id:
            raise InvalidMessageError(
                "A reply must reference a message in the same conversation.",
                details={"reply_to_id": reply_to_id, "conversation_id": conversation_id},
            )

    mid = message_id or new_message_id()
    stamp = iso(now)
    recipients = [uid for uid in participant_ids(conn, conversation_id) if uid != sender_id]
    with conn:
        conn.execute(
            "INSERT INTO messages(id, conversation_id, sender_id, body, idempotency_key, reply_to_id, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (mid, conversation_id, sender_id, body, idempotency_key, reply_to_id, stamp),
        )
        conn.executemany(
            "INSERT INTO receipts(message_id, user_id, status) VALUES (?,?,?)",
            [(mid, uid, status.SENT) for uid in recipients],
        )
    return get_message(conn, mid), True


def _own_live_message(conn: sqlite3.Connection, message_id: str, user_id: str, action: str) -> sqlite3.Row:
    row = get_message_row(conn, message_id)
    ensure_participant(conn, row["conversation_id"], user_id)
    if row["sender_id"] != user_id:
        raise ForbiddenMessageActionError(
            f"You can only {action} your own messages.",
            details={"message_id": message_id, "sender_id": row["sender_id"], "user_id": user_id},
        )
    if row["deleted_at"] is not None:
        raise InvalidMessageError(f"Cannot {action} a deleted message.", details={"message_id": message_id})
    return row


def edit_message(conn: sqlite3.Connection, message_id: str, user_id: str, body: str, now: datetime) -> dict[str, Any]:
    row = _own_live_message(conn, message_id, user_id, "edit")
    body = validate_body(body)
    if body != row["body"]:
        with conn:
            conn.execute("UPDATE messages SET body = ?, edited_at = ? WHERE id = ?", (body, iso(now), message_id))
    return get_message(conn, message_id)


def delete_message(conn: sqlite3.Connection, message_id: str, user_id: str, now: datetime) -> dict[str, Any]:
    """Soft delete: the record (and its position in the timeline) stays, the content is removed."""
    _own_live_message(conn, message_id, user_id, "delete")
    with conn:
        conn.execute(
            "UPDATE messages SET body = '', deleted_at = ?, pinned_at = NULL, pinned_by = NULL WHERE id = ?",
            (iso(now), message_id),
        )
        conn.execute("DELETE FROM reactions WHERE message_id = ?", (message_id,))
    return get_message(conn, message_id)


def toggle_reaction(
    conn: sqlite3.Connection, message_id: str, user_id: str, emoji: str, now: datetime
) -> dict[str, Any]:
    row = get_message_row(conn, message_id)
    ensure_participant(conn, row["conversation_id"], user_id)
    emoji = (emoji or "").strip()
    if not emoji or len(emoji) > MAX_EMOJI_LENGTH:
        raise InvalidMessageError("Reaction must be a single emoji.", details={"emoji": emoji})
    if row["deleted_at"] is not None:
        raise InvalidMessageError("Cannot react to a deleted message.", details={"message_id": message_id})
    exists = conn.execute(
        "SELECT 1 FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?", (message_id, user_id, emoji)
    ).fetchone()
    with conn:
        if exists:
            conn.execute(
                "DELETE FROM reactions WHERE message_id = ? AND user_id = ? AND emoji = ?", (message_id, user_id, emoji)
            )
        else:
            conn.execute(
                "INSERT INTO reactions(message_id, user_id, emoji, created_at) VALUES (?,?,?,?)",
                (message_id, user_id, emoji, iso(now)),
            )
    return get_message(conn, message_id)


def toggle_pin(conn: sqlite3.Connection, message_id: str, user_id: str, now: datetime) -> dict[str, Any]:
    row = get_message_row(conn, message_id)
    ensure_participant(conn, row["conversation_id"], user_id)
    if row["deleted_at"] is not None:
        raise InvalidMessageError("Cannot pin a deleted message.", details={"message_id": message_id})
    with conn:
        if row["pinned_at"]:
            conn.execute("UPDATE messages SET pinned_at = NULL, pinned_by = NULL WHERE id = ?", (message_id,))
        else:
            conn.execute(
                "UPDATE messages SET pinned_at = ?, pinned_by = ? WHERE id = ?", (iso(now), user_id, message_id)
            )
    return get_message(conn, message_id)

"""Conversations: direct messages and group chats."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from typing import Any

from .db import iso
from .errors import ConversationNotFoundError, InvalidConversationError, NotAParticipantError
from .users import get_user, users_by_id

MAX_GROUP_TITLE = 60


def new_conversation_id() -> str:
    return f"c_{uuid.uuid4().hex[:16]}"


def get_conversation_row(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None:
        raise ConversationNotFoundError(
            f"Conversation '{conversation_id}' does not exist.", details={"conversation_id": conversation_id}
        )
    return row


def participant_ids(conn: sqlite3.Connection, conversation_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT user_id FROM participants WHERE conversation_id = ? ORDER BY joined_at, user_id", (conversation_id,)
    ).fetchall()
    return [r["user_id"] for r in rows]


def ensure_participant(conn: sqlite3.Connection, conversation_id: str, user_id: str) -> sqlite3.Row:
    """Rule 5 (isolation): only participants may read or write a conversation."""
    conversation = get_conversation_row(conn, conversation_id)
    row = conn.execute(
        "SELECT 1 FROM participants WHERE conversation_id = ? AND user_id = ?", (conversation_id, user_id)
    ).fetchone()
    if row is None:
        raise NotAParticipantError(
            f"User '{user_id}' is not a participant of conversation '{conversation_id}'.",
            details={"conversation_id": conversation_id, "user_id": user_id},
        )
    return conversation


def find_direct_conversation(conn: sqlite3.Connection, user_a: str, user_b: str) -> str | None:
    row = conn.execute(
        """
        SELECT c.id FROM conversations c
        JOIN participants pa ON pa.conversation_id = c.id AND pa.user_id = ?
        JOIN participants pb ON pb.conversation_id = c.id AND pb.user_id = ?
        WHERE c.kind = 'direct'
        LIMIT 1
        """,
        (user_a, user_b),
    ).fetchone()
    return row["id"] if row else None


def create_conversation(
    conn: sqlite3.Connection,
    *,
    creator_id: str,
    kind: str,
    participant_ids_in: list[str],
    title: str | None,
    now: datetime,
    topic: str = "",
    emoji: str = "",
    conversation_id: str | None = None,
) -> tuple[str, bool]:
    """Create a conversation. Returns (conversation_id, created).

    Direct conversations are unique per pair of users: asking for one that
    already exists returns it instead of creating a duplicate.
    """
    get_user(conn, creator_id)
    others = [uid for uid in dict.fromkeys(participant_ids_in) if uid != creator_id]
    for uid in others:
        get_user(conn, uid)

    if kind == "direct":
        if len(others) != 1:
            raise InvalidConversationError(
                "A direct conversation needs exactly one other participant.",
                details={"participant_ids": participant_ids_in},
            )
        existing = find_direct_conversation(conn, creator_id, others[0])
        if existing:
            return existing, False
        title = None
    elif kind == "group":
        if len(others) < 2:
            raise InvalidConversationError(
                "A group conversation needs at least two other participants.",
                details={"participant_ids": participant_ids_in},
            )
        title = (title or "").strip()
        if not title or len(title) > MAX_GROUP_TITLE:
            raise InvalidConversationError(
                f"Group name must be 1-{MAX_GROUP_TITLE} characters.", details={"title": title}
            )
    else:
        raise InvalidConversationError(f"Unknown conversation kind '{kind}'.", details={"kind": kind})

    cid = conversation_id or new_conversation_id()
    stamp = iso(now)
    with conn:
        conn.execute(
            "INSERT INTO conversations(id, kind, title, topic, emoji, created_by, created_at) VALUES (?,?,?,?,?,?,?)",
            (cid, kind, title, topic, emoji, creator_id, stamp),
        )
        conn.executemany(
            "INSERT INTO participants(conversation_id, user_id, joined_at) VALUES (?,?,?)",
            [(cid, uid, stamp) for uid in [creator_id, *others]],
        )
    return cid, True


def set_muted(conn: sqlite3.Connection, conversation_id: str, user_id: str, muted: bool) -> None:
    ensure_participant(conn, conversation_id, user_id)
    with conn:
        conn.execute(
            "UPDATE participants SET muted = ? WHERE conversation_id = ? AND user_id = ?",
            (int(muted), conversation_id, user_id),
        )


def unread_count(conn: sqlite3.Connection, conversation_id: str, user_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM receipts r
        JOIN messages m ON m.id = r.message_id
        WHERE m.conversation_id = ? AND r.user_id = ? AND r.status != 'read' AND m.deleted_at IS NULL
        """,
        (conversation_id, user_id),
    ).fetchone()
    return int(row["n"])


def _last_message(conn: sqlite3.Connection, conversation_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at DESC, seq DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "sender_id": row["sender_id"],
        "body": "" if row["deleted_at"] else row["body"][:160],
        "deleted": row["deleted_at"] is not None,
        "created_at": row["created_at"],
    }


def conversation_summary(conn: sqlite3.Connection, conversation_id: str, viewer_id: str) -> dict[str, Any]:
    row = ensure_participant(conn, conversation_id, viewer_id)
    members = participant_ids(conn, conversation_id)
    people = users_by_id(conn, members)
    muted_row = conn.execute(
        "SELECT muted FROM participants WHERE conversation_id = ? AND user_id = ?", (conversation_id, viewer_id)
    ).fetchone()
    last = _last_message(conn, conversation_id)
    if row["kind"] == "direct":
        other = next((people[u] for u in members if u != viewer_id), None)
        title = other["name"] if other else "Direct message"
    else:
        title = row["title"]
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": title,
        "topic": row["topic"],
        "emoji": row["emoji"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "participant_ids": members,
        "participants": [people[u] for u in members if u in people],
        "muted": bool(muted_row["muted"]) if muted_row else False,
        "unread_count": unread_count(conn, conversation_id, viewer_id),
        "last_message": last,
        "updated_at": last["created_at"] if last else row["created_at"],
    }


def list_conversations(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    get_user(conn, user_id)
    rows = conn.execute("SELECT conversation_id FROM participants WHERE user_id = ?", (user_id,)).fetchall()
    summaries = [conversation_summary(conn, r["conversation_id"], user_id) for r in rows]
    summaries.sort(key=lambda c: (c["updated_at"], c["id"]), reverse=True)
    return summaries

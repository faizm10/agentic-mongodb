"""Demo users. NovaConnect has no real authentication: the client says who it is
via the ``X-Demo-User`` header (or ``user_id`` on the WebSocket). Demo only."""

from __future__ import annotations

import sqlite3
from typing import Any

from .errors import MissingUserError, UserNotFoundError


def user_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "handle": row["handle"],
        "title": row["title"],
        "bio": row["bio"],
        "location": row["location"],
        "avatar_hue": row["avatar_hue"],
    }


def list_users(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
    return [user_to_dict(r) for r in rows]


def get_user(conn: sqlite3.Connection, user_id: str | None) -> dict[str, Any]:
    if not user_id:
        raise MissingUserError(
            "No demo user selected. Send the X-Demo-User header.",
            details={"header": "X-Demo-User"},
        )
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise UserNotFoundError(f"User '{user_id}' does not exist.", details={"user_id": user_id})
    return user_to_dict(row)


def users_by_id(conn: sqlite3.Connection, user_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not user_ids:
        return {}
    marks = ",".join("?" * len(user_ids))
    rows = conn.execute(f"SELECT * FROM users WHERE id IN ({marks})", user_ids).fetchall()
    return {r["id"]: user_to_dict(r) for r in rows}

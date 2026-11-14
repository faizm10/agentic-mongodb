"""SQLite persistence: connection setup and schema.

The schema is intentionally small and readable. Business rules (idempotency,
status transitions, ordering, isolation) live in Python modules, not in
database triggers, so that they are easy to inspect, test and repair.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "novaconnect.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    handle       TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL DEFAULT '',
    bio          TEXT NOT NULL DEFAULT '',
    location     TEXT NOT NULL DEFAULT '',
    avatar_hue   INTEGER NOT NULL DEFAULT 220
);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('direct', 'group')),
    title       TEXT,
    topic       TEXT NOT NULL DEFAULT '',
    emoji       TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS participants (
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    user_id         TEXT NOT NULL REFERENCES users(id),
    joined_at       TEXT NOT NULL,
    muted           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (conversation_id, user_id)
);

-- seq is a monotonically increasing insertion counter used as an ordering tie-breaker.
-- Idempotency is enforced by app.messages.find_duplicate (see Rule 1), not by a constraint.
CREATE TABLE IF NOT EXISTS messages (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    id               TEXT NOT NULL UNIQUE,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id),
    sender_id        TEXT NOT NULL REFERENCES users(id),
    body             TEXT NOT NULL,
    idempotency_key  TEXT,
    reply_to_id      TEXT,
    created_at       TEXT NOT NULL,
    edited_at        TEXT,
    deleted_at       TEXT,
    pinned_at        TEXT,
    pinned_by        TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, seq);
CREATE INDEX IF NOT EXISTS idx_messages_idempotency ON messages(conversation_id, sender_id, idempotency_key);

-- One receipt per (message, recipient). The sender has no receipt for their own message.
CREATE TABLE IF NOT EXISTS receipts (
    message_id    TEXT NOT NULL REFERENCES messages(id),
    user_id       TEXT NOT NULL REFERENCES users(id),
    status        TEXT NOT NULL CHECK (status IN ('sent', 'delivered', 'read')),
    delivered_at  TEXT,
    read_at       TEXT,
    PRIMARY KEY (message_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_receipts_user ON receipts(user_id, status);

CREATE TABLE IF NOT EXISTS reactions (
    message_id  TEXT NOT NULL REFERENCES messages(id),
    user_id     TEXT NOT NULL REFERENCES users(id),
    emoji       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (message_id, user_id, emoji)
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Fixed-width UTC timestamp (microsecond precision) that sorts lexicographically."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def resolve_db_path(path: str | Path | None = None) -> str:
    if path is not None:
        return str(path)
    return os.environ.get("NOVACONNECT_DB_PATH") or str(DEFAULT_DB_PATH)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection and make sure the schema exists. Use ':memory:' for an isolated DB."""
    target = resolve_db_path(path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if target != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
    )
    conn.commit()
    return conn


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Read-only connection used by the monitoring probe; it can never write."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

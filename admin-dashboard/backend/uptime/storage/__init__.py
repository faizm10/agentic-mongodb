"""Storage selection.

OPSRELAY_STORAGE = auto (default) | mongodb | sqlite
  auto    -> MongoDB when MONGODB_URI is set, otherwise local development storage (SQLite)
  mongodb -> MongoDB; fails loudly if MONGODB_URI is missing (never silently falls back)
  sqlite  -> local development storage at OPSRELAY_SQLITE_PATH (default backend/data/uptime-dev.db)
"""

from __future__ import annotations

import os
from pathlib import Path

from .base import Storage
from ..sealing import require_key
from .mongo import MongoStorage, connect
from .sqlite import SqliteStorage

DEFAULT_SQLITE_PATH = Path(__file__).resolve().parents[2] / "data" / "uptime-dev.db"


def open_storage(*, retention_days: int = 35, incident_retention_days: int = 180) -> Storage:
    require_key()
    mode = (os.environ.get("OPSRELAY_STORAGE") or "auto").lower()
    uri = os.environ.get("MONGODB_URI", "")
    if mode == "mongodb" or (mode == "auto" and uri):
        db, uri = connect(uri)
        storage: Storage = MongoStorage(db, uri=uri, retention_days=retention_days,
                                        incident_retention_days=incident_retention_days)
    elif mode in ("sqlite", "auto"):
        storage = SqliteStorage(os.environ.get("OPSRELAY_SQLITE_PATH") or DEFAULT_SQLITE_PATH)
    else:
        raise ValueError(f"unknown OPSRELAY_STORAGE={mode!r} (auto | mongodb | sqlite)")
    storage.init()
    return storage


__all__ = ["MongoStorage", "SqliteStorage", "Storage", "open_storage"]

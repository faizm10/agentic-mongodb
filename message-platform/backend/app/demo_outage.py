"""Read the local OpsRelay demo mode without caching across requests."""

from __future__ import annotations

import json
from pathlib import Path

FILENAME = "opsrelay-outage.json"


def mode(db_path: str) -> str | None:
    if db_path == ":memory:":
        return None
    try:
        value = json.loads((Path(db_path).parent / FILENAME).read_text(encoding="utf-8")).get("scenario")
    except (FileNotFoundError, OSError, ValueError, AttributeError):
        return None
    return value if value in {"send", "receive", "both"} else None

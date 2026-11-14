"""Read the local OpsRelay demo mode without caching across requests."""

from __future__ import annotations

import json
from pathlib import Path

FILENAME = "opsrelay-outage.json"


def mode(data_dir: Path) -> str | None:
    try:
        value = json.loads((data_dir / FILENAME).read_text(encoding="utf-8")).get("scenario")
    except (FileNotFoundError, OSError, ValueError, AttributeError):
        return None
    return value if value in {"payment", "images", "inventory"} else None


def image_url(url: str, data_dir: Path) -> str:
    return "/api/demo/unavailable-image" if mode(data_dir) == "images" else url

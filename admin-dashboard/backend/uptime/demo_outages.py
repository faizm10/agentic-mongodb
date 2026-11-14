"""Local, reversible demo outages shared with the two app backends.

The uptime API and demo apps must run from the same repository checkout. Only the
admin API writes these files; the apps read them on each request so no restart is
needed. This deliberately does not change application source or test fixtures.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SCENARIOS = {
    "novamart": frozenset({"payment", "images", "inventory"}),
    "novaconnect": frozenset({"send", "receive", "both"}),
}
FILENAME = "opsrelay-outage.json"


def repo_root() -> Path:
    return Path(os.environ.get("OPSRELAY_DEMO_REPO_ROOT") or Path(__file__).resolve().parents[3]).resolve()


def state_path(service_id: str) -> Path:
    if service_id not in SCENARIOS:
        raise ValueError("Unknown demo service")
    app_dir = "shopping" if service_id == "novamart" else "message-platform"
    return repo_root() / app_dir / "backend" / "data" / FILENAME


def activate(service_id: str, scenario: str) -> None:
    if scenario not in SCENARIOS.get(service_id, ()):
        raise ValueError("Unsupported demo outage")
    path = state_path(service_id)
    if not (path.parent.parent / "app").is_dir():
        raise FileNotFoundError("The demo app backend is not present in this checkout")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".opsrelay-outage-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"scenario": scenario}, handle)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def clear(service_id: str) -> None:
    state_path(service_id).unlink(missing_ok=True)

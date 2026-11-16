"""Read the local OpsRelay demo mode without caching across requests."""

from __future__ import annotations

import json
from pathlib import Path

FILENAME = "opsrelay-outage.json"


# Controlled timeout for order confirmations, used by the cross-service checkout incident:
#   drop           wait, then fail without recording (the request never took effect)
#   lost_response  record and deliver, then wait (the sender times out; the record exists)
#   mixed          alternate drop and lost_response, starting with drop
#   pending        record but hold delivery (the acknowledgement says accepted, not delivered)
#   lookup_timeout reads of a confirmation wait (the record cannot be checked)
CONFIRMATION_MODES = frozenset({"drop", "lost_response", "mixed", "pending", "lookup_timeout"})
DEFAULT_DELAY_SECONDS = 3.0


def _read(db_path: str) -> dict:
    if db_path == ":memory:":
        return {}
    try:
        value = json.loads((Path(db_path).parent / FILENAME).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def mode(db_path: str) -> str | None:
    value = _read(db_path).get("scenario")
    return value if value in {"send", "receive", "both"} else None


def confirmation_fault(db_path: str) -> tuple[str, float] | None:
    """(mode, delay_seconds) while the confirmation timeout is active, else None."""
    state = _read(db_path)
    if state.get("scenario") != "confirmation_timeout":
        return None
    fault = state.get("mode") if state.get("mode") in CONFIRMATION_MODES else "mixed"
    try:
        delay = max(0.0, min(30.0, float(state.get("delay_seconds", DEFAULT_DELAY_SECONDS))))
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY_SECONDS
    return fault, delay

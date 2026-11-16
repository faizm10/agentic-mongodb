"""Local JSON persistence for demo orders, sold inventory and checkout attempts.

Writes are atomic (temp file + ``os.replace``) and serialized by a process-wide
lock, which is plenty for a single-process demo server.

A checkout attempt is recorded under its client-supplied checkout ID *before* the
order is written, so an attempt that failed before any order existed can still be
found. The store owns the checkout ID's uniqueness: one attempt and at most one
order per checkout ID.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .errors import CheckoutConflictError, OrderNotFoundError, OrderStoreError

STORE_FILENAME = "novamart-store.json"
_EMPTY: dict[str, Any] = {"schema_version": 2, "orders": [], "sold": {}, "checkouts": {}}
MAX_ATTEMPT_EVENTS = 40

# Attempt states. Only "completed", "failed" and "rejected" are final; every other
# state is an interrupted checkout that recovery must reconcile.
STARTED = "started"            # recorded; no order write has finished
ORDER_SAVED = "order_saved"    # order committed; confirmation not verified delivered
ORDER_FAILED = "order_failed"  # the order write raised; whether an order exists must be checked
REJECTED = "rejected"          # a business rule refused the cart; the customer was told why
COMPLETED = "completed"        # order saved and its confirmation verified delivered
FAILED = "failed"              # verified that no order exists; the customer can retry
FINAL_STATES = frozenset({COMPLETED, FAILED, REJECTED})


class DemoStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / STORE_FILENAME
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- reading
    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return copy.deepcopy(_EMPTY)
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise OrderStoreError(
                "The local order store could not be read.",
                details={"path": str(self.path), "error": f"{type(exc).__name__}: {exc}"},
                hint="Delete the file to reset demo data, or restore it from a backup.",
            ) from exc
        data.setdefault("orders", [])
        data.setdefault("sold", {})
        data.setdefault("checkouts", {})
        return data

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._read()

    def sold_counts(self) -> dict[str, int]:
        return dict(self.snapshot()["sold"])

    def list_orders(self, limit: int = 20) -> list[dict[str, Any]]:
        orders = self.snapshot()["orders"]
        return list(reversed(orders))[:limit]

    def order_count(self) -> int:
        return len(self.snapshot()["orders"])

    def order_for_checkout(self, checkout_id: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        orders = (data or self.snapshot())["orders"]
        return next((o for o in orders if o.get("checkout_id") == checkout_id), None)

    def get_checkout(self, checkout_id: str) -> dict[str, Any] | None:
        return self.snapshot()["checkouts"].get(checkout_id)

    def list_checkouts(self, *, since: datetime | None = None, until: datetime | None = None,
                       unfinished: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        """Attempts by start time, newest first. ``unfinished`` keeps only those recovery must reconcile."""
        out = []
        for attempt in self.snapshot()["checkouts"].values():
            started = datetime.fromisoformat(attempt["started_at"])
            if (since and started < since) or (until and started > until):
                continue
            if unfinished and attempt["state"] in FINAL_STATES:
                continue
            out.append(attempt)
        return sorted(out, key=lambda a: a["started_at"], reverse=True)[:limit]

    def get_order(self, order_id: str) -> dict[str, Any]:
        for order in self.snapshot()["orders"]:
            if order["order_id"] == order_id:
                return order
        raise OrderNotFoundError(f"Order '{order_id}' was not found.", details={"order_id": order_id})

    # ---------------------------------------------------------------- writing
    def _write(self, data: dict[str, Any]) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".novamart-", suffix=".json", dir=self.data_dir)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError as exc:
            raise OrderStoreError(
                "The local order store could not be written.",
                details={"path": str(self.path), "error": f"{type(exc).__name__}: {exc}"},
            ) from exc

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        """Read-modify-write under a lock. Nothing is written if the block raises."""
        with self._lock:
            data = self._read()
            yield data
            self._write(data)

    # -------------------------------------------------------------- attempts
    def begin_checkout(self, checkout_id: str, fingerprint: str, request_id: str | None,
                       now: datetime) -> tuple[dict[str, Any], bool]:
        """Record the attempt before anything else happens. Returns (attempt, is_retry)."""
        with self.transaction() as data:
            attempt = data["checkouts"].get(checkout_id)
            if attempt is not None:
                if attempt["fingerprint"] != fingerprint:
                    raise CheckoutConflictError(
                        "This checkout ID was already used for a different order.",
                        details={"checkout_id": checkout_id},
                        hint="Start a new checkout to change the cart or the delivery details.",
                    )
                _event(attempt, now, "retry_received", request_id=request_id)
                if request_id and request_id not in attempt["request_ids"]:
                    attempt["request_ids"].append(request_id)
                return attempt, True
            attempt = {
                "checkout_id": checkout_id,
                "fingerprint": fingerprint,
                "state": STARTED,
                "started_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "request_ids": [request_id] if request_id else [],
                "order_id": None,
                "confirmation_id": None,
                "confirmation": {"status": "not_sent"},
                "response": None,
                "reconciliation": None,
                "events": [],
            }
            _event(attempt, now, "checkout_started", request_id=request_id)
            data["checkouts"][checkout_id] = attempt
            return attempt, False

    def update_checkout(self, checkout_id: str, now: datetime, event: str, *,
                        data: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any] | None:
        """Set fields on an attempt and append an event. Pass ``data`` inside an open transaction."""
        if data is not None:
            return _apply(data, checkout_id, now, event, fields)
        with self.transaction() as current:
            return _apply(current, checkout_id, now, event, fields)

    def add_order(self, data: dict[str, Any], order: dict[str, Any]) -> None:
        """Append an order inside an open transaction. One order per checkout ID."""
        checkout_id = order.get("checkout_id")
        if checkout_id and self.order_for_checkout(checkout_id, data) is not None:
            raise CheckoutConflictError("An order already exists for this checkout ID.",
                                        details={"checkout_id": checkout_id})
        data["orders"].append(order)

    def count_unfinished(self, since: datetime) -> int:
        return len(self.list_checkouts(since=since, unfinished=True, limit=10_000))

    # ----------------------------------------------------------------- health
    def health(self) -> dict[str, Any]:
        """Read-only diagnostics: never writes to disk."""
        target = self.data_dir
        while not target.exists() and target != target.parent:
            target = target.parent  # the directory will be created on first write
        writable = os.access(target, os.W_OK)
        readable = True
        error = None
        orders = 0
        try:
            orders = len(self._read()["orders"])
        except OrderStoreError as exc:
            readable = False
            error = exc.details.get("error")
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "readable": readable,
            "writable": writable,
            "order_count": orders,
            "error": error,
        }


def _event(attempt: dict[str, Any], now: datetime, event: str, **fields: Any) -> None:
    attempt["events"].append({"at": now.isoformat(), "event": event,
                              **{k: v for k, v in fields.items() if v is not None}})
    del attempt["events"][:-MAX_ATTEMPT_EVENTS]
    attempt["updated_at"] = now.isoformat()


def _apply(data: dict[str, Any], checkout_id: str, now: datetime, event: str,
           fields: dict[str, Any]) -> dict[str, Any] | None:
    attempt = data["checkouts"].get(checkout_id)
    if attempt is None:
        return None
    request_id = fields.pop("request_id", None)
    detail = fields.pop("detail", None) or {}
    attempt.update(fields)
    _event(attempt, now, event, request_id=request_id, **detail)
    return attempt

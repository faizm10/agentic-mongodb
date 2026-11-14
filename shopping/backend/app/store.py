"""Local JSON persistence for demo orders and sold inventory.

Writes are atomic (temp file + ``os.replace``) and serialized by a process-wide
lock, which is plenty for a single-process demo server.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import OrderNotFoundError, OrderStoreError

STORE_FILENAME = "novamart-store.json"
_EMPTY: dict[str, Any] = {"schema_version": 1, "orders": [], "sold": {}}


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

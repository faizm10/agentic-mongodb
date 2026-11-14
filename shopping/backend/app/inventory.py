"""Inventory validation: make sure we never sell more units than we have."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from .errors import InsufficientStockError


@dataclass(frozen=True)
class Shortage:
    product_id: str
    requested: int
    available: int

    @property
    def message(self) -> str:
        if self.available <= 0:
            return "This item is sold out."
        return f"Only {self.available} left in stock (you requested {self.requested})."

    def to_dict(self) -> dict:
        return {
            "code": "INSUFFICIENT_STOCK",
            "product_id": self.product_id,
            "requested": self.requested,
            "available": self.available,
            "message": self.message,
        }


def aggregate_quantities(items: Iterable[tuple[str, int]]) -> dict[str, int]:
    """Combine duplicate cart lines for the same product into one requested quantity."""
    totals: dict[str, int] = defaultdict(int)
    for product_id, quantity in items:
        totals[product_id] += quantity
    return dict(totals)


def find_shortages(requested: Mapping[str, int], available: Mapping[str, int]) -> list[Shortage]:
    shortages: list[Shortage] = []
    for product_id, requested_qty in requested.items():
        available_qty = max(0, available.get(product_id, 0))
        if requested_qty > available_qty:
            shortages.append(Shortage(product_id, requested_qty, available_qty))
    return shortages


def ensure_available(requested: Mapping[str, int], available: Mapping[str, int]) -> None:
    shortages = find_shortages(requested, available)
    if shortages:
        first = shortages[0]
        raise InsufficientStockError(
            f"Not enough stock for {first.product_id}: requested {first.requested}, available {first.available}.",
            details={"shortages": [s.to_dict() for s in shortages]},
            hint="Reduce the quantity or remove the item from your cart.",
        )

"""Message status state machine (Rule 2: correct delivery status).

Each recipient has a receipt that moves strictly forward:

    sent  ->  delivered  ->  read

A receipt can never skip a state or move backwards.
"""

from __future__ import annotations

from .errors import InvalidStatusTransitionError

SENT = "sent"
DELIVERED = "delivered"
READ = "read"
STATUSES = (SENT, DELIVERED, READ)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    SENT: frozenset({DELIVERED}),
    DELIVERED: frozenset({READ}),
    READ: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def ensure_transition(current: str, target: str, *, message_id: str, user_id: str) -> None:
    if not can_transition(current, target):
        raise InvalidStatusTransitionError(
            f"Receipt for message {message_id} cannot move from '{current}' to '{target}'.",
            details={
                "message_id": message_id,
                "user_id": user_id,
                "current": current,
                "target": target,
                "allowed": sorted(ALLOWED_TRANSITIONS.get(current, ())),
            },
        )


def aggregate_status(receipt_statuses: list[str]) -> str:
    """Status shown to the sender: the *least advanced* state across all recipients."""
    if not receipt_statuses:
        return SENT
    return min(receipt_statuses, key=STATUSES.index)

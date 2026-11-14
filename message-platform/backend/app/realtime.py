"""WebSocket connection registry and fan-out."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("novaconnect.realtime")


class ConnectionManager:
    """Tracks open sockets per user. A user may have several tabs open."""

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    def online_user_ids(self) -> set[str]:
        return {uid for uid, sockets in self._connections.items() if sockets}

    def connection_count(self) -> int:
        return sum(len(s) for s in self._connections.values())

    async def connect(self, user_id: str, websocket: WebSocket) -> bool:
        """Register a socket. Returns True if this is the user's first open connection."""
        async with self._lock:
            first = not self._connections[user_id]
            self._connections[user_id].add(websocket)
            return first

    async def disconnect(self, user_id: str, websocket: WebSocket) -> bool:
        """Unregister a socket. Returns True if the user has no connections left."""
        async with self._lock:
            self._connections[user_id].discard(websocket)
            return not self._connections[user_id]

    async def send_to_users(self, user_ids: Iterable[str], event: dict[str, Any]) -> set[str]:
        """Push an event to every socket of the given users. Returns the users reached."""
        reached: set[str] = set()
        for user_id in set(user_ids):
            for websocket in list(self._connections.get(user_id, ())):
                try:
                    await websocket.send_json(event)
                    reached.add(user_id)
                except Exception:  # noqa: BLE001 - a dead socket must not break fan-out
                    logger.info("dropping dead socket for %s", user_id)
                    await self.disconnect(user_id, websocket)
        return reached

    async def broadcast(self, event: dict[str, Any], *, exclude: str | None = None) -> None:
        await self.send_to_users([u for u in self._connections if u != exclude], event)

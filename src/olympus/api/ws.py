"""WebSocket connection manager."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

from olympus.events.bus import EventBus
from olympus.events.types import Event

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]

    async def broadcast(self, data: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def handle_event(self, event: Event) -> None:
        """EventBus handler — broadcasts events to all WebSocket clients."""
        await self.broadcast({
            "type": event.type,
            "room_id": event.room_id,
            "data": event.data,
        })

    def setup(self) -> None:
        """Subscribe to the global EventBus."""
        EventBus.get().subscribe(self.handle_event)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

"""Async event bus for broadcasting events to subscribers."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from olympus.events.types import Event

logger = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Simple async pub/sub event bus. Singleton pattern."""

    _instance: EventBus | None = None

    def __init__(self) -> None:
        self._handlers: list[Handler] = []

    @classmethod
    def get(cls) -> EventBus:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def unsubscribe(self, handler: Handler) -> None:
        self._handlers = [h for h in self._handlers if h is not handler]

    async def publish(self, event: Event) -> None:
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("Event handler error for %s", event.type)

    def publish_nowait(self, event: Event) -> None:
        """Fire-and-forget publish for use in sync callbacks."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event))
        except RuntimeError:
            pass

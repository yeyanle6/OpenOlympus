"""Async event bus for broadcasting events to subscribers.

EventBus can be used as:
- Singleton via ``EventBus.get()`` (backward compatible, production use)
- Independent instance via ``EventBus()`` (testing, isolation)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from olympus.events.types import Event

logger = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Async pub/sub event bus. Instantiable with optional singleton access."""

    _instance: EventBus | None = None

    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._event_count: int = 0

    @classmethod
    def get(cls) -> EventBus:
        """Get or create the global singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def set(cls, instance: EventBus) -> None:
        """Replace the global singleton (useful for testing)."""
        cls._instance = instance

    @classmethod
    def reset(cls) -> None:
        """Clear the global singleton."""
        cls._instance = None

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def unsubscribe(self, handler: Handler) -> None:
        self._handlers = [h for h in self._handlers if h is not handler]

    @property
    def handler_count(self) -> int:
        return len(self._handlers)

    @property
    def event_count(self) -> int:
        return self._event_count

    async def publish(self, event: Event) -> None:
        self._event_count += 1
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

    def clear(self) -> None:
        """Remove all handlers (useful for testing)."""
        self._handlers.clear()
        self._event_count = 0

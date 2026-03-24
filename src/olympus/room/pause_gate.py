"""Cooperative pause/cancel gate for room execution."""

from __future__ import annotations

import asyncio


class RoomCancelled(Exception):
    """Raised when a room is cancelled via the gate."""


class PauseGate:
    """Cooperative concurrency primitive for pause/resume/cancel.

    Protocols call ``await gate.checkpoint()`` between agent invocations.
    The gate blocks if paused, and raises ``RoomCancelled`` if cancelled.
    """

    def __init__(self) -> None:
        self._resume_event = asyncio.Event()
        self._resume_event.set()  # Start in resumed state
        self._cancelled = False

    async def checkpoint(self) -> None:
        """Called by protocols between steps. Blocks if paused, raises if cancelled."""
        if self._cancelled:
            raise RoomCancelled()
        await self._resume_event.wait()
        if self._cancelled:
            raise RoomCancelled()

    def pause(self) -> None:
        self._resume_event.clear()

    def resume(self) -> None:
        self._resume_event.set()

    def cancel(self) -> None:
        # Set cancelled BEFORE unblocking, preventing race condition
        self._cancelled = True
        self._resume_event.set()

    @property
    def is_paused(self) -> bool:
        return not self._resume_event.is_set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

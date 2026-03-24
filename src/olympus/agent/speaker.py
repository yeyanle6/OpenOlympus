"""Global speaker lock — ensures only one Agent calls the LLM API at a time.

Like a conference room with one microphone: only the current speaker can talk,
everyone else waits their turn. This prevents concurrent API calls and controls cost.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SpeakerRequest:
    """A queued request to speak (call the API)."""
    agent_id: str
    room_id: str
    priority: int = 0  # Higher = more urgent


class SpeakerLock:
    """Global singleton that ensures only one API call happens at a time."""

    _instance: SpeakerLock | None = None

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._current_speaker: str = ""
        self._current_room: str = ""
        self._queue_size: int = 0

    @classmethod
    def get(cls) -> SpeakerLock:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @property
    def current_speaker(self) -> str:
        return self._current_speaker

    @property
    def current_room(self) -> str:
        return self._current_room

    @property
    def queue_size(self) -> int:
        return self._queue_size

    @property
    def is_busy(self) -> bool:
        return self._lock.locked()

    async def speak(self, agent_id: str, room_id: str = "") -> _SpeakerContext:
        """Acquire the speaker lock. Use as async context manager.

        Usage:
            async with speaker_lock.speak("planner", "room-123"):
                result = call_claude(...)
        """
        return _SpeakerContext(self, agent_id, room_id)

    def status(self) -> dict[str, Any]:
        return {
            "busy": self.is_busy,
            "current_speaker": self._current_speaker,
            "current_room": self._current_room,
            "queue_size": self._queue_size,
        }


class _SpeakerContext:
    def __init__(self, lock: SpeakerLock, agent_id: str, room_id: str):
        self._lock = lock
        self._agent_id = agent_id
        self._room_id = room_id

    async def __aenter__(self):
        self._lock._queue_size += 1
        logger.debug(
            "Agent %s (room %s) waiting to speak (queue: %d)",
            self._agent_id, self._room_id, self._lock._queue_size,
        )
        await self._lock._lock.acquire()
        self._lock._queue_size -= 1
        self._lock._current_speaker = self._agent_id
        self._lock._current_room = self._room_id
        logger.info(
            "Agent %s (room %s) is now speaking",
            self._agent_id, self._room_id,
        )
        return self

    async def __aexit__(self, *exc):
        self._lock._current_speaker = ""
        self._lock._current_room = ""
        self._lock._lock.release()
        logger.debug("Agent %s finished speaking", self._agent_id)

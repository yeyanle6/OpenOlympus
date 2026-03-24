"""In-memory per-room message storage."""

from __future__ import annotations

from olympus.types import Message


class SessionMemory:
    """Stores messages for a single room session."""

    def __init__(self) -> None:
        self._messages: list[Message] = []

    def add(self, message: Message) -> None:
        self._messages.append(message)

    def get_all(self) -> list[Message]:
        return list(self._messages)

    def get_last(self, n: int = 10) -> list[Message]:
        return list(self._messages[-n:])

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

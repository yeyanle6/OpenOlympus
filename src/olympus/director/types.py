"""Director-specific types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from olympus.types import RoomStatus


class Priority:
    """Emergency triage-inspired priority levels."""
    RED = 3      # Critical — blocks everything, run immediately
    YELLOW = 2   # Important — run next
    GREEN = 1    # Normal — queued
    BLUE = 0     # Low — background task


@dataclass
class DirectorAction:
    action: str  # spawn_room | pause_room | resume_room | stop_room | status | reply
    protocol: str = ""
    agents: list[str] = field(default_factory=list)
    task: str = ""
    reply: str = ""
    room_id: str = ""
    priority: int = Priority.GREEN  # Triage priority
    then: DirectorAction | None = None


@dataclass
class ManagedRoom:
    room_id: str
    task: str
    protocol: str
    agent_ids: list[str]
    status: RoomStatus = RoomStatus.CREATED
    priority: int = Priority.GREEN
    result: Any = None

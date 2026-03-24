"""Core data types shared across the Olympus framework."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable


class MessageType(str, Enum):
    SYSTEM = "system"
    OPINION = "opinion"
    ARTIFACT = "artifact"
    REVIEW = "review"
    STATUS = "status"
    VOTE = "vote"
    DECISION = "decision"


class RoomStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class AgentLayer(str, Enum):
    ORCHESTRATION = "orchestration"
    PLANNING = "planning"
    WORKER = "worker"
    SPECIALIST = "specialist"


@dataclass
class Message:
    type: MessageType
    sender: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class AgentResult:
    status: str  # "success" | "failed" | "timeout"
    artifact: str = ""
    error: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0
    agent_id: str = ""
    duration_ms: int = 0


@dataclass
class AgentPermissions:
    tools: list[str] = field(default_factory=list)
    write: bool = True
    execute: bool = True
    spawn_rooms: bool = False


LAYER_PERMISSIONS: dict[AgentLayer, AgentPermissions] = {
    AgentLayer.ORCHESTRATION: AgentPermissions(
        tools=["read", "grep", "glob", "git_log", "git_status"],
        write=True,
        execute=False,
        spawn_rooms=True,
    ),
    AgentLayer.PLANNING: AgentPermissions(
        tools=["read", "grep", "glob", "git_log"],
        write=False,
        execute=False,
    ),
    AgentLayer.WORKER: AgentPermissions(
        tools=["read", "write", "edit", "grep", "glob", "bash", "git"],
        write=True,
        execute=True,
    ),
    AgentLayer.SPECIALIST: AgentPermissions(
        tools=["read", "grep", "glob", "git_log", "git_blame"],
        write=False,
        execute=False,
    ),
}


@dataclass
class RoomConfig:
    timeout_seconds: float = 86400.0  # 24 hours for deep discussions
    max_budget_usd: float = 2.0
    max_retries: int = 1
    max_rounds: int = 3
    priority: int = 0
    parent_room_id: str = ""
    tags: list[str] = field(default_factory=list)
    # Sprint/OKR context — set by the loop engine or director
    sprint: int = 0  # current sprint number (0 = not in a sprint)
    sprint_goal: str = ""
    okr_ids: list[str] = field(default_factory=list)  # e.g. ["O1", "O1/KR2"]


# Callback types
OnMessage = Callable[[Message], None] | None
OnStatus = Callable[[str, RoomStatus], None] | None

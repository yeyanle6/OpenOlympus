"""Event type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str  # "room_message" | "room_status" | "cycle_complete" | "consensus_updated" | "okr_updated" | "decision_recorded" | "landmarks" | "gesture" | "camera_status"
    room_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)

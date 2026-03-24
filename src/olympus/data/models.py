"""Data models for the collection pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Operator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NEQ = "neq"


@dataclass
class CycleMetric:
    """One row per completed engine cycle."""

    cycle: int
    phase: str
    duration_ms: int
    cost_usd: float
    tokens_used: int
    blockers_detected: int
    blocker_types: str = ""  # comma-separated
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class GitCommit:
    """One row per git commit."""

    sha: str
    author: str
    message: str
    timestamp: str
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass
class OkrSnapshot:
    """Point-in-time capture of OKR progress."""

    objective_id: str
    objective_desc: str
    progress: float
    key_results_json: str = "[]"  # JSON array of {id, desc, progress}
    cycle: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class Alert:
    """An alert fired by the rule engine."""

    rule_name: str
    severity: AlertSeverity
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class Rule:
    """A threshold-based alert rule."""

    name: str
    metric: str  # e.g. "cost_usd", "duration_ms", "blockers_detected"
    operator: Operator
    threshold: float
    severity: AlertSeverity = AlertSeverity.WARNING
    cooldown_cycles: int = 10  # suppress re-fire for N cycles
    enabled: bool = True

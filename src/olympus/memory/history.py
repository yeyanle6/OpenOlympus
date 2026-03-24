"""Append-only decision history log with performance metrics.

Extended with:
 - Sprint-aware fields (sprint number, sprint goal)
 - Performance metrics per decision (velocity, cost, duration, blockers)
 - Aggregate metric computation for sprint reviews
 - Typed decisions with alternatives tracking
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import aiofiles


class DecisionType(str, Enum):
    """Categorises the kind of decision being recorded."""

    GO_NO_GO = "go_no_go"
    PIVOT = "pivot"
    SCOPE_CHANGE = "scope_change"
    ESCALATION = "escalation"
    RESOURCE_ALLOCATION = "resource_allocation"
    SPRINT_COMMITMENT = "sprint_commitment"
    GENERAL = "general"


class Confidence(str, Enum):
    """Confidence level for a decision."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ImpactScope(str, Enum):
    """Scope of impact for a decision."""

    NARROW = "narrow"
    MODERATE = "moderate"
    BROAD = "broad"


@dataclass
class Alternative:
    """An option that was considered but not chosen."""

    description: str
    rejected_reason: str = ""


@dataclass
class PerformanceMetrics:
    """Performance data attached to a decision or cycle."""

    cycle_duration_ms: int = 0
    cost_usd: float = 0.0
    tokens_used: int = 0
    tasks_completed: int = 0
    tasks_committed: int = 0
    blockers_detected: int = 0
    blocker_types: list[str] = field(default_factory=list)

    @property
    def velocity(self) -> float:
        """Tasks completed / tasks committed (0.0 if none committed)."""
        if self.tasks_committed <= 0:
            return 0.0
        return self.tasks_completed / self.tasks_committed


@dataclass
class SprintSummary:
    """Aggregate metrics for a completed sprint."""

    sprint: int
    cycles: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    total_tasks_completed: int = 0
    total_tasks_committed: int = 0
    total_blockers: int = 0
    avg_cycle_duration_ms: float = 0.0

    @property
    def velocity(self) -> float:
        if self.total_tasks_committed <= 0:
            return 0.0
        return self.total_tasks_completed / self.total_tasks_committed


class DecisionHistory:
    """Append-only JSONL log of decisions. Never modified, only appended."""

    def __init__(self, path: str | Path = "memories/decisions.jsonl"):
        self.path = Path(path)

    async def record(
        self,
        decision: str,
        rationale: str = "",
        cycle: int | None = None,
        phase: str = "",
        agents: list[str] | None = None,
        room_id: str = "",
        sprint: int | None = None,
        sprint_goal: str = "",
        metrics: PerformanceMetrics | None = None,
        decision_type: DecisionType = DecisionType.GENERAL,
        alternatives: list[Alternative] | None = None,
        impact: str = "",
        confidence: Confidence | None = None,
        impact_scope: ImpactScope | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle": cycle,
            "phase": phase,
            "decision": decision,
            "rationale": rationale,
            "agents": agents or [],
            "room_id": room_id,
            "decision_type": decision_type.value,
        }
        if sprint is not None:
            entry["sprint"] = sprint
        if sprint_goal:
            entry["sprint_goal"] = sprint_goal
        if metrics is not None:
            entry["metrics"] = asdict(metrics)
        if alternatives:
            entry["alternatives"] = [asdict(a) for a in alternatives]
        if impact:
            entry["impact"] = impact
        if confidence is not None:
            entry["confidence"] = confidence.value
        if impact_scope is not None:
            entry["impact_scope"] = impact_scope.value

        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(self.path, "a", encoding="utf-8") as f:
            await f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def get_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
            lines = await f.readlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
        return entries

    async def search(self, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        results = []
        async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
            async for line in f:
                if keyword.lower() in line.lower():
                    results.append(json.loads(line.strip()))
                    if len(results) >= limit:
                        break
        return results

    async def get_sprint_summary(self, sprint: int) -> SprintSummary:
        """Compute aggregate metrics for a given sprint number."""
        summary = SprintSummary(sprint=sprint)
        if not self.path.exists():
            return summary

        async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("sprint") != sprint:
                    continue

                summary.cycles += 1
                m = entry.get("metrics", {})
                summary.total_cost_usd += m.get("cost_usd", 0.0)
                summary.total_tokens += m.get("tokens_used", 0)
                summary.total_tasks_completed += m.get("tasks_completed", 0)
                summary.total_tasks_committed += m.get("tasks_committed", 0)
                summary.total_blockers += m.get("blockers_detected", 0)
                dur = m.get("cycle_duration_ms", 0)
                # Running average
                if summary.cycles > 0:
                    summary.avg_cycle_duration_ms = (
                        (summary.avg_cycle_duration_ms * (summary.cycles - 1) + dur)
                        / summary.cycles
                    )

        return summary

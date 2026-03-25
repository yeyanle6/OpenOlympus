"""Workflow engine — orchestrates multi-phase project execution.

Runs phases sequentially, each phase creating a Room with the configured
protocol and agents. Phase output feeds into the next phase as context.
Human gates pause execution until approved.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from olympus.workflow.phases import PhaseConfig, HumanRole, ModelTier, WORKFLOW_TEMPLATES

logger = logging.getLogger(__name__)


@dataclass
class PhaseResult:
    """Result of a completed phase."""
    phase_name: str
    status: str  # completed, failed, waiting_approval, skipped
    room_id: str = ""
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_name": self.phase_name,
            "status": self.status,
            "room_id": self.room_id,
            "summary": self.summary[:500],
            "timestamp": self.timestamp,
        }


@dataclass
class ProjectState:
    """Tracks the state of a running project workflow."""
    project_id: str
    name: str
    template: str
    phases: list[PhaseConfig]
    current_phase: int = 0
    status: str = "created"  # created, running, paused, completed, failed
    results: list[PhaseResult] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "template": self.template,
            "current_phase": self.current_phase,
            "total_phases": len(self.phases),
            "current_phase_name": self.phases[self.current_phase].name if self.current_phase < len(self.phases) else "",
            "status": self.status,
            "results": [r.to_dict() for r in self.results],
            "phases": [
                {
                    "name": p.name,
                    "protocol": p.protocol,
                    "agents": p.agents,
                    "model_tier": p.model_tier.value,
                    "human_role": p.human_role.value,
                    "status": self._phase_status(i),
                }
                for i, p in enumerate(self.phases)
            ],
        }

    def _phase_status(self, index: int) -> str:
        if index < self.current_phase:
            result = next((r for r in self.results if r.phase_name == self.phases[index].name), None)
            return result.status if result else "completed"
        elif index == self.current_phase:
            return "active" if self.status == "running" else "pending"
        else:
            return "pending"


class WorkflowEngine:
    """Runs a project through its lifecycle phases."""

    def __init__(self, director: Any):
        self._director = director
        self._projects: dict[str, ProjectState] = {}

    def create_project(
        self,
        name: str,
        template: str = "standard",
        custom_phases: list[PhaseConfig] | None = None,
    ) -> ProjectState:
        """Create a new project with a workflow template."""
        import uuid
        project_id = uuid.uuid4().hex[:12]

        phases = custom_phases or WORKFLOW_TEMPLATES.get(template, WORKFLOW_TEMPLATES["standard"])

        project = ProjectState(
            project_id=project_id,
            name=name,
            template=template,
            phases=list(phases),
        )
        self._projects[project_id] = project
        logger.info("Project %s created: %s (%d phases)", project_id, name, len(phases))
        return project

    def get_project(self, project_id: str) -> ProjectState | None:
        return self._projects.get(project_id)

    def list_projects(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self._projects.values()]

    async def run_phase(self, project_id: str) -> PhaseResult:
        """Run the current phase of a project."""
        project = self._projects.get(project_id)
        if not project:
            return PhaseResult(phase_name="", status="failed", summary="Project not found")

        if project.current_phase >= len(project.phases):
            project.status = "completed"
            return PhaseResult(phase_name="", status="completed", summary="All phases complete")

        phase = project.phases[project.current_phase]
        project.status = "running"

        # Check human gate
        if phase.human_role == HumanRole.ACTIVE:
            logger.info("Phase %s requires human active participation", phase.name)

        # Build context from previous phase results
        context = ""
        if project.results:
            prev = project.results[-1]
            context = f"Previous phase ({prev.phase_name}) concluded:\n{prev.summary[:2000]}"

        # Create room via Director
        task = f"[Project: {project.name}] Phase: {phase.name}\n{phase.description}"
        if context:
            task += f"\n\nContext from previous phase:\n{context}"
        if phase.acceptance_criteria:
            task += "\n\nAcceptance criteria:\n" + "\n".join(f"- {ac}" for ac in phase.acceptance_criteria)
        if phase.max_file_changes > 0:
            task += f"\n\nConstraint: Each task must change ≤{phase.max_file_changes} files."

        # Use Director to create room
        agents_str = " ".join(phase.agents)
        message = f"{phase.protocol} {agents_str}: {task}"

        result = await self._director.chat(message)
        room_id = result.get("room_id", "")

        if not room_id:
            phase_result = PhaseResult(
                phase_name=phase.name,
                status="failed",
                summary=f"Failed to create room: {result.get('reply', '')}",
            )
            project.results.append(phase_result)
            project.status = "failed"
            return phase_result

        # Wait for room to complete
        phase_result = await self._wait_for_room(room_id, phase)
        project.results.append(phase_result)

        if phase_result.status == "completed":
            # Check if human approval needed
            if phase.human_role == HumanRole.APPROVE:
                phase_result.status = "waiting_approval"
                project.status = "paused"
                logger.info("Phase %s waiting for human approval", phase.name)
            else:
                project.current_phase += 1
                if project.current_phase >= len(project.phases):
                    project.status = "completed"
                    logger.info("Project %s completed all phases", project_id)
        else:
            project.status = "failed"

        return phase_result

    def approve_phase(self, project_id: str) -> bool:
        """Human approves current phase, advance to next."""
        project = self._projects.get(project_id)
        if not project or project.status != "paused":
            return False

        # Find waiting result and mark approved
        for r in project.results:
            if r.status == "waiting_approval":
                r.status = "completed"

        project.current_phase += 1
        project.status = "running" if project.current_phase < len(project.phases) else "completed"
        return True

    def reject_phase(self, project_id: str, reason: str = "") -> bool:
        """Human rejects current phase, mark for redo."""
        project = self._projects.get(project_id)
        if not project or project.status != "paused":
            return False

        for r in project.results:
            if r.status == "waiting_approval":
                r.status = "rejected"
                r.summary += f"\n\nRejection reason: {reason}"

        # Don't advance — stay on same phase for redo
        project.status = "running"
        return True

    async def run_all(self, project_id: str) -> list[PhaseResult]:
        """Run all remaining phases (stops at human gates)."""
        results = []
        project = self._projects.get(project_id)
        if not project:
            return results

        while project.current_phase < len(project.phases) and project.status not in ("paused", "failed", "completed"):
            result = await self.run_phase(project_id)
            results.append(result)
            if result.status in ("failed", "waiting_approval"):
                break

        return results

    async def _wait_for_room(self, room_id: str, phase: PhaseConfig) -> PhaseResult:
        """Wait for a room to complete and extract results."""
        for _ in range(360):  # 1 hour max
            rooms = await self._director.get_rooms_status()
            room = next((r for r in rooms if r["room_id"] == room_id), None)
            if not room:
                return PhaseResult(phase_name=phase.name, status="failed", room_id=room_id, summary="Room not found")
            if room["status"] not in ("running", "created"):
                break
            await asyncio.sleep(10)

        # Extract summary from messages
        msgs = self._director.get_room_messages(room_id)
        summary_parts = []
        for m in msgs:
            if m.get("content") and "Agent used" not in m["content"] and len(m["content"]) > 50:
                summary_parts.append(m["content"])

        status = "completed" if room and room.get("status") == "completed" else "failed"

        return PhaseResult(
            phase_name=phase.name,
            status=status,
            room_id=room_id,
            summary="\n---\n".join(s[:1000] for s in summary_parts[-3:]),
            artifacts=[m["content"] for m in msgs if len(m.get("content", "")) > 50 and "Agent used" not in m["content"]],
        )

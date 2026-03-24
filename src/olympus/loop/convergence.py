"""Convergence phase controller for autonomous loop.

Supports optional Sprint boundaries (Scrum-style) that overlay onto the
base phase logic.  When a SprintConfig is provided the controller:
 - maps cycles to sprints (sprint_length cycles each),
 - inserts SPRINT_REVIEW at the last cycle of every sprint,
 - inserts SPRINT_PLANNING at the first cycle of every sprint (after the 1st).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Phase(str, Enum):
    BRAINSTORM = "brainstorm"
    EVALUATE = "evaluate"
    EXECUTE = "execute"
    RETROSPECT = "retrospect"
    SPRINT_PLANNING = "sprint_planning"
    SPRINT_REVIEW = "sprint_review"


@dataclass
class SprintConfig:
    """Optional Scrum-style sprint overlay for the convergence controller."""

    sprint_length: int = 10  # cycles per sprint
    planning_enabled: bool = True
    review_enabled: bool = True


class ConvergenceController:
    """Determines the current phase based on cycle count and history.

    When *sprint* is provided, sprint boundaries take precedence over the
    default retrospect interval — the sprint review replaces the retrospect
    at sprint boundaries.
    """

    def __init__(
        self,
        retrospect_interval: int = 5,
        sprint: SprintConfig | None = None,
    ):
        self.retrospect_interval = retrospect_interval
        self.sprint = sprint

    # ------------------------------------------------------------------
    # Sprint helpers
    # ------------------------------------------------------------------

    def current_sprint(self, cycle: int) -> int:
        """Return 1-based sprint number for a given cycle."""
        if self.sprint is None or cycle <= 0:
            return 0
        return (cycle - 1) // self.sprint.sprint_length + 1

    def is_sprint_boundary(self, cycle: int) -> bool:
        """True when *cycle* is the last cycle of its sprint."""
        if self.sprint is None or cycle <= 0:
            return False
        return cycle % self.sprint.sprint_length == 0

    def is_sprint_start(self, cycle: int) -> bool:
        """True when *cycle* is the first cycle of a new sprint (after sprint 1)."""
        if self.sprint is None or cycle <= 1:
            return False
        return (cycle - 1) % self.sprint.sprint_length == 0 and cycle > 1

    # ------------------------------------------------------------------
    # Phase resolution
    # ------------------------------------------------------------------

    def get_phase(self, cycle: int) -> Phase:
        if cycle <= 0:
            return Phase.BRAINSTORM

        # Sprint boundary checks take precedence
        if self.sprint is not None:
            if self.sprint.review_enabled and self.is_sprint_boundary(cycle):
                return Phase.SPRINT_REVIEW
            if self.sprint.planning_enabled and self.is_sprint_start(cycle):
                return Phase.SPRINT_PLANNING

        # Retrospection triggers every N cycles (after cycle 3)
        if cycle > 3 and cycle % self.retrospect_interval == 0:
            return Phase.RETROSPECT

        if cycle == 1:
            return Phase.BRAINSTORM
        elif cycle == 2:
            return Phase.EVALUATE
        else:
            return Phase.EXECUTE

    def get_phase_rules(self, phase: Phase) -> str:
        rules = {
            Phase.BRAINSTORM: (
                "## Phase: Brainstorm (Cycle 1) — IDEO Forced Divergence\n"
                "STEP 1 — DIVERGE: Each agent MUST generate 3 fundamentally different approaches.\n"
                "  - Approaches must differ at the TECHNICAL ROUTE level, not just wording.\n"
                "  - Example: 'build from scratch' vs 'buy SDK' vs 'fork open-source' — NOT three\n"
                "    variations of the same approach.\n"
                "  - If approaches are too similar, the Critic will reject them.\n"
                "STEP 2 — CONVERGE: After all approaches are on the table, rank the top 3.\n"
                "  - Ranking criteria: feasibility, cost, time-to-value, risk.\n"
                "- End with a clear top-3 list in consensus with rationale for each."
            ),
            Phase.EVALUATE: (
                "## Phase: Evaluate (Cycle 2) — Pixar Brain Trust + Apache Veto\n"
                "- Pick the #1 proposal from brainstorm\n"
                "- Critic runs a Pre-Mortem: assume it failed 6 months from now, find why\n"
                "  NOTE: Critic evaluates the PROPOSAL, not the proposer. Blind evaluation.\n"
                "- Researcher validates feasibility with evidence\n"
                "- Auditor checks for gaps and unstated assumptions\n"
                "- Output: GO or NO-GO decision\n"
                "  If NO-GO: you MUST provide a concrete alternative (Apache principle).\n"
                "  A veto without an alternative is invalid.\n"
                "- If NO-GO with alternative, try #2 or the alternative"
            ),
            Phase.EXECUTE: (
                "## Phase: Execute (Cycle 3+) — OODA Loop\n"
                "OBSERVE first: Before taking action, scan the current state:\n"
                "  - What changed since last cycle? (compare current vs previous Next Action)\n"
                "  - Are there new blockers or completed dependencies?\n"
                "  - Did any Specialist layer output arrive that changes the picture?\n"
                "ORIENT: Based on observations, confirm or adjust the plan.\n"
                "DECIDE + ACT:\n"
                "  - Every cycle MUST produce tangible artifacts (code, files, deployment)\n"
                "  - Pure discussion is FORBIDDEN\n"
                "  - Priority: Ship > Plan > Discuss\n"
                "  - If stuck on the same Next Action for 2 cycles, change direction or shrink scope"
            ),
            Phase.RETROSPECT: (
                "## Phase: Retrospect\n"
                "- Review the last few cycles before continuing\n"
                "- For each recent cycle: what was decided? what was produced? what stalled?\n"
                "- Identify patterns: recurring blockers, drifting goals, abandoned work\n"
                "- Update consensus with a ## Retrospection section\n"
                "- Then proceed with the next action"
            ),
            Phase.SPRINT_PLANNING: (
                "## Phase: Sprint Planning\n"
                "- Review the backlog and select items for this sprint\n"
                "- Break selected items into cycle-sized tasks (WBS)\n"
                "- Set sprint goal aligned with current OKR objectives\n"
                "- Assign tasks to agents based on capabilities\n"
                "- Update consensus with ## Sprint Backlog section\n"
                "- Output: committed sprint backlog with acceptance criteria"
            ),
            Phase.SPRINT_REVIEW: (
                "## Phase: Sprint Review\n"
                "- Demo completed work from this sprint\n"
                "- Compare delivered vs. committed sprint backlog\n"
                "- Calculate velocity: tasks completed / tasks committed\n"
                "- Identify incomplete items — return to backlog or carry over\n"
                "- Run a mini-retrospective: what went well, what to improve\n"
                "- Update consensus with ## Sprint Review section\n"
                "- Prepare for next sprint planning"
            ),
        }
        return rules[phase]

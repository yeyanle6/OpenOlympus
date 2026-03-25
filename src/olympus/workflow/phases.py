"""Project phases — defines the lifecycle stages of a project.

Each phase specifies the collaboration protocol, agent roles, model preference,
human involvement level, and acceptance criteria.

Based on real-world multi-model workflow experience:
  Requirements (roundtable, human active)
  → Architecture (debate, human observe)
  → Decomposition (pipeline, auto)
  → Execution (per-workflow, auto by complexity)
  → Testing (peer_review, auto)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class HumanRole(str, Enum):
    """How much the human participates in this phase."""
    ACTIVE = "active"      # Human leads or co-leads discussion
    OBSERVE = "observe"    # Human watches, occasionally interjects
    APPROVE = "approve"    # Human reviews output and approves/rejects
    NONE = "none"          # Fully autonomous


class ModelTier(str, Enum):
    """Model complexity tier for auto-selection."""
    HIGH = "high"          # Most capable (opus, gpt-4o)
    MEDIUM = "medium"      # Balanced (sonnet, gpt-4o-mini)
    LOW = "low"            # Fast/cheap (haiku, flash)
    AUTO = "auto"          # System decides based on task complexity


@dataclass
class PhaseConfig:
    """Configuration for a single project phase."""
    name: str
    protocol: str                          # roundtable, pipeline, peer_review, debate, delegate
    agents: list[str]                      # Agent roles to involve
    model_tier: ModelTier = ModelTier.AUTO  # Which model tier to prefer
    human_role: HumanRole = HumanRole.NONE
    max_file_changes: int = 0              # 0 = unlimited, >0 = per-task limit
    acceptance_criteria: list[str] = field(default_factory=list)
    depends_on: str = ""                   # Previous phase name that must complete first
    description: str = ""


# ── Default project workflow templates ────────────────────────

STANDARD_WORKFLOW: list[PhaseConfig] = [
    PhaseConfig(
        name="requirements",
        protocol="roundtable",
        agents=["planner", "architect", "critic"],
        model_tier=ModelTier.HIGH,
        human_role=HumanRole.ACTIVE,
        description="Requirement analysis — human + agents define scope and goals",
        acceptance_criteria=[
            "Goal statement defined",
            "Scope (in/out) agreed",
            "Key constraints identified",
        ],
    ),
    PhaseConfig(
        name="architecture",
        protocol="roundtable",
        agents=["architect", "critic"],
        model_tier=ModelTier.HIGH,
        human_role=HumanRole.OBSERVE,
        description="Architecture design — architects debate from different angles",
        acceptance_criteria=[
            "Architecture decision record created",
            "Trade-offs documented with options",
            "Critic pre-mortem passed",
        ],
        depends_on="requirements",
    ),
    PhaseConfig(
        name="decomposition",
        protocol="pipeline",
        agents=["planner", "architect"],
        model_tier=ModelTier.MEDIUM,
        human_role=HumanRole.APPROVE,
        max_file_changes=3,
        description="Task decomposition — break into small tasks, group into workflows",
        acceptance_criteria=[
            "Tasks broken into ≤3 file changes each",
            "Tasks grouped into workflows",
            "Architect reviewed decomposition",
        ],
        depends_on="architecture",
    ),
    PhaseConfig(
        name="execution",
        protocol="pipeline",
        agents=["builder", "reviewer"],
        model_tier=ModelTier.AUTO,
        human_role=HumanRole.NONE,
        max_file_changes=3,
        description="Code execution — implement each workflow, review after each",
        acceptance_criteria=[
            "Code written with unit tests",
            "Architect review passed per workflow",
            "No regressions in existing tests",
        ],
        depends_on="decomposition",
    ),
    PhaseConfig(
        name="testing",
        protocol="peer_review",
        agents=["tester", "architect"],
        model_tier=ModelTier.HIGH,
        human_role=HumanRole.NONE,
        description="Integration testing — test plan + execution + fix loop",
        acceptance_criteria=[
            "Test plan reviewed by architect",
            "All tests pass",
            "Edge cases covered",
        ],
        depends_on="execution",
    ),
]


RESEARCH_WORKFLOW: list[PhaseConfig] = [
    PhaseConfig(
        name="research",
        protocol="roundtable",
        agents=["researcher", "planner", "critic"],
        model_tier=ModelTier.HIGH,
        human_role=HumanRole.ACTIVE,
        description="Deep research on a topic",
        acceptance_criteria=["Key findings documented", "Sources cited"],
    ),
    PhaseConfig(
        name="analysis",
        protocol="roundtable",
        agents=["architect", "critic", "planner"],
        model_tier=ModelTier.HIGH,
        human_role=HumanRole.OBSERVE,
        description="Analyze research findings and form conclusions",
        acceptance_criteria=["Options compared", "Recommendation given"],
        depends_on="research",
    ),
    PhaseConfig(
        name="planning",
        protocol="pipeline",
        agents=["planner", "critic"],
        model_tier=ModelTier.MEDIUM,
        human_role=HumanRole.APPROVE,
        description="Create actionable plan from analysis",
        acceptance_criteria=["Task list with acceptance criteria", "Risk register"],
        depends_on="analysis",
    ),
]


WORKFLOW_TEMPLATES: dict[str, list[PhaseConfig]] = {
    "standard": STANDARD_WORKFLOW,
    "research": RESEARCH_WORKFLOW,
}

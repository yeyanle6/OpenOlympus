"""Mock LLM agent for development and testing without Claude CLI.

Set environment variable OLYMPUS_MOCK=1 to enable mock mode globally.
Or pass mock=True to LLMAgent constructor.

Mock responses are deterministic based on agent role and task content,
making tests reproducible.
"""

from __future__ import annotations

import hashlib
import os
import time

from olympus.types import Message, AgentResult
from olympus.agent.definition import AgentDefinition

MOCK_ENABLED = os.environ.get("OLYMPUS_MOCK", "").strip() in ("1", "true", "yes")


ROLE_TEMPLATES: dict[str, str] = {
    "planner": (
        "## Plan\n\n"
        "Based on the task, here is a structured approach:\n"
        "1. Analyze requirements and constraints\n"
        "2. Break down into subtasks\n"
        "3. Assign priorities and dependencies\n\n"
        "**Goal**: {task_summary}\n"
        "**Scope**: IN — task analysis and decomposition. OUT — implementation.\n"
    ),
    "architect": (
        "## Architecture Advisory\n\n"
        "**Context**: {task_summary}\n\n"
        "### Options\n"
        "| Option | Pros | Cons |\n"
        "|--------|------|------|\n"
        "| A: Simple | Fast, low risk | Limited flexibility |\n"
        "| B: Modular | Extensible | More complexity |\n\n"
        "**Recommendation**: Option B for long-term maintainability.\n"
    ),
    "critic": (
        "## Critic Review\n\n"
        "**Pre-mortem**: Assuming this failed in 6 months:\n"
        "- Risk 1: Scope creep without clear boundaries\n"
        "- Risk 2: Insufficient testing coverage\n\n"
        "**Bias check**: Confirmation bias detected — alternatives not explored.\n\n"
        "**Verdict**: CONDITIONAL-GO — address risks before proceeding.\n"
    ),
    "builder": (
        "## Implementation Summary\n\n"
        "Completed the following:\n"
        "- Analyzed existing codebase patterns\n"
        "- Implemented core logic\n"
        "- Added basic test coverage\n\n"
        "**Files modified**: (mock mode — no actual changes)\n"
    ),
    "researcher": (
        "## Research Report\n\n"
        "**Query**: {task_summary}\n\n"
        "### Findings\n"
        "1. Current state of the art supports the proposed approach\n"
        "2. Alternative methods exist but trade off complexity for marginal gains\n"
        "3. Open questions remain around edge cases\n\n"
        "**Confidence**: Medium\n"
    ),
}

DEFAULT_TEMPLATE = (
    "## {role} Response\n\n"
    "Analyzed the task: {task_summary}\n\n"
    "Key observations:\n"
    "- The approach is feasible within current constraints\n"
    "- Consider edge cases and error handling\n"
    "- Recommend iterative validation\n"
)


def mock_response(definition: AgentDefinition, task: str) -> AgentResult:
    """Generate a deterministic mock response for an agent."""
    start = time.monotonic()
    role = definition.agent_id
    task_summary = task[:100]

    template = ROLE_TEMPLATES.get(role, DEFAULT_TEMPLATE)
    content = template.format(
        task_summary=task_summary,
        role=definition.name,
    )

    # Make it slightly unique based on task hash
    task_hash = hashlib.md5(task.encode()).hexdigest()[:8]
    content += f"\n\n*[Mock response {task_hash} for {role}]*\n"

    duration_ms = int((time.monotonic() - start) * 1000)

    return AgentResult(
        status="success",
        artifact=content,
        tokens_used=len(content),
        cost_usd=0.0,
        agent_id=role,
        duration_ms=duration_ms,
    )

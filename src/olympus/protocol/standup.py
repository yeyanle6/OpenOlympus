"""Standup protocol — each participant gives a brief status update in a single round."""

from __future__ import annotations

from olympus.types import (
    Message,
    MessageType,
    AgentResult,
    AgentLayer,
    OnMessage,
)
from olympus.agent.llm_agent import LLMAgent
from olympus.memory.session import SessionMemory
from olympus.room.pause_gate import PauseGate
from olympus.protocol.base import Protocol


class StandupProtocol(Protocol):
    """Single-round status collection from filtered participants.

    Each agent reports: what they did, what's next, and blockers.
    No iteration — every agent speaks exactly once.

    Participant filtering:
        - ``allowed_layers``: restrict to specific AgentLayers
        - ``allowed_roles``: restrict to specific agent_id values
        If both are ``None``, all agents participate.
    """

    def __init__(
        self,
        *,
        allowed_layers: list[AgentLayer] | None = None,
        allowed_roles: list[str] | None = None,
    ):
        self.allowed_layers = allowed_layers
        self.allowed_roles = allowed_roles

    # ── participant filter ───────────────────────────────────────

    def select_participants(self, agents: list[LLMAgent]) -> list[LLMAgent]:
        """Return agents that match the layer/role filter."""
        selected: list[LLMAgent] = []
        for agent in agents:
            if self.allowed_roles and agent.agent_id not in self.allowed_roles:
                continue
            if (
                self.allowed_layers
                and hasattr(agent, "definition")
                and agent.definition.layer not in self.allowed_layers
            ):
                continue
            selected.append(agent)
        return selected

    # ── protocol entry point ─────────────────────────────────────

    async def run(
        self,
        agents: list[LLMAgent],
        task: str,
        context: list[Message] | None = None,
        *,
        gate: PauseGate | None = None,
        on_message: OnMessage = None,
    ) -> list[AgentResult]:
        participants = self.select_participants(agents)
        if not participants:
            return []

        memory = SessionMemory()
        if context:
            for m in context:
                memory.add(m)

        standup_prompt = (
            f"{task}\n\n"
            "Give a brief standup update:\n"
            "1. What you accomplished\n"
            "2. What you plan to do next\n"
            "3. Any blockers"
        )

        all_results: list[AgentResult] = []

        for agent in participants:
            if gate:
                await gate.checkpoint()

            result = await agent.execute(standup_prompt, memory.get_all(), use_tools=False)
            all_results.append(result)

            if result.status == "success":
                msg = Message(
                    type=MessageType.STATUS,
                    sender=agent.agent_id,
                    content=result.artifact,
                    metadata={"protocol": "standup"},
                )
                memory.add(msg)
                if on_message:
                    on_message(msg)

        return all_results

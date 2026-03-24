"""Delegate protocol — single agent executes the full task."""

from __future__ import annotations

from olympus.types import Message, MessageType, AgentResult, OnMessage
from olympus.agent.llm_agent import LLMAgent
from olympus.room.pause_gate import PauseGate
from olympus.protocol.base import Protocol


class DelegateProtocol(Protocol):
    """Delegates the task to a single agent (the first in the list)."""

    async def run(
        self,
        agents: list[LLMAgent],
        task: str,
        context: list[Message] | None = None,
        *,
        gate: PauseGate | None = None,
        on_message: OnMessage = None,
    ) -> list[AgentResult]:
        if not agents:
            return []

        agent = agents[0]

        if gate:
            await gate.checkpoint()

        result = await agent.execute(task, context)

        if result.status == "success" and on_message:
            on_message(Message(
                type=MessageType.ARTIFACT,
                sender=agent.agent_id,
                content=result.artifact,
            ))

        return [result]

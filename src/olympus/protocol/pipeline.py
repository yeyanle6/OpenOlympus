"""Pipeline protocol — sequential handoff chain A -> B -> C."""

from __future__ import annotations

from olympus.types import Message, MessageType, AgentResult, OnMessage
from olympus.agent.llm_agent import LLMAgent
from olympus.memory.session import SessionMemory
from olympus.room.pause_gate import PauseGate
from olympus.protocol.base import Protocol


class PipelineProtocol(Protocol):
    """Agents execute sequentially, each receiving prior outputs as context."""

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

        memory = SessionMemory()
        if context:
            for m in context:
                memory.add(m)

        all_results: list[AgentResult] = []
        current_task = task

        for i, agent in enumerate(agents):
            if gate:
                await gate.checkpoint()

            result = await agent.execute(current_task, memory.get_all())
            all_results.append(result)

            if result.status != "success":
                break

            msg = Message(
                type=MessageType.ARTIFACT,
                sender=agent.agent_id,
                content=result.artifact,
                metadata={"stage": i + 1, "pipeline_length": len(agents)},
            )
            memory.add(msg)
            if on_message:
                on_message(msg)

            # Next agent receives refined context
            if i < len(agents) - 1:
                current_task = (
                    f"Previous agent ({agent.agent_id}) produced:\n\n"
                    f"{result.artifact}\n\n"
                    f"Original task: {task}\n\n"
                    f"Continue from where they left off."
                )

        return all_results

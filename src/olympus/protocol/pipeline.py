"""Pipeline protocol — sequential handoff chain A -> B -> C with step-level retry."""

from __future__ import annotations

import logging

from olympus.types import Message, MessageType, AgentResult, OnMessage
from olympus.agent.llm_agent import LLMAgent
from olympus.memory.session import SessionMemory
from olympus.room.pause_gate import PauseGate
from olympus.protocol.base import Protocol

logger = logging.getLogger(__name__)


class PipelineProtocol(Protocol):
    """Agents execute sequentially, each receiving prior outputs as context.

    If a step fails, it retries up to ``max_retries`` times before
    aborting the pipeline.
    """

    def __init__(self, max_retries: int = 1):
        self.max_retries = max_retries

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

            # Step-level retry
            result: AgentResult | None = None
            for attempt in range(1 + self.max_retries):
                result = await agent.execute(current_task, memory.get_all())

                if result.status == "success":
                    break

                if attempt < self.max_retries:
                    logger.warning(
                        "Pipeline step %d (%s) failed (attempt %d/%d): %s, retrying",
                        i + 1, agent.agent_id, attempt + 1, 1 + self.max_retries,
                        result.error,
                    )

            assert result is not None
            all_results.append(result)

            if result.status != "success":
                # All retries exhausted — emit failure and abort
                if on_message:
                    on_message(Message(
                        type=MessageType.STATUS,
                        sender=agent.agent_id,
                        content=f"Pipeline aborted at step {i + 1}/{len(agents)} "
                                f"({agent.agent_id}): {result.error}",
                        metadata={"stage": i + 1, "aborted": True},
                    ))
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

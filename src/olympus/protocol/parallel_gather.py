"""Parallel gather protocol — fan-out/fan-in for concurrent agent execution."""

from __future__ import annotations

import asyncio

from olympus.types import Message, MessageType, AgentResult, OnMessage
from olympus.agent.llm_agent import LLMAgent
from olympus.room.pause_gate import PauseGate
from olympus.protocol.base import Protocol


class ParallelGatherProtocol(Protocol):
    """Run all agents concurrently, gather results, optionally synthesize."""

    def __init__(self, synthesizer_index: int | None = None):
        self.synthesizer_index = synthesizer_index

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

        if gate:
            await gate.checkpoint()

        # Fan-out: all agents in parallel
        coros = [agent.execute(task, context) for agent in agents]
        raw_results = await asyncio.gather(*coros, return_exceptions=True)

        all_results: list[AgentResult] = []
        for i, r in enumerate(raw_results):
            if isinstance(r, BaseException):
                result = AgentResult(
                    status="failed",
                    error=str(r),
                    agent_id=agents[i].agent_id,
                )
            else:
                result = r
            all_results.append(result)

            if result.status == "success" and on_message:
                on_message(Message(
                    type=MessageType.OPINION,
                    sender=agents[i].agent_id,
                    content=result.artifact,
                ))

        # Optional synthesis pass
        if (
            self.synthesizer_index is not None
            and 0 <= self.synthesizer_index < len(agents)
        ):
            artifacts = [r.artifact for r in all_results if r.status == "success"]
            if artifacts:
                synthesis_task = (
                    f"Synthesize these {len(artifacts)} results:\n\n"
                    + "\n---\n".join(artifacts)
                    + f"\n\nOriginal task: {task}"
                )
                synth = await agents[self.synthesizer_index].execute(
                    synthesis_task, context
                )
                all_results.append(synth)
                if synth.status == "success" and on_message:
                    on_message(Message(
                        type=MessageType.ARTIFACT,
                        sender=agents[self.synthesizer_index].agent_id,
                        content=synth.artifact,
                        metadata={"synthesis": True},
                    ))

        return all_results

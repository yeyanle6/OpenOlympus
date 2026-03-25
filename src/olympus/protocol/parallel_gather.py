"""Parallel gather protocol — fan-out/fan-in with explicit merge strategy."""

from __future__ import annotations

import asyncio
from enum import Enum

from olympus.types import Message, MessageType, AgentResult, OnMessage
from olympus.agent.llm_agent import LLMAgent
from olympus.room.pause_gate import PauseGate
from olympus.protocol.base import Protocol


class MergeStrategy(str, Enum):
    """How to combine parallel results in the fan-in phase."""
    CONCAT = "concat"        # Concatenate all results
    SELECT_BEST = "select_best"  # Synthesizer picks the best one
    VOTE = "vote"            # Count [APPROVED]/[DONE] signals as votes
    SYNTHESIZE = "synthesize"  # Synthesizer merges into unified output


class ParallelGatherProtocol(Protocol):
    """Run all agents concurrently, gather results, merge with explicit strategy.

    merge_strategy controls the fan-in phase:
    - concat: just collect all results (default, no synthesizer needed)
    - select_best: synthesizer picks the best result
    - vote: count approval signals, emit majority decision
    - synthesize: synthesizer merges all results into one
    """

    def __init__(
        self,
        synthesizer_index: int | None = None,
        merge_strategy: MergeStrategy = MergeStrategy.CONCAT,
    ):
        self.synthesizer_index = synthesizer_index
        self.merge_strategy = merge_strategy

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

        # Fan-in: merge results based on strategy
        successful = [r for r in all_results if r.status == "success"]

        if self.merge_strategy == MergeStrategy.VOTE:
            # Count approval signals
            import re
            approvals = sum(
                1 for r in successful
                if re.search(r"\[APPROVED\]|\[DONE\]|\[GO\]", r.artifact, re.IGNORECASE)
            )
            verdict = "APPROVED" if approvals > len(agents) / 2 else "NOT APPROVED"
            if on_message:
                on_message(Message(
                    type=MessageType.DECISION,
                    sender="parallel_gather",
                    content=f"[{verdict}] Vote: {approvals}/{len(agents)} approved",
                    metadata={"approvals": approvals, "total": len(agents)},
                ))

        elif self.merge_strategy in (MergeStrategy.SYNTHESIZE, MergeStrategy.SELECT_BEST):
            # Need a synthesizer
            if self.synthesizer_index is not None and 0 <= self.synthesizer_index < len(agents):
                artifacts = [r.artifact for r in successful]
                if artifacts:
                    if self.merge_strategy == MergeStrategy.SYNTHESIZE:
                        synth_task = (
                            f"Synthesize these {len(artifacts)} results into a unified output:\n\n"
                            + "\n---\n".join(artifacts)
                            + f"\n\nOriginal task: {task}"
                        )
                    else:
                        synth_task = (
                            f"Select the best result from these {len(artifacts)} options. "
                            f"Explain why it's best:\n\n"
                            + "\n---\n".join(artifacts)
                            + f"\n\nOriginal task: {task}"
                        )
                    synth = await agents[self.synthesizer_index].execute(
                        synth_task, context
                    )
                    all_results.append(synth)
                    if synth.status == "success" and on_message:
                        on_message(Message(
                            type=MessageType.ARTIFACT,
                            sender=agents[self.synthesizer_index].agent_id,
                            content=synth.artifact,
                            metadata={"merge_strategy": self.merge_strategy.value},
                        ))

        # CONCAT: no additional processing needed

        return all_results

"""Roundtable protocol — agents take turns speaking, each seeing all prior messages."""

from __future__ import annotations

import re

from olympus.types import Message, MessageType, AgentResult, OnMessage
from olympus.agent.llm_agent import LLMAgent
from olympus.memory.session import SessionMemory
from olympus.room.pause_gate import PauseGate
from olympus.protocol.base import Protocol


class RoundtableProtocol(Protocol):
    """Multi-agent round-robin discussion. Each agent speaks one at a time,
    seeing all prior messages for proper conversational flow."""

    def __init__(self, max_rounds: int = 5, token_budget: int = 500_000):
        self.max_rounds = max_rounds
        self.token_budget = token_budget

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
        total_tokens = 0

        for round_num in range(1, self.max_rounds + 1):
            done_count = 0

            for agent in agents:
                if gate:
                    await gate.checkpoint()

                # Each agent sees all prior messages; no tools in discussion
                result = await agent.execute(task, memory.get_all(), use_tools=False)
                all_results.append(result)
                total_tokens += result.tokens_used

                if result.status == "success":
                    msg = Message(
                        type=MessageType.OPINION,
                        sender=agent.agent_id,
                        content=result.artifact,
                        metadata={"round": round_num},
                    )
                    memory.add(msg)
                    if on_message:
                        on_message(msg)

                    if self._has_done_signal(result.artifact):
                        done_count += 1

                # Token budget check
                if total_tokens >= self.token_budget:
                    return all_results

            # Majority convergence
            if done_count > len(agents) / 2:
                return all_results

        return all_results

    @staticmethod
    def _has_done_signal(text: str) -> bool:
        return bool(re.search(r"\[DONE\]|\[CONVERGED\]", text, re.IGNORECASE))

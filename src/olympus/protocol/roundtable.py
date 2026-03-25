"""Roundtable protocol — agents take turns speaking, each seeing all prior messages."""

from __future__ import annotations

import re

from olympus.types import Message, MessageType, AgentResult, OnMessage
from olympus.agent.llm_agent import LLMAgent
from olympus.memory.session import SessionMemory
from olympus.room.pause_gate import PauseGate
from olympus.protocol.base import Protocol


class RoundtableProtocol(Protocol):
    """Multi-agent round-robin discussion with silence detection.

    Convergence conditions (any triggers early stop):
    - Majority of agents signal [DONE] or [CONVERGED]
    - Token budget exceeded
    - Max rounds reached
    - Silence detected: 2 consecutive rounds with >80% content overlap
    """

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
        prev_round_texts: list[str] = []  # Texts from previous round

        for round_num in range(1, self.max_rounds + 1):
            done_count = 0
            current_round_texts: list[str] = []

            for agent in agents:
                if gate:
                    await gate.checkpoint()

                result = await agent.execute(task, memory.get_all(), use_tools=False)
                all_results.append(result)
                total_tokens += result.tokens_used

                if result.status == "success" and result.artifact.strip() and len(result.artifact.strip()) >= 10:
                    msg = Message(
                        type=MessageType.OPINION,
                        sender=agent.agent_id,
                        content=result.artifact,
                        metadata={"round": round_num},
                    )
                    memory.add(msg)
                    if on_message:
                        on_message(msg)

                    current_round_texts.append(result.artifact)

                    if self._has_done_signal(result.artifact):
                        done_count += 1

                # Token budget check
                if total_tokens >= self.token_budget:
                    return all_results

            # Majority convergence
            if done_count > len(agents) / 2:
                return all_results

            # Silence detection: if this round repeats previous round
            if prev_round_texts and self._is_silent(prev_round_texts, current_round_texts):
                return all_results

            prev_round_texts = current_round_texts

        return all_results

    @staticmethod
    def _has_done_signal(text: str) -> bool:
        return bool(re.search(r"\[DONE\]|\[CONVERGED\]", text, re.IGNORECASE))

    @staticmethod
    def _is_silent(prev_texts: list[str], curr_texts: list[str]) -> bool:
        """Detect if current round is repeating previous round (>80% overlap)."""
        if not prev_texts or not curr_texts:
            return False

        overlap_count = 0
        for curr in curr_texts:
            curr_keywords = set(re.findall(r'\w{4,}', curr.lower()))
            if not curr_keywords:
                continue
            for prev in prev_texts:
                prev_keywords = set(re.findall(r'\w{4,}', prev.lower()))
                if not prev_keywords:
                    continue
                intersection = curr_keywords & prev_keywords
                union = curr_keywords | prev_keywords
                if union and len(intersection) / len(union) > 0.8:
                    overlap_count += 1
                    break

        return overlap_count >= len(curr_texts) * 0.8

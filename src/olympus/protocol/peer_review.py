"""Peer review protocol — author + reviewer iterate until [APPROVED]."""

from __future__ import annotations

import re

from olympus.types import Message, MessageType, AgentResult, OnMessage
from olympus.agent.llm_agent import LLMAgent
from olympus.memory.session import SessionMemory
from olympus.room.pause_gate import PauseGate
from olympus.protocol.base import Protocol


class PeerReviewProtocol(Protocol):
    """Author produces work, reviewer critiques. Iterate until [APPROVED] or max rounds."""

    def __init__(self, max_rounds: int = 3):
        self.max_rounds = max_rounds

    async def run(
        self,
        agents: list[LLMAgent],
        task: str,
        context: list[Message] | None = None,
        *,
        gate: PauseGate | None = None,
        on_message: OnMessage = None,
    ) -> list[AgentResult]:
        if len(agents) < 2:
            return []

        author = agents[0]
        reviewer = agents[1]
        memory = SessionMemory()
        if context:
            for m in context:
                memory.add(m)

        all_results: list[AgentResult] = []

        for round_num in range(1, self.max_rounds + 1):
            # Author writes/revises
            if gate:
                await gate.checkpoint()

            if round_num == 1:
                author_task = task
            else:
                last_review = all_results[-1].artifact if all_results else ""
                author_task = (
                    f"Original task: {task}\n\n"
                    f"Reviewer feedback:\n{last_review}\n\n"
                    f"Please revise your work based on this feedback."
                )

            author_result = await author.execute(author_task, memory.get_all())
            all_results.append(author_result)

            if author_result.status == "success":
                msg = Message(
                    type=MessageType.ARTIFACT,
                    sender=author.agent_id,
                    content=author_result.artifact,
                    metadata={"round": round_num, "role": "author"},
                )
                memory.add(msg)
                if on_message:
                    on_message(msg)

            # Reviewer critiques
            if gate:
                await gate.checkpoint()

            # Pixar Brain Trust principle: blind review — hide author identity
            # Reviewer evaluates the WORK, not the person
            review_task = (
                f"Review the following work for the task: {task}\n\n"
                f"Work to review (author identity hidden for unbiased evaluation):\n"
                f"---\n{author_result.artifact}\n---\n\n"
                f"Evaluate the work on its merits. Provide constructive feedback.\n"
                f"If the work is satisfactory, include [APPROVED] in your response.\n"
                f"If you identify critical issues, include [BLOCKED] with a concrete "
                f"alternative approach (veto without alternative is invalid)."
            )

            review_result = await reviewer.execute(review_task, memory.get_all(), use_tools=False)
            all_results.append(review_result)

            if review_result.status == "success":
                msg = Message(
                    type=MessageType.REVIEW,
                    sender=reviewer.agent_id,
                    content=review_result.artifact,
                    metadata={"round": round_num, "role": "reviewer"},
                )
                memory.add(msg)
                if on_message:
                    on_message(msg)

                if self._is_approved(review_result.artifact):
                    return all_results

        return all_results

    @staticmethod
    def _is_approved(text: str) -> bool:
        return bool(re.search(r"\[APPROVED\]", text, re.IGNORECASE))

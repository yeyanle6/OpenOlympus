"""Review meeting protocol — presenter + multiple reviewers iterate until consensus."""

from __future__ import annotations

import re

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


class ReviewMeetingProtocol(Protocol):
    """Structured review with one presenter and N reviewers.

    Flow per round:
        1. Presenter presents / revises work
        2. Each reviewer provides feedback
        3. If any reviewer says [BLOCKED] → stop immediately
        4. If all reviewers say [APPROVED] → stop (consensus)
        5. Otherwise presenter revises, next round

    Participant filtering:
        - ``presenter_role``: agent_id of the presenter (first agent if None)
        - ``reviewer_layers``: restrict reviewers to specific layers
        - ``reviewer_roles``: restrict reviewers to specific agent_id values
    """

    def __init__(
        self,
        max_rounds: int = 3,
        *,
        presenter_role: str | None = None,
        reviewer_layers: list[AgentLayer] | None = None,
        reviewer_roles: list[str] | None = None,
    ):
        self.max_rounds = max_rounds
        self.presenter_role = presenter_role
        self.reviewer_layers = reviewer_layers
        self.reviewer_roles = reviewer_roles

    # ── participant selection ────────────────────────────────────

    def select_participants(
        self, agents: list[LLMAgent]
    ) -> tuple[LLMAgent | None, list[LLMAgent]]:
        """Return (presenter, reviewers). Presenter is picked first, rest are reviewers."""
        presenter: LLMAgent | None = None
        reviewers: list[LLMAgent] = []

        for agent in agents:
            if presenter is None and (
                self.presenter_role is None
                or agent.agent_id == self.presenter_role
            ):
                presenter = agent
                continue

            # Reviewer filter
            if self.reviewer_roles and agent.agent_id not in self.reviewer_roles:
                continue
            if (
                self.reviewer_layers
                and hasattr(agent, "definition")
                and agent.definition.layer not in self.reviewer_layers
            ):
                continue
            reviewers.append(agent)

        return presenter, reviewers

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
        presenter, reviewers = self.select_participants(agents)
        if presenter is None or not reviewers:
            return []

        memory = SessionMemory()
        if context:
            for m in context:
                memory.add(m)

        all_results: list[AgentResult] = []

        for round_num in range(1, self.max_rounds + 1):
            # ── Presenter turn ───────────────────────────────────
            if gate:
                await gate.checkpoint()

            if round_num == 1:
                presenter_task = task
            else:
                feedback_parts = [
                    r.artifact
                    for r in all_results[-len(reviewers) :]
                    if r.status == "success"
                ]
                presenter_task = (
                    f"Original task: {task}\n\n"
                    f"Reviewer feedback from round {round_num - 1}:\n"
                    + "\n---\n".join(feedback_parts)
                    + "\n\nPlease revise your work based on this feedback."
                )

            presenter_result = await presenter.execute(
                presenter_task, memory.get_all()
            )
            all_results.append(presenter_result)

            if presenter_result.status == "success":
                msg = Message(
                    type=MessageType.ARTIFACT,
                    sender=presenter.agent_id,
                    content=presenter_result.artifact,
                    metadata={"round": round_num, "role": "presenter"},
                )
                memory.add(msg)
                if on_message:
                    on_message(msg)

            # ── Reviewer turns ───────────────────────────────────
            approved_count = 0
            for reviewer in reviewers:
                if gate:
                    await gate.checkpoint()

                review_task = (
                    f"Review the following work for the task: {task}\n\n"
                    f"Work to review:\n{presenter_result.artifact}\n\n"
                    f"Provide constructive feedback. "
                    f"Include [APPROVED] if satisfactory, or [BLOCKED] if there "
                    f"is a critical issue that must be resolved."
                )

                review_result = await reviewer.execute(
                    review_task, memory.get_all(), use_tools=False
                )
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

                    # Blocked = immediate stop
                    if self._is_blocked(review_result.artifact):
                        return all_results

                    if self._is_approved(review_result.artifact):
                        approved_count += 1

            # All reviewers approved → consensus
            if approved_count == len(reviewers):
                return all_results

        return all_results

    # ── terminal signals ─────────────────────────────────────────

    @staticmethod
    def _is_approved(text: str) -> bool:
        return bool(re.search(r"\[APPROVED\]", text, re.IGNORECASE))

    @staticmethod
    def _is_blocked(text: str) -> bool:
        return bool(re.search(r"\[BLOCKED\]", text, re.IGNORECASE))

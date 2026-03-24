"""Decision gate protocol — go/no-go vote with veto power."""

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


class DecisionGateProtocol(Protocol):
    """Single-round go/no-go decision gate.

    All eligible agents vote on a proposal. The outcome is:
        - [APPROVED] if votes reach ``approval_threshold`` (default: majority)
        - [BLOCKED]  if any agent vetoes (includes [BLOCKED] in response)

    A ``DECISION`` summary message is emitted with the final tally.

    Participant filtering:
        - ``voter_layers``: restrict voters to specific layers
        - ``voter_roles``: restrict voters to specific agent_id values
        If both are ``None``, all agents vote.
    """

    def __init__(
        self,
        *,
        approval_threshold: float = 0.5,
        voter_layers: list[AgentLayer] | None = None,
        voter_roles: list[str] | None = None,
    ):
        self.approval_threshold = approval_threshold
        self.voter_layers = voter_layers
        self.voter_roles = voter_roles

    # ── participant filter ───────────────────────────────────────

    def select_participants(self, agents: list[LLMAgent]) -> list[LLMAgent]:
        """Return agents that match the layer/role filter."""
        selected: list[LLMAgent] = []
        for agent in agents:
            if self.voter_roles and agent.agent_id not in self.voter_roles:
                continue
            if (
                self.voter_layers
                and hasattr(agent, "definition")
                and agent.definition.layer not in self.voter_layers
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
        voters = self.select_participants(agents)
        if not voters:
            return []

        memory = SessionMemory()
        if context:
            for m in context:
                memory.add(m)

        vote_prompt = (
            f"Decision required: {task}\n\n"
            "Cast your vote on this proposal:\n"
            "- Include [APPROVED] if you support proceeding\n"
            "- Include [BLOCKED] if there is a critical issue that prevents proceeding.\n"
            "  IMPORTANT: If you vote [BLOCKED], you MUST provide at least one concrete\n"
            "  alternative approach. A veto without an alternative is invalid and will be\n"
            "  recorded as ABSTAINED. This follows the Apache Foundation principle:\n"
            "  'vetoes must be accompanied by a technical justification and alternative.'\n"
            "Explain your reasoning briefly."
        )

        all_results: list[AgentResult] = []
        approvals = 0
        blocks = 0

        for agent in voters:
            if gate:
                await gate.checkpoint()

            result = await agent.execute(vote_prompt, memory.get_all(), use_tools=False)
            all_results.append(result)

            if result.status == "success":
                msg = Message(
                    type=MessageType.VOTE,
                    sender=agent.agent_id,
                    content=result.artifact,
                    metadata={"protocol": "decision_gate"},
                )
                memory.add(msg)
                if on_message:
                    on_message(msg)

                if self._is_blocked(result.artifact):
                    # Apache principle: veto must include alternative
                    has_alternative = self._has_alternative(result.artifact)
                    if has_alternative:
                        blocks += 1
                        decision_msg = Message(
                            type=MessageType.DECISION,
                            sender="decision_gate",
                            content=f"[BLOCKED] Vetoed by {agent.agent_id} (with alternative provided). "
                            f"Votes so far: {approvals} approved, {blocks} blocked "
                            f"out of {len(all_results)} cast.",
                            metadata={
                                "outcome": "blocked",
                                "blocker": agent.agent_id,
                                "approvals": approvals,
                                "blocks": blocks,
                                "total_voters": len(voters),
                                "has_alternative": True,
                            },
                        )
                        memory.add(decision_msg)
                        if on_message:
                            on_message(decision_msg)
                        return all_results
                    else:
                        # Veto without alternative → downgraded to ABSTAINED
                        decision_msg = Message(
                            type=MessageType.DECISION,
                            sender="decision_gate",
                            content=f"[ABSTAINED] {agent.agent_id} vetoed without providing "
                            f"an alternative. Vote downgraded to abstention per Apache principle.",
                            metadata={
                                "outcome": "abstained_veto",
                                "agent": agent.agent_id,
                                "has_alternative": False,
                            },
                        )
                        memory.add(decision_msg)
                        if on_message:
                            on_message(decision_msg)
                        # Don't block — continue voting

                if self._is_approved(result.artifact):
                    approvals += 1

        # ── Tally ────────────────────────────────────────────────
        threshold_met = approvals / len(voters) > self.approval_threshold
        outcome = "approved" if threshold_met else "blocked"
        signal = "[APPROVED]" if threshold_met else "[BLOCKED]"

        decision_msg = Message(
            type=MessageType.DECISION,
            sender="decision_gate",
            content=f"{signal} Final tally: {approvals}/{len(voters)} approved "
            f"(threshold: >{self.approval_threshold:.0%}).",
            metadata={
                "outcome": outcome,
                "approvals": approvals,
                "blocks": blocks,
                "total_voters": len(voters),
                "threshold": self.approval_threshold,
            },
        )
        memory.add(decision_msg)
        if on_message:
            on_message(decision_msg)

        return all_results

    # ── terminal signals ─────────────────────────────────────────

    @staticmethod
    def _is_approved(text: str) -> bool:
        return bool(re.search(r"\[APPROVED\]", text, re.IGNORECASE))

    @staticmethod
    def _is_blocked(text: str) -> bool:
        return bool(re.search(r"\[BLOCKED\]", text, re.IGNORECASE))

    @staticmethod
    def _has_alternative(text: str) -> bool:
        """Check if a BLOCKED vote includes an alternative proposal."""
        alt_patterns = [
            r"(?:alternative|instead|suggest|propose|recommend|rather)[:\s]",
            r"(?:替代|建议|方案|改为|应该)",
            r"option\s+[a-z]",
        ]
        for pat in alt_patterns:
            if re.search(pat, text, re.IGNORECASE):
                return True
        return False

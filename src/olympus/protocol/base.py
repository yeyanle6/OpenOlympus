"""Protocol ABC — the core abstraction for collaboration strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from olympus.types import Message, AgentResult, OnMessage
from olympus.agent.llm_agent import LLMAgent
from olympus.room.pause_gate import PauseGate


class Protocol(ABC):
    """Base class for all collaboration protocols."""

    @abstractmethod
    async def run(
        self,
        agents: list[LLMAgent],
        task: str,
        context: list[Message] | None = None,
        *,
        gate: PauseGate | None = None,
        on_message: OnMessage = None,
    ) -> list[AgentResult]:
        ...

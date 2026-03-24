"""Room — execution lifecycle container for protocol runs."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from olympus.types import (
    Message,
    MessageType,
    RoomConfig,
    RoomStatus,
    AgentResult,
    OnMessage,
    OnStatus,
)
from olympus.room.pause_gate import PauseGate, RoomCancelled
from olympus.protocol.base import Protocol
from olympus.agent.llm_agent import LLMAgent

logger = logging.getLogger(__name__)

COST_PER_MILLION_TOKENS = 3.0


class Room:
    """Manages a single protocol execution with lifecycle controls."""

    def __init__(
        self,
        protocol: Protocol,
        agents: list[LLMAgent],
        task: str,
        config: RoomConfig | None = None,
        gate: PauseGate | None = None,
        on_message: OnMessage = None,
        on_status: OnStatus = None,
    ):
        self.room_id = uuid.uuid4().hex[:12]
        self.protocol = protocol
        self.agents = agents
        self.task = task
        self.config = config or RoomConfig()
        self.gate = gate or PauseGate()
        self._on_message = on_message
        self._on_status = on_status
        self._status = RoomStatus.CREATED
        self._results: list[AgentResult] = []
        self._start_time: float = 0
        self._elapsed_ms: int = 0

    @property
    def status(self) -> RoomStatus:
        return self._status

    @property
    def results(self) -> list[AgentResult]:
        return list(self._results)

    @property
    def elapsed_ms(self) -> int:
        return self._elapsed_ms

    async def run(self) -> list[AgentResult]:
        """Execute the protocol with retry, timeout, and budget enforcement."""
        self._set_status(RoomStatus.RUNNING)
        self._start_time = time.monotonic()

        last_error: Exception | None = None
        attempts = self.config.max_retries + 1

        for attempt in range(attempts):
            try:
                self._results = await asyncio.wait_for(
                    self.protocol.run(
                        agents=self.agents,
                        task=self.task,
                        gate=self.gate,
                        on_message=self._on_message,
                    ),
                    timeout=self.config.timeout_seconds,
                )
                self._elapsed_ms = int((time.monotonic() - self._start_time) * 1000)

                # Budget check
                if self._is_over_budget():
                    self._set_status(RoomStatus.BUDGET_EXCEEDED)
                    return self._results

                self._set_status(RoomStatus.COMPLETED)
                return self._results

            except asyncio.TimeoutError:
                last_error = asyncio.TimeoutError(
                    f"Room timed out after {self.config.timeout_seconds}s"
                )
                if attempt < attempts - 1:
                    logger.warning(
                        "Room %s timed out (attempt %d/%d), retrying",
                        self.room_id, attempt + 1, attempts,
                    )
                    continue

            except RoomCancelled:
                self._elapsed_ms = int((time.monotonic() - self._start_time) * 1000)
                self._set_status(RoomStatus.CANCELLED)
                return self._results

            except Exception as e:
                last_error = e
                if attempt < attempts - 1:
                    logger.warning(
                        "Room %s failed (attempt %d/%d): %s, retrying",
                        self.room_id, attempt + 1, attempts, e,
                    )
                    continue

        # All attempts exhausted
        self._elapsed_ms = int((time.monotonic() - self._start_time) * 1000)
        if isinstance(last_error, asyncio.TimeoutError):
            self._set_status(RoomStatus.TIMEOUT)
        else:
            self._set_status(RoomStatus.FAILED)
        return self._results

    def _is_over_budget(self) -> bool:
        total_tokens = sum(r.tokens_used for r in self._results)
        cost = (total_tokens / 1_000_000) * COST_PER_MILLION_TOKENS
        return cost > self.config.max_budget_usd

    def _set_status(self, status: RoomStatus) -> None:
        self._status = status
        if self._on_status:
            self._on_status(self.room_id, status)

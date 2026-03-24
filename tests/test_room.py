"""Tests for Room lifecycle and PauseGate."""

import asyncio

import pytest

from olympus.types import Message, MessageType, AgentResult, RoomConfig, RoomStatus
from olympus.room.pause_gate import PauseGate, RoomCancelled
from olympus.room.room import Room
from olympus.protocol.base import Protocol
from olympus.agent.llm_agent import LLMAgent


# ── Mock protocol for testing ────────────────────────────────

class MockProtocol(Protocol):
    def __init__(self, results=None, delay=0, raise_exc=None):
        self._results = results or [AgentResult(status="success", artifact="done")]
        self._delay = delay
        self._raise_exc = raise_exc

    async def run(self, agents, task, context=None, *, gate=None, on_message=None):
        if gate:
            await gate.checkpoint()
        if self._delay:
            # Sleep in small increments to allow cancellation
            end = asyncio.get_event_loop().time() + self._delay
            while asyncio.get_event_loop().time() < end:
                await asyncio.sleep(0.02)
                if gate:
                    await gate.checkpoint()
        if self._raise_exc:
            raise self._raise_exc
        if on_message:
            on_message(Message(type=MessageType.ARTIFACT, sender="mock", content="test"))
        return self._results


# ── PauseGate tests ───────────────────────────────────────────

async def test_gate_checkpoint_passes_normally():
    gate = PauseGate()
    await gate.checkpoint()  # Should not block


async def test_gate_cancel_raises():
    gate = PauseGate()
    gate.cancel()
    with pytest.raises(RoomCancelled):
        await gate.checkpoint()


async def test_gate_pause_and_resume():
    gate = PauseGate()
    gate.pause()
    assert gate.is_paused

    # Resume in background
    async def resume_later():
        await asyncio.sleep(0.1)
        gate.resume()

    asyncio.create_task(resume_later())
    await gate.checkpoint()  # Should eventually pass
    assert not gate.is_paused


# ── Room tests ────────────────────────────────────────────────

async def test_room_completes_successfully():
    protocol = MockProtocol()
    room = Room(protocol=protocol, agents=[], task="test task")
    results = await room.run()

    assert room.status == RoomStatus.COMPLETED
    assert len(results) == 1
    assert results[0].status == "success"


async def test_room_timeout():
    protocol = MockProtocol(delay=5)
    config = RoomConfig(timeout_seconds=0.1, max_retries=0)
    room = Room(protocol=protocol, agents=[], task="slow", config=config)
    await room.run()

    assert room.status == RoomStatus.TIMEOUT


async def test_room_cancelled():
    gate = PauseGate()

    async def cancel_soon():
        await asyncio.sleep(0.05)
        gate.cancel()

    protocol = MockProtocol(delay=5)
    room = Room(protocol=protocol, agents=[], task="cancel me", gate=gate)

    asyncio.create_task(cancel_soon())
    await room.run()

    assert room.status == RoomStatus.CANCELLED


async def test_room_failed():
    protocol = MockProtocol(raise_exc=ValueError("boom"))
    config = RoomConfig(max_retries=0)
    room = Room(protocol=protocol, agents=[], task="fail", config=config)
    await room.run()

    assert room.status == RoomStatus.FAILED


async def test_room_on_message_callback():
    messages = []
    protocol = MockProtocol()
    room = Room(
        protocol=protocol, agents=[], task="test",
        on_message=lambda m: messages.append(m),
    )
    await room.run()

    assert len(messages) == 1
    assert messages[0].content == "test"

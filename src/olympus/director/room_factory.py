"""RoomFactory — creates and manages Room lifecycle, extracted from Director."""

from __future__ import annotations

import logging
from typing import Any

from olympus.types import Message, MessageType, RoomStatus
from olympus.agent.pool import AgentPool
from olympus.agent.loader import AgentLoader
from olympus.director.types import DirectorAction, ManagedRoom
from olympus.events.bus import EventBus
from olympus.events.types import Event
from olympus.memory.references import ReferenceExtractor
from olympus.protocol.base import Protocol
from olympus.protocol.delegate import DelegateProtocol
from olympus.protocol.roundtable import RoundtableProtocol
from olympus.protocol.peer_review import PeerReviewProtocol
from olympus.protocol.pipeline import PipelineProtocol
from olympus.protocol.parallel_gather import ParallelGatherProtocol
from olympus.protocol.standup import StandupProtocol
from olympus.protocol.review_meeting import ReviewMeetingProtocol
from olympus.protocol.decision_gate import DecisionGateProtocol
from olympus.room.room import Room
from olympus.room.pause_gate import PauseGate

logger = logging.getLogger(__name__)


class RoomFactory:
    """Creates Room instances and wires up callbacks."""

    def __init__(self, loader: AgentLoader, pool: AgentPool, bus: EventBus):
        self.loader = loader
        self.pool = pool
        self._bus = bus

    def create_room(
        self,
        action: DirectorAction,
        room_messages: dict[str, list[dict[str, str]]],
        room_refs: dict[str, ReferenceExtractor],
    ) -> tuple[Room, ManagedRoom, PauseGate, list[str]] | None:
        """Validate and create a Room. Returns (room, managed, gate, agent_ids) or None."""
        valid_ids = set(self.loader.list_ids())
        agents_ids = [a for a in action.agents if a in valid_ids]
        if not agents_ids:
            return None

        agents = self.pool.get_agents(agents_ids)
        protocol = self.get_protocol(action.protocol)
        gate = PauseGate()

        def on_message(msg: Message) -> None:
            # Validate message quality
            from olympus.agent.validator import validate_message
            prev = [m["content"] for m in room_messages.get(room.room_id, [])]
            vr = validate_message(msg.content, prev)
            if not vr.valid:
                return

            msg_data = {
                "sender": msg.sender,
                "content": content,
                "type": msg.type.value,
                "id": msg.id,
                "timestamp": msg.timestamp,
                "metadata": msg.metadata,
            }
            if room.room_id not in room_messages:
                room_messages[room.room_id] = []
            room_messages[room.room_id].append(msg_data)
            # Extract references
            if room.room_id not in room_refs:
                room_refs[room.room_id] = ReferenceExtractor()
            msg_idx = len(room_messages[room.room_id]) - 1
            room_refs[room.room_id].extract_from_message(
                msg.content, msg.sender, msg_idx
            )
            # Persist message
            import asyncio
            from olympus.memory.rooms_store import RoomsStore
            store = RoomsStore()
            asyncio.ensure_future(store.save_message(room.room_id, msg_data))
            self._bus.publish_nowait(Event(
                type="room_message",
                room_id=room.room_id,
                data=msg_data,
            ))

        def on_status(room_id: str, status: RoomStatus) -> None:
            self._bus.publish_nowait(Event(
                type="room_status",
                room_id=room_id,
                data={"status": status.value},
            ))

        room = Room(
            protocol=protocol,
            agents=agents,
            task=action.task,
            gate=gate,
            on_message=on_message,
            on_status=on_status,
        )

        managed = ManagedRoom(
            room_id=room.room_id,
            task=action.task,
            protocol=action.protocol,
            agent_ids=agents_ids,
        )

        return room, managed, gate, agents_ids

    @staticmethod
    def get_protocol(name: str) -> Protocol:
        protocols: dict[str, Protocol] = {
            "delegate": DelegateProtocol(),
            "roundtable": RoundtableProtocol(),
            "peer_review": PeerReviewProtocol(),
            "pipeline": PipelineProtocol(),
            "parallel": ParallelGatherProtocol(),
            "standup": StandupProtocol(),
            "review_meeting": ReviewMeetingProtocol(),
            "decision_gate": DecisionGateProtocol(),
        }
        return protocols.get(name, DelegateProtocol())

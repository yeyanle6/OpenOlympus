"""Director — LLM-powered intent parser and room orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from typing import Any

from olympus.types import Message, MessageType, RoomConfig, RoomStatus
from olympus.agent.loader import AgentLoader
from olympus.agent.pool import AgentPool
from olympus.director.types import DirectorAction, ManagedRoom
from olympus.events.bus import EventBus
from olympus.events.types import Event
from olympus.memory.consensus import ConsensusMemory
from olympus.memory.references import ReferenceExtractor
from olympus.memory.rooms_store import RoomsStore
from olympus.director.room_aliases import resolve_alias, get_aliases_prompt
from olympus.memory.wbs import TaskBreakdown, WBSNode, TaskStatus
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

_MAX_THEN_DEPTH = 1

_INTENT_SYSTEM_PROMPT = """\
You are a Director that interprets user messages and dispatches them to AI agent teams.
You MUST always respond with valid JSON. When the user asks you to do something, \
you should ALWAYS create a room (action: "spawn_room") unless they explicitly ask for status or to control a room.

Available agents (by layer):
- Orchestration: coordinator, tracker
- Planning: planner, auditor, critic
- Worker: builder, worker
- Specialist: architect, researcher, explorer, reviewer, tester

Available protocols:
- delegate: Single agent handles the task (default for simple tasks)
- roundtable: All selected agents discuss and converge
- peer_review: Author + reviewer iterate until approved
- pipeline: Sequential handoff chain A -> B -> C
- parallel: Fan-out to all agents, gather results
- standup: Single-round status updates from each participant
- review_meeting: Presenter + reviewers iterate until [APPROVED] or [BLOCKED]
- decision_gate: Go/no-go vote with veto power ([APPROVED]/[BLOCKED])

Active rooms: {rooms_status}

Rules:
- If the user mentions a specific agent or protocol, use that
- If unsure, default to delegate with the most relevant agent
- For planning/analysis tasks: use planner or architect
- For coding/building tasks: use builder
- For research tasks: use researcher
- For review tasks: use peer_review with builder + reviewer
- ALWAYS use "spawn_room" action for new tasks. Only use "reply" for greetings or questions about the system itself.

Respond ONLY with JSON (no markdown, no explanation):
{{
  "action": "spawn_room",
  "protocol": "<protocol name>",
  "agents": ["<agent_id>", ...],
  "task": "<detailed task description in the user's language>",
  "reply": "<brief description of what you're doing>"
}}
"""


class Director:
    """Interprets user intent and manages rooms."""

    def __init__(
        self,
        loader: AgentLoader,
        pool: AgentPool,
        consensus: ConsensusMemory | None = None,
    ):
        self.loader = loader
        self.pool = pool
        self.consensus = consensus or ConsensusMemory()
        self._rooms: dict[str, ManagedRoom] = {}
        self._room_tasks: dict[str, asyncio.Task] = {}
        self._gates: dict[str, PauseGate] = {}
        self._bus = EventBus.get()
        self._conversation: list[dict[str, str]] = []
        self._room_messages: dict[str, list[dict[str, str]]] = {}
        self._auto_followup = True
        self._followup_depth: dict[str, int] = {}  # room_id -> depth
        self._max_followup_depth = 4  # Deep via layered follow-ups, not repeated rounds
        self._max_concurrent_rooms = 1  # Only one room runs at a time
        self._parent_room: dict[str, str] = {}  # child_room_id -> parent_room_id
        self._room_refs: dict[str, ReferenceExtractor] = {}  # room_id -> extractor
        self._room_themes: dict[str, str] = {}  # room_id -> theme
        self._store = RoomsStore()
        self._wbs: dict[str, TaskBreakdown] = {}  # root_room_id -> task tree

    async def chat(self, user_message: str) -> dict[str, Any]:
        """Process a user message and return a response."""
        self._conversation.append({"role": "user", "content": user_message})

        # Parse intent via LLM
        action = await self._parse_intent(user_message)
        response = await self._execute_action(action)

        self._conversation.append({"role": "assistant", "content": response.get("reply", "")})
        return response

    async def get_rooms_status(self) -> list[dict[str, Any]]:
        return [
            {
                "room_id": r.room_id,
                "task": r.task,
                "protocol": r.protocol,
                "agents": r.agent_ids,
                "status": r.status.value,
                "parent_room": self._parent_room.get(r.room_id, ""),
                "depth": self._followup_depth.get(r.room_id, 0),
                "theme": self._room_themes.get(r.room_id, "strategy"),
            }
            for r in self._rooms.values()
        ]

    def get_room_messages(self, room_id: str) -> list[dict[str, str]]:
        return self._room_messages.get(room_id, [])

    def get_wbs(self, root_room_id: str) -> dict:
        wbs = self._wbs.get(root_room_id)
        if not wbs:
            return {"sprint_goal": "", "nodes": [], "completion_pct": 0}
        return {
            "sprint_goal": wbs.sprint_goal,
            "nodes": wbs.to_list(),
            "completion_pct": round(wbs.completion_pct() * 100, 1),
            "total_estimated": wbs.total_estimated_cycles(),
            "total_actual": wbs.total_actual_cycles(),
        }

    def get_room_references(self, room_id: str) -> dict:
        extractor = self._room_refs.get(room_id)
        if not extractor:
            return {"tree": {"name": "References", "children": []}, "by_type": {}, "agent_refs": {}, "total": 0, "stats": {}}
        return extractor.get_graph_data()

    async def _run_coordinator_review(
        self, parent_room_id: str, original_task: str, artifacts: list[str]
    ) -> None:
        """Coordinator reviews completed discussion and spawns follow-up rooms."""
        # Check depth limit
        depth = self._followup_depth.get(parent_room_id, 0)
        if depth >= self._max_followup_depth:
            logger.info("Follow-up depth limit reached for %s", parent_room_id)
            return

        coordinator = self.pool.get_agent("coordinator")
        if not coordinator:
            return

        # Build review prompt
        discussion_summary = "\n---\n".join(
            art[:3000] for art in artifacts[:3]
        )
        review_prompt = (
            f"## Review Task\n\n"
            f"A discussion just completed on: {original_task}\n\n"
            f"## Discussion Summary:\n\n{discussion_summary}\n\n"
            f"## Your Job\n\n"
            f"As Coordinator of a new R&D division, review this discussion proactively.\n"
            f"Deep preliminary research is valued over premature convergence.\n\n"
            f"Determine what follow-up discussions are needed. Group them by THEME:\n"
            f"- 'technical': algorithm, implementation, hardware, signal processing\n"
            f"- 'business': market, pricing, user research, competition\n"
            f"- 'compliance': regulatory, legal, privacy, ethics\n"
            f"- 'validation': testing, benchmarks, data collection, verification\n"
            f"- 'design': UX, product design, user experience\n\n"
            f"List up to 3 follow-ups. Each MUST include a 'theme' field.\n"
            f"Only say COMPLETE if ALL key questions are thoroughly addressed.\n\n"
            f"Respond ONLY with JSON:\n"
            f'{{"status": "needs_followup" | "complete", '
            f'"followups": [{{"task": "...", "agents": ["..."], "protocol": "...", "theme": "technical|business|compliance|validation|design"}}], '
            f'"summary": "..."}}\n'
        )

        result = await coordinator.execute(review_prompt)
        if result.status != "success" or not result.artifact.strip():
            return

        # Store coordinator's review as a message in the parent room
        if parent_room_id not in self._room_messages:
            self._room_messages[parent_room_id] = []
        self._room_messages[parent_room_id].append({
            "sender": "coordinator",
            "content": result.artifact,
            "type": "review",
        })
        self._bus.publish_nowait(Event(
            type="room_message",
            room_id=parent_room_id,
            data={"sender": "coordinator", "content": result.artifact, "type": "review"},
        ))

        # Try to parse follow-up instructions
        try:
            text = result.artifact
            data = self._try_extract_json(text)

            if data.get("status") == "needs_followup":
                followups = data.get("followups", [])
                for fu in followups[:3]:  # Max 3 follow-ups
                    fu_task = fu.get("task", "")
                    fu_agents = fu.get("agents", [])
                    fu_protocol = fu.get("protocol", "delegate")
                    fu_theme = fu.get("theme", "technical")
                    if fu_task and fu_agents:
                        logger.info(
                            "Coordinator spawning follow-up: %s (agents: %s)",
                            fu_task[:100], fu_agents,
                        )
                        fu_action = DirectorAction(
                            action="spawn_room",
                            protocol=fu_protocol,
                            agents=fu_agents,
                            task=fu_task,
                        )
                        fu_response = await self._spawn_room(fu_action)
                        fu_room_id = fu_response.get("room_id", "")
                        if fu_room_id:
                            self._followup_depth[fu_room_id] = depth + 1
                            self._parent_room[fu_room_id] = parent_room_id
                            self._room_themes[fu_room_id] = fu_theme

                            # WBS: track as subtask under root room
                            root_id = parent_room_id
                            while root_id in self._parent_room:
                                root_id = self._parent_room[root_id]
                            if root_id not in self._wbs:
                                self._wbs[root_id] = TaskBreakdown()
                            wbs_node = WBSNode(
                                id=fu_room_id[:8],
                                title=fu_task[:80],
                                parent_id=parent_room_id[:8] if depth > 0 else "",
                                assignee=", ".join(fu_agents),
                                status=TaskStatus.IN_PROGRESS,
                            )
                            self._wbs[root_id].add(wbs_node)
                            # Re-save meta with correct parent info and theme
                            await self._store.save_room_meta(fu_room_id, {
                                "room_id": fu_room_id,
                                "task": fu_task,
                                "protocol": fu_protocol,
                                "agents": fu_agents,
                                "status": "running",
                                "parent_room": parent_room_id,
                                "depth": depth + 1,
                                "theme": fu_theme,
                            })
            else:
                logger.info("Coordinator says discussion is complete")

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Could not parse coordinator review as JSON: %s", e)

    async def restore_from_disk(self) -> int:
        """Restore completed rooms from disk on startup. Returns count restored."""
        room_metas = await self._store.load_all_rooms()
        count = 0
        for meta in room_metas:
            rid = meta["room_id"]
            if rid in self._rooms:
                continue
            # Restore messages
            msgs = await self._store.load_messages(rid)
            if msgs:
                self._room_messages[rid] = msgs
            # Restore metadata as a ManagedRoom
            # Force running→completed on restore (no live process exists)
            saved_status = meta.get("status", "completed")
            if saved_status in ("running", "created"):
                saved_status = "completed"
            managed = ManagedRoom(
                room_id=rid,
                task=meta.get("task", ""),
                protocol=meta.get("protocol", ""),
                agent_ids=meta.get("agents", []),
                status=RoomStatus(saved_status),
            )
            self._rooms[rid] = managed
            if meta.get("parent_room"):
                self._parent_room[rid] = meta["parent_room"]
            if meta.get("depth"):
                self._followup_depth[rid] = meta["depth"]
            if meta.get("theme"):
                self._room_themes[rid] = meta["theme"]
            # Restore references by re-extracting from messages
            if msgs:
                extractor = ReferenceExtractor()
                for i, msg in enumerate(msgs):
                    extractor.extract_from_message(msg.get("content", ""), msg.get("sender", ""), i)
                self._room_refs[rid] = extractor
            count += 1
        return count

    async def shutdown(self) -> None:
        for task in self._room_tasks.values():
            task.cancel()
        await asyncio.gather(*self._room_tasks.values(), return_exceptions=True)

    async def _parse_intent(self, message: str) -> DirectorAction:
        # Only show running rooms to keep prompt concise
        all_rooms = await self.get_rooms_status()
        active = [r for r in all_rooms if r["status"] in ("running", "created")]
        rooms_status = json.dumps(active[:5], ensure_ascii=False) if active else "[]"
        prompt = _INTENT_SYSTEM_PROMPT.format(rooms_status=rooms_status)

        # Add recent conversation context
        recent = self._conversation[-10:]
        context = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
        full_prompt = f"{prompt}\n\nConversation:\n{context}"

        try:
            result = await asyncio.to_thread(self._call_claude, full_prompt)
            text = self._extract_text(result)
            data = self._try_extract_json(text)
            return self._parse_action(data)
        except Exception as e:
            logger.warning("Intent parse failed: %s, falling back to reply", e)
            return DirectorAction(action="reply", reply=str(e))

    async def _execute_action(
        self, action: DirectorAction, depth: int = 0
    ) -> dict[str, Any]:
        response: dict[str, Any] = {}

        if action.action == "spawn_room":
            response = await self._spawn_room(action)
        elif action.action == "pause_room" and action.room_id:
            gate = self._gates.get(action.room_id)
            if gate:
                gate.pause()
                managed = self._rooms.get(action.room_id)
                if managed:
                    managed.status = RoomStatus.PAUSED
                response = {"reply": f"Room {action.room_id} paused"}
            else:
                response = {"reply": f"Room {action.room_id} not found"}
        elif action.action == "resume_room" and action.room_id:
            gate = self._gates.get(action.room_id)
            if gate:
                gate.resume()
                managed = self._rooms.get(action.room_id)
                if managed:
                    managed.status = RoomStatus.RUNNING
                response = {"reply": f"Room {action.room_id} resumed"}
            else:
                response = {"reply": f"Room {action.room_id} not found"}
        elif action.action == "stop_room" and action.room_id:
            gate = self._gates.get(action.room_id)
            if gate:
                gate.cancel()
                response = {"reply": f"Room {action.room_id} cancelled"}
            else:
                response = {"reply": f"Room {action.room_id} not found"}
        elif action.action == "status":
            rooms = await self.get_rooms_status()
            response = {"reply": f"Active rooms: {len(rooms)}", "rooms": rooms}
        else:
            response = {"reply": action.reply or "OK"}

        # Handle chained action
        if action.then and depth < _MAX_THEN_DEPTH:
            then_response = await self._execute_action(action.then, depth + 1)
            response["then_result"] = then_response

        return response

    def _count_running_rooms(self) -> int:
        return sum(1 for r in self._rooms.values() if r.status == RoomStatus.RUNNING)

    async def _spawn_room(self, action: DirectorAction) -> dict[str, Any]:
        # Check concurrent room limit
        running = self._count_running_rooms()
        if running >= self._max_concurrent_rooms:
            logger.warning("Room limit reached (%d/%d), rejecting spawn", running, self._max_concurrent_rooms)
            return {"reply": f"Cannot start new room: {running} already running (max {self._max_concurrent_rooms}). Wait for current room to finish."}

        # Validate agent IDs
        valid_ids = set(self.loader.list_ids())
        agents_ids = [a for a in action.agents if a in valid_ids]
        if not agents_ids:
            return {"reply": "No valid agents specified"}

        agents = self.pool.get_agents(agents_ids)
        protocol = self._get_protocol(action.protocol)
        gate = PauseGate()

        def on_message(msg: Message) -> None:
            msg_data = {"sender": msg.sender, "content": msg.content, "type": msg.type.value}
            # Store message for REST API retrieval
            if room.room_id not in self._room_messages:
                self._room_messages[room.room_id] = []
            self._room_messages[room.room_id].append(msg_data)
            # Extract references
            if room.room_id not in self._room_refs:
                self._room_refs[room.room_id] = ReferenceExtractor()
            msg_idx = len(self._room_messages[room.room_id]) - 1
            self._room_refs[room.room_id].extract_from_message(
                msg.content, msg.sender, msg_idx
            )
            # Persist message to disk
            asyncio.ensure_future(self._store.save_message(room.room_id, msg_data))
            self._bus.publish_nowait(Event(
                type="room_message",
                room_id=room.room_id,
                data=msg_data,
            ))

        def on_status(room_id: str, status: RoomStatus) -> None:
            managed = self._rooms.get(room_id)
            if managed:
                managed.status = status
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
        self._rooms[room.room_id] = managed
        self._gates[room.room_id] = gate

        # Persist room metadata
        asyncio.ensure_future(self._store.save_room_meta(room.room_id, {
            "room_id": room.room_id,
            "task": action.task,
            "protocol": action.protocol,
            "agents": agents_ids,
            "status": "running",
            "parent_room": self._parent_room.get(room.room_id, ""),
            "depth": self._followup_depth.get(room.room_id, 0),
        }))

        async def run_room() -> None:
          try:
            results = await room.run()
            managed.result = results
            # Update WBS node status
            for root_id, wbs in self._wbs.items():
                node = wbs.get(room.room_id[:8])
                if node:
                    node.status = (
                        TaskStatus.DONE if room.status == RoomStatus.COMPLETED
                        else TaskStatus.BLOCKED
                    )
                    break

            # Persist final status
            await self._store.save_room_meta(room.room_id, {
                "room_id": room.room_id,
                "task": action.task,
                "protocol": action.protocol,
                "agents": agents_ids,
                "status": room.status.value,
                "parent_room": self._parent_room.get(room.room_id, ""),
                "depth": self._followup_depth.get(room.room_id, 0),
            })
            # Persist references
            if room.room_id in self._room_refs:
                await self._store.save_references(
                    room.room_id, self._room_refs[room.room_id].get_graph_data()
                )
            # Save clean text summary to consensus on completion
            if room.status == RoomStatus.COMPLETED and results:
                artifacts = [
                    r.artifact for r in results
                    if r.status == "success" and r.artifact.strip()
                ]
                if artifacts:
                    trimmed = []
                    for art in artifacts[:3]:
                        trimmed.append(art[:5000] if len(art) > 5000 else art)
                    summary = (
                        f"\n\n## Room {room.room_id} Result\n"
                        f"**Task**: {action.task[:200]}\n"
                        f"**Protocol**: {action.protocol}\n"
                        f"**Agents**: {', '.join(agents_ids)}\n\n"
                        + "\n---\n".join(trimmed)
                    )
                    current = await self.consensus.read()
                    await self.consensus.write(current + summary)

                    # Feed results back to parent room if this is a follow-up
                    parent_id = self._parent_room.get(room.room_id)
                    if parent_id and parent_id in self._room_messages:
                        # Structured backflow with source, topic, priority
                        theme = self._room_themes.get(room.room_id, "")
                        summary_text = "\n".join(art[:2000] for art in artifacts[:2])
                        backflow_msg = {
                            "sender": "coordinator",
                            "content": (
                                f"## Sub-Room Result: {room.room_id[:8]}\n"
                                f"**Source**: {', '.join(agents_ids)}\n"
                                f"**Protocol**: {action.protocol}\n"
                                f"**Theme**: {theme or 'general'}\n"
                                f"**Task**: {action.task[:200]}\n\n"
                                f"### Key Findings\n\n{summary_text}"
                            ),
                            "type": "backflow",
                            "source_room": room.room_id,
                            "theme": theme,
                            "agents": agents_ids,
                        }
                        self._room_messages[parent_id].append(backflow_msg)
                        self._bus.publish_nowait(Event(
                            type="room_message",
                            room_id=parent_id,
                            data=backflow_msg,
                        ))

                    # Auto follow-up: Coordinator reviews and proposes next steps
                    if self._auto_followup:
                        try:
                            logger.info("Starting coordinator review for room %s", room.room_id)
                            await self._run_coordinator_review(
                                room.room_id, action.task, artifacts
                            )
                        except Exception as e:
                            logger.error("Coordinator review failed for room %s: %s", room.room_id, e)

          except Exception as e:
            logger.error("run_room CRASHED for %s: %s", room.room_id, e, exc_info=True)
            managed.status = RoomStatus.FAILED

        task = asyncio.create_task(run_room())
        self._room_tasks[room.room_id] = task

        return {
            "reply": f"Room {room.room_id} started: {action.task}",
            "room_id": room.room_id,
            "protocol": action.protocol,
            "agents": agents_ids,
        }

    @staticmethod
    def _get_protocol(name: str) -> Protocol:
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

    def _parse_action(self, data: dict) -> DirectorAction:
        then_data = data.get("then")
        then_action = self._parse_action(then_data) if then_data and isinstance(then_data, dict) else None
        return DirectorAction(
            action=data.get("action", "reply"),
            protocol=data.get("protocol", "delegate"),
            agents=data.get("agents", []),
            task=data.get("task", ""),
            reply=data.get("reply", ""),
            room_id=data.get("room_id", ""),
            then=then_action,
        )

    @staticmethod
    def _call_claude(prompt: str, timeout: int = 180) -> dict:
        proc = subprocess.run(
            [
                "claude", "-p", prompt,
                "--output-format", "json",
                "--max-turns", "1",
                "--model", "sonnet",
            ],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Claude CLI error: {proc.stderr[:500]}")
        return json.loads(proc.stdout)

    @staticmethod
    def _extract_text(result: dict) -> str:
        for key in ("result", "text", "content"):
            if key in result:
                val = result[key]
                if isinstance(val, str):
                    return val
                if isinstance(val, list):
                    return "\n".join(
                        item.get("text", "") for item in val
                        if isinstance(item, dict) and item.get("type") == "text"
                    )
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def _try_extract_json(text: str) -> dict:
        # Try parsing directly
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # Try extracting from markdown code block
        match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        # Try finding any JSON object in the text (greedy match for largest block)
        # Look for { ... } patterns that span multiple lines
        for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL):
            try:
                candidate = m.group()
                parsed = json.loads(candidate)
                # Must have expected keys
                if "status" in parsed or "action" in parsed:
                    return parsed
            except (json.JSONDecodeError, ValueError):
                continue

        # Last resort: find the largest { ... } block
        start = text.find('{')
        if start >= 0:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except (json.JSONDecodeError, ValueError):
                            break

        raise ValueError(f"Could not extract JSON from response: {text[:200]}")

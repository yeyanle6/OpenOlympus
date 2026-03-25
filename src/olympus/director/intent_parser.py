"""IntentParser — LLM-powered intent parsing extracted from Director."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from typing import Any

from olympus.director.types import DirectorAction

logger = logging.getLogger(__name__)

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


class IntentParser:
    """Parses user messages into DirectorActions via LLM."""

    def __init__(self) -> None:
        self._conversation: list[dict[str, str]] = []

    @property
    def conversation(self) -> list[dict[str, str]]:
        return self._conversation

    def add_message(self, role: str, content: str) -> None:
        self._conversation.append({"role": role, "content": content})

    async def parse(self, message: str, rooms_status: str) -> DirectorAction:
        self.add_message("user", message)
        prompt = _INTENT_SYSTEM_PROMPT.format(rooms_status=rooms_status)

        recent = self._conversation[-10:]
        context = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
        full_prompt = f"{prompt}\n\nConversation:\n{context}"

        try:
            result = await asyncio.to_thread(self._call_claude, full_prompt)
            text = self._extract_text(result)
            data = self._try_extract_json(text)
            action = self._parse_action(data)
            self.add_message("assistant", action.reply or action.task[:100])
            return action
        except Exception as e:
            logger.warning("Intent parse failed: %s, falling back to reply", e)
            return DirectorAction(action="reply", reply=str(e))

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
                if isinstance(val, str) and val.strip():
                    return val
                if isinstance(val, list):
                    return "\n".join(
                        item.get("text", "") for item in val
                        if isinstance(item, dict) and item.get("type") == "text"
                    )
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def _parse_action(data: dict) -> DirectorAction:
        then_data = data.get("then")
        then_action = IntentParser._parse_action(then_data) if then_data and isinstance(then_data, dict) else None
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
    def _try_extract_json(text: str) -> dict:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL):
            try:
                candidate = m.group()
                parsed = json.loads(candidate)
                if "status" in parsed or "action" in parsed:
                    return parsed
            except (json.JSONDecodeError, ValueError):
                continue

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

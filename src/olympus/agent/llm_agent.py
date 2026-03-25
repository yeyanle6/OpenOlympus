"""LLM agent that wraps Claude CLI as a subprocess."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

from olympus.types import Message, AgentResult
from olympus.agent.definition import AgentDefinition
from olympus.agent.speaker import SpeakerLock
from olympus.agent.evolution import EvolutionEngine
from olympus.agent.mock import MOCK_ENABLED, mock_response
from olympus.memory.session import SessionMemory

# Global evolution engine singleton
_evolution: EvolutionEngine | None = None


def get_evolution_engine() -> EvolutionEngine:
    global _evolution
    if _evolution is None:
        _evolution = EvolutionEngine()
    return _evolution


class LLMAgent:
    """Wraps a single agent definition and executes tasks via Claude CLI."""

    def __init__(self, definition: AgentDefinition):
        self.definition = definition
        self.agent_id = definition.agent_id

    async def execute(
        self,
        task: str,
        context: list[Message] | None = None,
        room_id: str = "",
        use_tools: bool | None = None,
    ) -> AgentResult:
        # Mock mode: return deterministic response without Claude CLI
        if MOCK_ENABLED:
            return mock_response(self.definition, task)

        prompt = self._build_prompt(task, context)
        start = time.monotonic()
        speaker = SpeakerLock.get()

        try:
            # Only one agent speaks (calls API) at a time globally
            ctx = speaker.speak(self.agent_id, room_id)
            async with await ctx:
                result = await asyncio.to_thread(self._call_claude, prompt, 600, use_tools)
            duration_ms = int((time.monotonic() - start) * 1000)

            text = self._extract_text(result)
            usage = result.get("usage", {})
            tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            cost = result.get("total_cost_usd", result.get("cost_usd", 0.0))

            # Treat error_max_turns as success if we got text output
            subtype = result.get("subtype", "")
            status = "success" if text.strip() else "failed"

            # Self-evolution: detect and fulfill tool requests
            if text:
                evo = get_evolution_engine()
                requests = evo.extract_tool_requests(text, self.agent_id, room_id)
                for req in requests:
                    logger.info("Agent %s requested new tool: %s", self.agent_id, req.name)
                    tool = await evo.fulfill_request(req)
                    if tool:
                        logger.info("Tool %s created successfully", tool.name)

            return AgentResult(
                status=status,
                artifact=text,
                tokens_used=tokens,
                cost_usd=cost,
                agent_id=self.agent_id,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                status="timeout",
                error="Claude CLI timed out",
                agent_id=self.agent_id,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception as e:
            return AgentResult(
                status="failed",
                error=str(e),
                agent_id=self.agent_id,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    def _build_prompt(self, task: str, context: list[Message] | None) -> str:
        parts: list[str] = []

        # Persona and principles
        if self.definition.persona:
            parts.append(self.definition.persona)
        if self.definition.principles:
            parts.append(f"## Core Principles\n{self.definition.principles}")
        if self.definition.framework:
            parts.append(f"## Decision Framework\n{self.definition.framework}")

        # Permission constraints
        perms = self.definition.effective_permissions
        if not perms.write:
            parts.append("CONSTRAINT: You are READ-ONLY. Do not write or modify any files.")
        if not perms.execute:
            parts.append("CONSTRAINT: You cannot execute shell commands.")

        # Context from prior messages
        if context:
            parts.append("## Prior Context")
            for msg in context[-10:]:  # Last 10 messages
                parts.append(f"[{msg.sender}]: {msg.content}")

        # Current task
        parts.append(f"## Task\n{task}")

        # Output format
        if self.definition.output_format:
            parts.append(f"## Expected Output Format\n{self.definition.output_format}")

        # Worker agents: remind to finish with text summary
        perms = self.definition.effective_permissions
        if perms.write and perms.execute:
            parts.append(
                "IMPORTANT: After completing your work, output a text summary "
                "of what you did and the key results."
            )

        # Show available custom tools and self-evolution capability
        evo = get_evolution_engine()
        tools_section = evo.get_tools_prompt_section()
        if tools_section:
            parts.append(tools_section)

        return "\n\n".join(parts)

    # Per-role tool assignments
    ROLE_TOOLS: dict[str, tuple[str, int]] = {
        # (tools_spec, max_turns)
        # Worker layer: full access
        "builder":     ("default", 20),
        "worker":      ("default", 20),
        # Specialist layer: role-specific tools
        "researcher":  ("Read,Grep,Glob,WebSearch,WebFetch", 8),
        "explorer":    ("Read,Grep,Glob", 5),
        "reviewer":    ("Read,Grep,Glob", 5),
        "tester":      ("Read,Grep,Glob,Bash", 8),
        "architect":   ("Read,Grep,Glob", 5),
        # Planning layer: no tools (pure discussion)
        "planner":     ("", 1),
        "auditor":     ("", 1),
        "critic":      ("", 1),
        # Orchestration layer: read-only
        "coordinator": ("Read,Grep,Glob", 3),
        "tracker":     ("Read,Grep,Glob", 3),
    }

    def _call_claude(self, prompt: str, timeout: int = 600, use_tools: bool | None = None) -> dict[str, Any]:
        perms = self.definition.effective_permissions
        is_worker = perms.write and perms.execute

        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "json",
        ]

        if use_tools is False:
            # Protocol explicitly requests no tools (discussion mode)
            cmd.extend(["--max-turns", "2"])
            cmd.extend(["--model", "sonnet"])
            cmd.extend(["--tools", ""])
        elif use_tools is True or is_worker:
            # Protocol explicitly requests tools, or agent is a worker
            tools_spec, max_turns = self.ROLE_TOOLS.get(self.agent_id, ("default", 10))
            cmd.extend(["--max-turns", str(max_turns)])
            cmd.extend(["--tools", tools_spec])
            if is_worker:
                cmd.extend(["--permission-mode", "bypassPermissions"])
        else:
            # Default: use role-specific tools
            tools_spec, max_turns = self.ROLE_TOOLS.get(self.agent_id, ("", 1))
            cmd.extend(["--max-turns", str(max_turns)])
            if tools_spec:
                cmd.extend(["--tools", tools_spec])
                cmd.extend(["--model", "sonnet"])
            else:
                cmd.extend(["--tools", ""])
                cmd.extend(["--model", "sonnet"])

        # Phase 0 instrumentation: measure popen vs total latency
        t_start = time.monotonic()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        t_total = time.monotonic() - t_start

        cmd_debug = [c for c in cmd if c != prompt]
        logger.info(
            "Claude CLI: %s | prompt=%d chars | total=%.1fs",
            cmd_debug, len(prompt), t_total,
        )

        if proc.returncode != 0:
            raise RuntimeError(
                f"Claude CLI exited with code {proc.returncode}: {proc.stderr[:500]}"
            )

        parsed = json.loads(proc.stdout)
        logger.info("Claude CLI result: subtype=%s num_turns=%s result_len=%d",
                     parsed.get("subtype"), parsed.get("num_turns"), len(parsed.get("result", "")))
        return parsed

    @staticmethod
    def _extract_text(result: dict[str, Any]) -> str:
        """Extract clean text from Claude CLI JSON output.

        The Claude CLI returns JSON with a 'result' key containing the text.
        For error_max_turns, the 'result' may be empty but content may be
        available in other fields. We also need to handle the case where
        'result' is a stringified JSON blob (not useful text).
        """
        # Primary: 'result' field (Claude CLI standard output)
        text = result.get("result", "")
        if isinstance(text, str) and text.strip():
            return text

        # Fallback: 'text' or 'content' fields
        for key in ("text", "content"):
            if key in result:
                val = result[key]
                if isinstance(val, str) and val.strip():
                    return val
                if isinstance(val, list):
                    parts = [
                        item.get("text", "")
                        for item in val
                        if isinstance(item, dict) and item.get("type") == "text"
                    ]
                    joined = "\n".join(parts)
                    if joined.strip():
                        return joined

        # Last resort: if result was a JSON string, return it
        if isinstance(text, str) and text.strip():
            return text

        # Fallback: construct summary from what happened
        parts_out = []

        # Permission denials → show what was attempted
        denials = result.get("permission_denials", [])
        if denials:
            for d in denials:
                tool = d.get("tool_name", "")
                inp = d.get("tool_input", {})
                fp = inp.get("file_path", "")
                content = inp.get("content", "")
                if content:
                    parts_out.append(f"[{tool} → {fp}]\n{content[:2000]}")
                elif fp:
                    parts_out.append(f"[{tool} → {fp}]")

        if parts_out:
            return "\n---\n".join(parts_out)

        # No text, no denials — report what we know
        num_turns = result.get("num_turns", 0)
        subtype = result.get("subtype", "")
        if num_turns > 1:
            return (
                f"[Agent used {num_turns} tool calls but produced no text output. "
                f"Status: {subtype}]"
            )

        return ""

"""Self-evolution mechanism — agents can request and create new tools at runtime.

When an agent discovers it lacks a capability:
1. It signals a ToolRequest via a structured marker in its output
2. The EvolutionEngine detects the request
3. Builder agent creates the tool as a Python script in tools/
4. The tool is registered and becomes available to future calls

Tools are simple Python scripts with a standard interface:
    tools/<name>.py --help     → description
    tools/<name>.py <args>     → execute and print result
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolRequest:
    """A request from an agent to create a new tool."""
    name: str
    description: str
    requested_by: str  # agent_id
    room_id: str = ""
    use_case: str = ""  # why the agent needs it
    input_spec: str = ""  # what arguments it takes
    output_spec: str = ""  # what it should return
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class CustomTool:
    """A registered custom tool."""
    name: str
    description: str
    path: str  # path to the script
    created_by: str = ""
    created_at: str = ""
    usage_count: int = 0


class EvolutionEngine:
    """Manages tool creation and discovery for self-evolving agents."""

    def __init__(self, tools_dir: str | Path = "tools"):
        self.tools_dir = Path(tools_dir)
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self._registry: dict[str, CustomTool] = {}
        self._pending_requests: list[ToolRequest] = []
        self._load_existing_tools()

    def _load_existing_tools(self) -> None:
        """Discover tools already in the tools/ directory."""
        for path in self.tools_dir.glob("*.py"):
            if path.name.startswith("_"):
                continue
            name = path.stem
            # Try to get description from the script
            desc = self._get_tool_description(path)
            self._registry[name] = CustomTool(
                name=name,
                description=desc,
                path=str(path),
            )
        if self._registry:
            logger.info("Loaded %d existing tools: %s",
                        len(self._registry), list(self._registry.keys()))

    def _get_tool_description(self, path: Path) -> str:
        """Extract description from a tool script's docstring or --help."""
        try:
            content = path.read_text(encoding="utf-8")
            # Extract first docstring
            match = re.search(r'"""(.*?)"""', content, re.DOTALL)
            if match:
                return match.group(1).strip().split("\n")[0]
        except Exception:
            pass
        return f"Custom tool: {path.stem}"

    def list_tools(self) -> list[dict[str, Any]]:
        """List all registered custom tools."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "path": t.path,
                "created_by": t.created_by,
                "usage_count": t.usage_count,
            }
            for t in self._registry.values()
        ]

    def get_tool(self, name: str) -> CustomTool | None:
        return self._registry.get(name)

    def get_tools_prompt_section(self) -> str:
        """Generate a prompt section listing available custom tools."""
        if not self._registry:
            return ""
        lines = ["## Available Custom Tools"]
        for t in self._registry.values():
            lines.append(f"- `{t.name}`: {t.description}")
        lines.append(
            "\nTo use a custom tool, run: python tools/<name>.py <args>"
        )
        lines.append(
            "\nIf you need a tool that doesn't exist, include this marker in your response:\n"
            "[TOOL_REQUEST]\n"
            '{"name": "tool_name", "description": "what it does", '
            '"use_case": "why you need it", '
            '"input_spec": "what arguments", "output_spec": "what it returns"}\n'
            "[/TOOL_REQUEST]"
        )
        return "\n".join(lines)

    def extract_tool_requests(self, text: str, agent_id: str, room_id: str = "") -> list[ToolRequest]:
        """Extract tool requests from agent output text."""
        requests = []
        pattern = re.compile(
            r"\[TOOL_REQUEST\]\s*\n?(.*?)\n?\[/TOOL_REQUEST\]",
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            try:
                data = json.loads(match.group(1).strip())
                req = ToolRequest(
                    name=data.get("name", ""),
                    description=data.get("description", ""),
                    requested_by=agent_id,
                    room_id=room_id,
                    use_case=data.get("use_case", ""),
                    input_spec=data.get("input_spec", ""),
                    output_spec=data.get("output_spec", ""),
                )
                if req.name and req.description:
                    requests.append(req)
                    self._pending_requests.append(req)
                    logger.info("Tool request from %s: %s - %s",
                                agent_id, req.name, req.description)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to parse tool request: %s", e)
        return requests

    async def fulfill_request(self, request: ToolRequest) -> CustomTool | None:
        """Use Builder agent to create the requested tool."""
        build_prompt = (
            f"Create a Python CLI tool script at tools/{request.name}.py\n\n"
            f"Tool name: {request.name}\n"
            f"Description: {request.description}\n"
            f"Use case: {request.use_case}\n"
            f"Input: {request.input_spec}\n"
            f"Expected output: {request.output_spec}\n\n"
            f"Requirements:\n"
            f"- Must be a standalone Python script (no external dependencies beyond stdlib + requests)\n"
            f"- Must have a docstring as the first line describing what it does\n"
            f"- Must accept command-line arguments via argparse\n"
            f"- Must print results to stdout (JSON preferred)\n"
            f"- Must handle errors gracefully\n"
            f"- Must work with: python tools/{request.name}.py <args>\n"
        )

        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    "claude", "-p", build_prompt,
                    "--output-format", "json",
                    "--max-turns", "10",
                    "--permission-mode", "bypassPermissions",
                    "--tools", "Write,Read,Bash",
                ],
                capture_output=True, text=True, timeout=300,
            )

            # Check if file was created
            tool_path = self.tools_dir / f"{request.name}.py"
            if tool_path.exists():
                desc = self._get_tool_description(tool_path)
                tool = CustomTool(
                    name=request.name,
                    description=desc,
                    path=str(tool_path),
                    created_by=request.requested_by,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                self._registry[request.name] = tool
                self._save_registry()
                logger.info("Tool created: %s by %s", request.name, request.requested_by)
                return tool
            else:
                logger.warning("Tool creation failed: %s not found", tool_path)
                return None

        except Exception as e:
            logger.error("Tool creation error: %s", e)
            return None

    def run_tool(self, name: str, args: list[str] | None = None) -> str:
        """Execute a custom tool and return its output."""
        tool = self._registry.get(name)
        if not tool:
            return f"Error: tool '{name}' not found"

        cmd = ["python3", tool.path] + (args or [])
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            tool.usage_count += 1
            if proc.returncode != 0:
                return f"Error: {proc.stderr[:500]}"
            return proc.stdout
        except subprocess.TimeoutExpired:
            return "Error: tool execution timed out"
        except Exception as e:
            return f"Error: {e}"

    def get_pending_requests(self) -> list[ToolRequest]:
        return list(self._pending_requests)

    def clear_pending(self) -> None:
        self._pending_requests.clear()

    def _save_registry(self) -> None:
        """Save registry to disk for persistence."""
        reg_path = self.tools_dir / "_registry.json"
        data = {
            name: {
                "name": t.name,
                "description": t.description,
                "path": t.path,
                "created_by": t.created_by,
                "created_at": t.created_at,
                "usage_count": t.usage_count,
            }
            for name, t in self._registry.items()
        }
        reg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

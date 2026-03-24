"""Load agent definitions from YAML frontmatter + Markdown files."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from olympus.types import AgentLayer, AgentPermissions
from olympus.agent.definition import AgentDefinition


class AgentLoader:
    """Loads and caches agent definitions from a directory of Markdown files."""

    def __init__(self, agents_dir: str | Path = "agents"):
        self.agents_dir = Path(agents_dir)
        self._cache: dict[str, AgentDefinition] = {}
        self._mtimes: dict[str, float] = {}

    def load_all(self) -> dict[str, AgentDefinition]:
        """Load all agent definitions, using mtime-based cache invalidation."""
        for path in sorted(self.agents_dir.glob("*.md")):
            agent_id = path.stem
            mtime = path.stat().st_mtime
            if agent_id in self._mtimes and self._mtimes[agent_id] == mtime:
                continue
            defn = self._parse_file(path)
            if defn:
                self._cache[agent_id] = defn
                self._mtimes[agent_id] = mtime
        return dict(self._cache)

    def get(self, agent_id: str) -> AgentDefinition | None:
        if not self._cache:
            self.load_all()
        return self._cache.get(agent_id)

    def list_ids(self) -> list[str]:
        if not self._cache:
            self.load_all()
        return list(self._cache.keys())

    def _parse_file(self, path: Path) -> AgentDefinition | None:
        text = path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not fm_match:
            return None

        meta = yaml.safe_load(fm_match.group(1)) or {}
        body = text[fm_match.end():]
        agent_id = path.stem

        layer_str = meta.get("layer", "specialist")
        try:
            layer = AgentLayer(layer_str)
        except ValueError:
            layer = AgentLayer.SPECIALIST

        perms_data = meta.get("permissions")
        perms = None
        if perms_data and isinstance(perms_data, dict):
            perms = AgentPermissions(
                tools=perms_data.get("tools", []),
                write=perms_data.get("write", True),
                execute=perms_data.get("execute", True),
                spawn_rooms=perms_data.get("spawn_rooms", False),
            )

        return AgentDefinition(
            agent_id=agent_id,
            name=meta.get("name", agent_id),
            description=meta.get("description", ""),
            layer=layer,
            capabilities=meta.get("capabilities", []),
            permissions=perms,
            max_concurrent=meta.get("max_concurrent", 1),
            team=meta.get("team", ""),
            escalation_path=meta.get("escalation_path", []),
            collaboration_protocols=meta.get("collaboration_protocols", []),
            persona=self._extract_section(body, "Persona"),
            principles=self._extract_section(body, "Core Principles"),
            framework=self._extract_section(body, "Decision Framework"),
            output_format=self._extract_section(body, "Output Format"),
            raw_body=body.strip(),
        )

    @staticmethod
    def _extract_section(body: str, heading: str) -> str:
        pattern = rf"##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)"
        m = re.search(pattern, body, re.DOTALL)
        return m.group(1).strip() if m else ""

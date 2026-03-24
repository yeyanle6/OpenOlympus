"""Agent definition data class."""

from __future__ import annotations

from dataclasses import dataclass, field

from olympus.types import AgentLayer, AgentPermissions, LAYER_PERMISSIONS


@dataclass
class AgentDefinition:
    agent_id: str
    name: str
    description: str
    layer: AgentLayer
    capabilities: list[str] = field(default_factory=list)
    permissions: AgentPermissions | None = None
    max_concurrent: int = 1
    team: str = ""
    escalation_path: list[str] = field(default_factory=list)
    collaboration_protocols: list[str] = field(default_factory=list)
    persona: str = ""
    principles: str = ""
    framework: str = ""
    output_format: str = ""
    raw_body: str = ""

    @property
    def effective_permissions(self) -> AgentPermissions:
        if self.permissions is not None:
            return self.permissions
        return LAYER_PERMISSIONS.get(
            self.layer,
            AgentPermissions(tools=["read", "grep", "glob"]),
        )

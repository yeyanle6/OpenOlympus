"""Tests for AgentLoader — especially the new team/escalation/protocol fields."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from olympus.agent.loader import AgentLoader
from olympus.agent.definition import AgentDefinition


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    """Create a temporary agents directory with sample .md files."""
    d = tmp_path / "agents"
    d.mkdir()
    return d


def _write_agent(agents_dir: Path, name: str, frontmatter: str) -> Path:
    body = textwrap.dedent(f"""\
    # {name.title()}

    ## Persona
    Test persona for {name}.

    ## Core Principles
    1. Test principle

    ## Decision Framework
    - Test framework

    ## Output Format
    Test output
    """)
    path = agents_dir / f"{name}.md"
    path.write_text(f"---\n{frontmatter}---\n\n{body}")
    return path


class TestNewFrontmatterFields:
    """Verify team, escalation_path, and collaboration_protocols are parsed."""

    def test_all_three_fields_parsed(self, agents_dir: Path):
        _write_agent(agents_dir, "builder", textwrap.dedent("""\
            name: Builder
            description: Test builder
            layer: worker
            team: execution
            escalation_path: [coordinator, architect]
            collaboration_protocols: [delegate, pipeline, peer_review]
            capabilities: [code]
        """))
        loader = AgentLoader(agents_dir)
        defn = loader.get("builder")

        assert defn is not None
        assert defn.team == "execution"
        assert defn.escalation_path == ["coordinator", "architect"]
        assert defn.collaboration_protocols == ["delegate", "pipeline", "peer_review"]

    def test_fields_default_to_empty(self, agents_dir: Path):
        """Agents without the new fields should still load with defaults."""
        _write_agent(agents_dir, "legacy", textwrap.dedent("""\
            name: Legacy
            description: Old-style agent without new fields
            layer: specialist
            capabilities: [read]
        """))
        loader = AgentLoader(agents_dir)
        defn = loader.get("legacy")

        assert defn is not None
        assert defn.team == ""
        assert defn.escalation_path == []
        assert defn.collaboration_protocols == []

    def test_partial_fields(self, agents_dir: Path):
        """Only some new fields present — others default."""
        _write_agent(agents_dir, "partial", textwrap.dedent("""\
            name: Partial
            description: Partial fields
            layer: planning
            team: strategy
        """))
        loader = AgentLoader(agents_dir)
        defn = loader.get("partial")

        assert defn is not None
        assert defn.team == "strategy"
        assert defn.escalation_path == []
        assert defn.collaboration_protocols == []


class TestRealAgentFiles:
    """Load the actual agents/ directory and verify the new fields."""

    def test_load_all_12_agents(self):
        loader = AgentLoader("agents")
        agents = loader.load_all()
        assert len(agents) == 12

    def test_every_agent_has_team(self):
        loader = AgentLoader("agents")
        agents = loader.load_all()
        for agent_id, defn in agents.items():
            assert defn.team != "", f"{agent_id} missing team"

    def test_every_agent_has_escalation_path(self):
        loader = AgentLoader("agents")
        agents = loader.load_all()
        for agent_id, defn in agents.items():
            assert len(defn.escalation_path) > 0, f"{agent_id} missing escalation_path"

    def test_every_agent_has_collaboration_protocols(self):
        loader = AgentLoader("agents")
        agents = loader.load_all()
        for agent_id, defn in agents.items():
            assert len(defn.collaboration_protocols) > 0, f"{agent_id} missing collaboration_protocols"

    def test_team_assignments(self):
        """Verify squads match the 4-layer architecture."""
        loader = AgentLoader("agents")
        agents = loader.load_all()

        expected_teams = {
            "coordinator": "command",
            "tracker": "command",
            "planner": "strategy",
            "auditor": "strategy",
            "critic": "strategy",
            "builder": "execution",
            "worker": "execution",
            "architect": "intelligence",
            "researcher": "intelligence",
            "explorer": "intelligence",
            "reviewer": "intelligence",
            "tester": "intelligence",
        }
        for agent_id, expected_team in expected_teams.items():
            assert agents[agent_id].team == expected_team, (
                f"{agent_id}: expected team={expected_team}, got {agents[agent_id].team}"
            )

    def test_escalation_targets_exist(self):
        """Every agent in an escalation_path must itself be a valid agent."""
        loader = AgentLoader("agents")
        agents = loader.load_all()
        valid_ids = set(agents.keys())
        for agent_id, defn in agents.items():
            for target in defn.escalation_path:
                assert target in valid_ids, (
                    f"{agent_id} escalates to '{target}' which is not a valid agent"
                )

    def test_collaboration_protocols_are_valid(self):
        """All listed protocols must be one of the 5 defined protocols."""
        valid_protocols = {"delegate", "roundtable", "peer_review", "pipeline", "parallel"}
        loader = AgentLoader("agents")
        agents = loader.load_all()
        for agent_id, defn in agents.items():
            for proto in defn.collaboration_protocols:
                assert proto in valid_protocols, (
                    f"{agent_id} lists unknown protocol '{proto}'"
                )

    def test_backward_compatibility_existing_fields(self):
        """Ensure existing fields still load correctly after adding new ones."""
        loader = AgentLoader("agents")
        defn = loader.get("coordinator")
        assert defn is not None
        assert defn.name == "Coordinator"
        assert defn.layer.value == "orchestration"
        assert "dispatch" in defn.capabilities
        assert defn.effective_permissions.spawn_rooms is True

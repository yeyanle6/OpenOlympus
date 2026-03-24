"""Tests for agent permission enforcement."""

from olympus.types import AgentLayer, LAYER_PERMISSIONS
from olympus.agent.loader import AgentLoader


def test_planning_agents_cannot_write():
    loader = AgentLoader("agents")
    agents = loader.load_all()

    planning_agents = [a for a in agents.values() if a.layer == AgentLayer.PLANNING]
    assert len(planning_agents) == 3  # planner, auditor, critic

    for agent in planning_agents:
        perms = agent.effective_permissions
        assert not perms.write, f"{agent.name} should not have write permission"
        assert not perms.execute, f"{agent.name} should not have execute permission"


def test_worker_agents_can_write_and_execute():
    loader = AgentLoader("agents")
    agents = loader.load_all()

    worker_agents = [a for a in agents.values() if a.layer == AgentLayer.WORKER]
    assert len(worker_agents) == 2  # builder, worker

    for agent in worker_agents:
        perms = agent.effective_permissions
        assert perms.write, f"{agent.name} should have write permission"
        assert perms.execute, f"{agent.name} should have execute permission"


def test_specialist_agents_are_read_only():
    loader = AgentLoader("agents")
    agents = loader.load_all()

    specialist_agents = [a for a in agents.values() if a.layer == AgentLayer.SPECIALIST]
    assert len(specialist_agents) == 5  # architect, researcher, explorer, reviewer, tester

    for agent in specialist_agents:
        perms = agent.effective_permissions
        assert not perms.write, f"{agent.name} should not have write permission"
        assert not perms.execute, f"{agent.name} should not have execute permission"


def test_coordinator_can_spawn_rooms():
    loader = AgentLoader("agents")
    agents = loader.load_all()

    coordinator = agents["coordinator"]
    perms = coordinator.effective_permissions
    assert perms.spawn_rooms


def test_all_12_agents_loaded():
    loader = AgentLoader("agents")
    agents = loader.load_all()
    assert len(agents) == 12

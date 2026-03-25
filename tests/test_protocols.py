"""Tests for protocol implementations."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from olympus.types import Message, MessageType, AgentResult
from olympus.protocol.delegate import DelegateProtocol
from olympus.protocol.roundtable import RoundtableProtocol
from olympus.protocol.peer_review import PeerReviewProtocol
from olympus.protocol.pipeline import PipelineProtocol
from olympus.protocol.parallel_gather import ParallelGatherProtocol
from olympus.protocol.standup import StandupProtocol
from olympus.protocol.review_meeting import ReviewMeetingProtocol
from olympus.protocol.decision_gate import DecisionGateProtocol
from olympus.types import AgentLayer


class FakeAgent:
    """Minimal agent mock for protocol tests."""

    def __init__(self, agent_id: str, response: str = "OK", layer: AgentLayer | None = None):
        self.agent_id = agent_id
        self._response = response
        if layer is not None:
            self.definition = type("Def", (), {"layer": layer})()

    async def execute(self, task, context=None, **kwargs):
        return AgentResult(
            status="success",
            artifact=self._response,
            agent_id=self.agent_id,
        )


# ── Delegate ──────────────────────────────────────────────────

async def test_delegate_single_agent():
    agent = FakeAgent("builder", "built it")
    protocol = DelegateProtocol()
    results = await protocol.run([agent], "build something")

    assert len(results) == 1
    assert results[0].artifact == "built it"


async def test_delegate_empty_agents():
    protocol = DelegateProtocol()
    results = await protocol.run([], "nothing")
    assert results == []


# ── Roundtable ────────────────────────────────────────────────

async def test_roundtable_converges_on_done():
    agents = [
        FakeAgent("a", "I think [DONE]"),
        FakeAgent("b", "Agreed [DONE]"),
    ]
    protocol = RoundtableProtocol(max_rounds=3)
    results = await protocol.run(agents, "discuss")

    # Should converge in round 1 since majority says DONE
    assert len(results) == 2


async def test_roundtable_max_rounds():
    agents = [
        FakeAgent("a", "still thinking"),
        FakeAgent("b", "me too"),
    ]
    protocol = RoundtableProtocol(max_rounds=2)
    results = await protocol.run(agents, "discuss")

    # 2 agents * 2 rounds = 4 results
    assert len(results) == 4


# ── PeerReview ────────────────────────────────────────────────

async def test_peer_review_approved():
    author = FakeAgent("builder", "my work")
    reviewer = FakeAgent("reviewer", "looks good [APPROVED]")
    protocol = PeerReviewProtocol(max_rounds=3)
    results = await protocol.run([author, reviewer], "write code")

    # 1 author + 1 reviewer = 2 results (approved on first round)
    assert len(results) == 2


async def test_peer_review_needs_fewer_than_2_agents():
    protocol = PeerReviewProtocol()
    results = await protocol.run([FakeAgent("solo")], "review")
    assert results == []


# ── Pipeline ──────────────────────────────────────────────────

async def test_pipeline_sequential():
    agents = [
        FakeAgent("planner", "plan ready"),
        FakeAgent("builder", "code written"),
        FakeAgent("tester", "tests pass"),
    ]
    protocol = PipelineProtocol()
    results = await protocol.run(agents, "build feature")

    assert len(results) == 3
    assert results[0].artifact == "plan ready"
    assert results[2].artifact == "tests pass"


# ── ParallelGather ────────────────────────────────────────────

async def test_parallel_gather():
    agents = [
        FakeAgent("explorer", "found file A"),
        FakeAgent("researcher", "docs say X"),
    ]
    protocol = ParallelGatherProtocol()
    results = await protocol.run(agents, "research topic")

    assert len(results) == 2
    artifacts = {r.artifact for r in results}
    assert "found file A" in artifacts
    assert "docs say X" in artifacts


async def test_parallel_gather_with_synthesis():
    agents = [
        FakeAgent("explorer", "found A"),
        FakeAgent("researcher", "found B"),
    ]
    from olympus.protocol.parallel_gather import MergeStrategy
    protocol = ParallelGatherProtocol(synthesizer_index=0, merge_strategy=MergeStrategy.SYNTHESIZE)
    results = await protocol.run(agents, "research")

    # 2 parallel + 1 synthesis = 3 results
    assert len(results) == 3


# ── Standup ──────────────────────────────────────────────────

async def test_standup_all_agents():
    agents = [
        FakeAgent("builder", "did X, next Y, no blockers"),
        FakeAgent("tester", "ran tests, next coverage, no blockers"),
    ]
    protocol = StandupProtocol()
    results = await protocol.run(agents, "daily standup")

    assert len(results) == 2
    assert results[0].agent_id == "builder"
    assert results[1].agent_id == "tester"


async def test_standup_filter_by_role():
    agents = [
        FakeAgent("builder", "update"),
        FakeAgent("tester", "update"),
        FakeAgent("critic", "update"),
    ]
    protocol = StandupProtocol(allowed_roles=["builder", "tester"])
    results = await protocol.run(agents, "standup")

    assert len(results) == 2
    assert {r.agent_id for r in results} == {"builder", "tester"}


async def test_standup_filter_by_layer():
    agents = [
        FakeAgent("builder", "update", layer=AgentLayer.WORKER),
        FakeAgent("critic", "update", layer=AgentLayer.PLANNING),
    ]
    protocol = StandupProtocol(allowed_layers=[AgentLayer.WORKER])
    results = await protocol.run(agents, "standup")

    assert len(results) == 1
    assert results[0].agent_id == "builder"


async def test_standup_empty_after_filter():
    agents = [FakeAgent("builder", "update")]
    protocol = StandupProtocol(allowed_roles=["nonexistent"])
    results = await protocol.run(agents, "standup")
    assert results == []


# ── ReviewMeeting ────────────────────────────────────────────

async def test_review_meeting_all_approved():
    presenter = FakeAgent("builder", "my design")
    reviewer1 = FakeAgent("critic", "looks good [APPROVED]")
    reviewer2 = FakeAgent("auditor", "solid [APPROVED]")
    protocol = ReviewMeetingProtocol(max_rounds=3)
    results = await protocol.run([presenter, reviewer1, reviewer2], "review design")

    # 1 presenter + 2 reviewers = 3 results (approved round 1)
    assert len(results) == 3


async def test_review_meeting_blocked():
    presenter = FakeAgent("builder", "my design")
    reviewer = FakeAgent("critic", "critical flaw [BLOCKED]")
    protocol = ReviewMeetingProtocol(max_rounds=3)
    results = await protocol.run([presenter, reviewer], "review design")

    # 1 presenter + 1 reviewer = 2 results (blocked immediately)
    assert len(results) == 2


async def test_review_meeting_max_rounds():
    presenter = FakeAgent("builder", "my design")
    reviewer = FakeAgent("critic", "needs work")
    protocol = ReviewMeetingProtocol(max_rounds=2)
    results = await protocol.run([presenter, reviewer], "review design")

    # 2 rounds * (1 presenter + 1 reviewer) = 4
    assert len(results) == 4


async def test_review_meeting_needs_presenter_and_reviewer():
    protocol = ReviewMeetingProtocol()
    results = await protocol.run([FakeAgent("solo")], "review")
    assert results == []


# ── DecisionGate ─────────────────────────────────────────────

async def test_decision_gate_majority_approved():
    agents = [
        FakeAgent("planner", "go ahead [APPROVED]"),
        FakeAgent("auditor", "[APPROVED] looks safe"),
        FakeAgent("critic", "I have concerns but ok"),
    ]
    protocol = DecisionGateProtocol()
    results = await protocol.run(agents, "should we deploy?")

    # All 3 vote, 2/3 > 50% threshold → approved
    assert len(results) == 3


async def test_decision_gate_vetoed_with_alternative():
    """Veto WITH alternative blocks the vote (Apache principle)."""
    agents = [
        FakeAgent("planner", "[APPROVED]"),
        FakeAgent("auditor", "critical flaw [BLOCKED]. Alternative: use approach B instead"),
        FakeAgent("critic", "[APPROVED]"),
    ]
    protocol = DecisionGateProtocol()
    results = await protocol.run(agents, "should we deploy?")

    # Stops at auditor's veto (has alternative) — only 2 votes cast
    assert len(results) == 2
    assert results[1].agent_id == "auditor"


async def test_decision_gate_veto_without_alternative_becomes_abstain():
    """Veto WITHOUT alternative is downgraded to abstention."""
    agents = [
        FakeAgent("planner", "[APPROVED]"),
        FakeAgent("auditor", "absolutely not [BLOCKED]"),
        FakeAgent("critic", "[APPROVED]"),
    ]
    protocol = DecisionGateProtocol()
    results = await protocol.run(agents, "should we deploy?")

    # Veto without alternative → abstained, voting continues, all 3 vote
    assert len(results) == 3


async def test_decision_gate_below_threshold():
    agents = [
        FakeAgent("a", "[APPROVED]"),
        FakeAgent("b", "not sure"),
        FakeAgent("c", "need more info"),
    ]
    protocol = DecisionGateProtocol(approval_threshold=0.5)
    results = await protocol.run(agents, "proceed?")

    # 1/3 ≈ 33% which is not > 50% → blocked
    assert len(results) == 3


async def test_decision_gate_filter_by_role():
    agents = [
        FakeAgent("planner", "[APPROVED]"),
        FakeAgent("builder", "[APPROVED]"),
        FakeAgent("tester", "[APPROVED]"),
    ]
    protocol = DecisionGateProtocol(voter_roles=["planner", "tester"])
    results = await protocol.run(agents, "vote")

    assert len(results) == 2
    assert {r.agent_id for r in results} == {"planner", "tester"}


async def test_decision_gate_empty_voters():
    protocol = DecisionGateProtocol(voter_roles=["nobody"])
    results = await protocol.run([FakeAgent("builder")], "vote")
    assert results == []

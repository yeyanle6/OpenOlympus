"""Tests for OKR progress tracking and decision logging in the loop engine.

Covers:
- OKR serialization (objects → Markdown round-trip)
- KR progress recomputation from initiative completion ratios
- Initiative status transition detection
- Consensus OKR section replacement / insertion
- Confidence and impact scope derivation heuristics
- Engine cycle integration with OKR updates and decision log
"""

import pytest

from olympus.loop.convergence import Phase
from olympus.loop.engine import LoopEngine
from olympus.loop.stagnation import BlockerInfo, BlockerType
from olympus.memory.consensus import (
    ConsensusMemory,
    Initiative,
    KeyResult,
    Objective,
)
from olympus.memory.history import Confidence, DecisionHistory, ImpactScope


# =====================================================================
# OKR serialization (serialize_okrs)
# =====================================================================


class TestSerializeOKRs:
    def test_serialize_basic(self):
        okrs = [
            Objective(
                id="O1",
                description="Launch MVP",
                key_results=[
                    KeyResult(id="KR1", description="Core features", progress=0.8),
                    KeyResult(id="KR2", description="Security audit", progress=0.3),
                ],
            ),
        ]
        md = ConsensusMemory.serialize_okrs(okrs)
        assert "## OKR" in md
        assert "### O1: Launch MVP" in md
        assert "KR1: Core features [progress: 0.80]" in md
        assert "KR2: Security audit [progress: 0.30]" in md

    def test_serialize_with_initiatives(self):
        okrs = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="Feature",
                        progress=0.5,
                        initiatives=[
                            Initiative(id="I1", description="Build auth", status="done", owner="builder"),
                            Initiative(id="I2", description="Write tests", status="pending"),
                        ],
                    ),
                ],
            ),
        ]
        md = ConsensusMemory.serialize_okrs(okrs)
        assert "I1: Build auth [status: done] @builder" in md
        assert "I2: Write tests [status: pending]" in md
        # No @owner for I2
        assert md.count("@") == 1

    def test_serialize_empty(self):
        assert ConsensusMemory.serialize_okrs([]) == ""

    def test_round_trip(self):
        """Parse → serialize → parse should produce equivalent objects."""
        consensus = (
            "## OKR\n"
            "### O1: Launch MVP\n"
            "- KR1: Core features [progress: 0.80]\n"
            "  - I1: Build auth [status: done] @builder\n"
            "  - I2: API endpoints [status: in_progress] @worker\n"
            "- KR2: Security audit [progress: 0.30]\n"
            "### O2: Growth\n"
            "- KR1: Signups [progress: 0.50]\n"
        )
        okrs = ConsensusMemory.extract_okrs(consensus)
        md = ConsensusMemory.serialize_okrs(okrs)
        okrs2 = ConsensusMemory.extract_okrs(md)

        assert len(okrs2) == len(okrs)
        assert okrs2[0].id == "O1"
        assert len(okrs2[0].key_results) == 2
        assert okrs2[0].key_results[0].progress == pytest.approx(0.8)
        assert len(okrs2[0].key_results[0].initiatives) == 2
        assert okrs2[0].key_results[0].initiatives[0].status == "done"
        assert okrs2[0].key_results[0].initiatives[0].owner == "builder"


# =====================================================================
# update_okr_section (replace/insert OKR block in consensus)
# =====================================================================


class TestUpdateOKRSection:
    def test_replace_existing_section(self):
        consensus = (
            "## Company State\nOK\n\n"
            "## OKR\n"
            "### O1: Old\n"
            "- KR1: Old feature [progress: 0.10]\n\n"
            "## Next Action\nShip it\n"
        )
        okrs = [
            Objective(
                id="O1",
                description="New",
                key_results=[KeyResult(id="KR1", description="New feature", progress=0.90)],
            ),
        ]
        updated = ConsensusMemory.update_okr_section(consensus, okrs)
        assert "progress: 0.90" in updated
        assert "New feature" in updated
        assert "Old feature" not in updated
        # Other sections preserved
        assert "## Company State" in updated
        assert "## Next Action" in updated

    def test_insert_before_next_action(self):
        consensus = (
            "## Company State\nOK\n\n"
            "## Next Action\nShip it\n"
        )
        okrs = [
            Objective(
                id="O1",
                description="Test",
                key_results=[KeyResult(id="KR1", description="A", progress=0.5)],
            ),
        ]
        updated = ConsensusMemory.update_okr_section(consensus, okrs)
        assert "## OKR" in updated
        # OKR should come before Next Action
        okr_pos = updated.index("## OKR")
        next_pos = updated.index("## Next Action")
        assert okr_pos < next_pos

    def test_append_when_no_next_action(self):
        consensus = "## Company State\nOK\n"
        okrs = [
            Objective(
                id="O1",
                description="Test",
                key_results=[KeyResult(id="KR1", description="A", progress=0.5)],
            ),
        ]
        updated = ConsensusMemory.update_okr_section(consensus, okrs)
        assert updated.endswith("\n")
        assert "## OKR" in updated

    def test_noop_with_empty_okrs(self):
        consensus = "## Company State\nOK\n"
        assert ConsensusMemory.update_okr_section(consensus, []) == consensus


# =====================================================================
# KR progress recomputation from initiatives
# =====================================================================


class TestUpdateKRProgressFromInitiatives:
    def test_all_done(self):
        okrs = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        progress=0.0,
                        initiatives=[
                            Initiative(id="I1", description="X", status="done"),
                            Initiative(id="I2", description="Y", status="done"),
                        ],
                    ),
                ],
            ),
        ]
        ConsensusMemory.update_kr_progress_from_initiatives(okrs)
        assert okrs[0].key_results[0].progress == pytest.approx(1.0)

    def test_partial_completion(self):
        okrs = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        progress=0.0,
                        initiatives=[
                            Initiative(id="I1", description="X", status="done"),
                            Initiative(id="I2", description="Y", status="in_progress"),
                            Initiative(id="I3", description="Z", status="pending"),
                        ],
                    ),
                ],
            ),
        ]
        ConsensusMemory.update_kr_progress_from_initiatives(okrs)
        assert okrs[0].key_results[0].progress == pytest.approx(1 / 3)

    def test_no_initiatives_preserves_progress(self):
        okrs = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(id="KR1", description="A", progress=0.75),
                ],
            ),
        ]
        ConsensusMemory.update_kr_progress_from_initiatives(okrs)
        assert okrs[0].key_results[0].progress == pytest.approx(0.75)

    def test_objective_progress_updates(self):
        okrs = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        progress=0.0,
                        initiatives=[
                            Initiative(id="I1", description="X", status="done"),
                            Initiative(id="I2", description="Y", status="done"),
                        ],
                    ),
                    KeyResult(
                        id="KR2",
                        description="B",
                        progress=0.0,
                        initiatives=[
                            Initiative(id="I1", description="X", status="pending"),
                        ],
                    ),
                ],
            ),
        ]
        ConsensusMemory.update_kr_progress_from_initiatives(okrs)
        # KR1 = 1.0, KR2 = 0.0 → average = 0.5
        assert okrs[0].progress == pytest.approx(0.5)


# =====================================================================
# Initiative status transition detection
# =====================================================================


class TestTransitionInitiativeStatuses:
    def test_pending_to_in_progress(self):
        old = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="pending"),
                        ],
                    ),
                ],
            ),
        ]
        new = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="in_progress"),
                        ],
                    ),
                ],
            ),
        ]
        transitions = ConsensusMemory.transition_initiative_statuses(old, new)
        assert len(transitions) == 1
        assert transitions[0] == ("O1", "KR1", "I1", "pending → in_progress")

    def test_in_progress_to_done(self):
        old = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="in_progress"),
                        ],
                    ),
                ],
            ),
        ]
        new = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="done"),
                        ],
                    ),
                ],
            ),
        ]
        transitions = ConsensusMemory.transition_initiative_statuses(old, new)
        assert len(transitions) == 1
        assert "in_progress → done" in transitions[0][3]

    def test_blocked_transition(self):
        old = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="in_progress"),
                        ],
                    ),
                ],
            ),
        ]
        new = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="blocked"),
                        ],
                    ),
                ],
            ),
        ]
        transitions = ConsensusMemory.transition_initiative_statuses(old, new)
        assert len(transitions) == 1
        assert "blocked" in transitions[0][3]

    def test_no_change_no_transitions(self):
        objs = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="done"),
                        ],
                    ),
                ],
            ),
        ]
        transitions = ConsensusMemory.transition_initiative_statuses(objs, objs)
        assert transitions == []

    def test_new_initiative_not_in_old(self):
        old = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(id="KR1", description="A", initiatives=[]),
                ],
            ),
        ]
        new = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="in_progress"),
                        ],
                    ),
                ],
            ),
        ]
        transitions = ConsensusMemory.transition_initiative_statuses(old, new)
        assert len(transitions) == 1
        assert "pending → in_progress" in transitions[0][3]

    def test_multiple_transitions(self):
        old = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="pending"),
                            Initiative(id="I2", description="Y", status="in_progress"),
                        ],
                    ),
                ],
            ),
        ]
        new = [
            Objective(
                id="O1",
                description="Test",
                key_results=[
                    KeyResult(
                        id="KR1",
                        description="A",
                        initiatives=[
                            Initiative(id="I1", description="X", status="in_progress"),
                            Initiative(id="I2", description="Y", status="done"),
                        ],
                    ),
                ],
            ),
        ]
        transitions = ConsensusMemory.transition_initiative_statuses(old, new)
        assert len(transitions) == 2


# =====================================================================
# Confidence and impact scope derivation
# =====================================================================


class TestDeriveConfidenceAndImpact:
    def test_high_confidence_execute_no_blockers(self):
        conf, scope = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, [], [], []
        )
        assert conf == Confidence.HIGH
        assert scope == ImpactScope.NARROW

    def test_low_confidence_brainstorm(self):
        conf, _ = LoopEngine._derive_confidence_and_impact(
            Phase.BRAINSTORM, [], [], []
        )
        assert conf == Confidence.LOW

    def test_medium_confidence_one_blocker(self):
        blocker = BlockerInfo(
            type=BlockerType.WIP_OVERFLOW, description="WIP 3/2"
        )
        conf, _ = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, [blocker], [], []
        )
        assert conf == Confidence.MEDIUM

    def test_low_confidence_stagnation(self):
        blocker = BlockerInfo(
            type=BlockerType.STAGNATION, description="Same action"
        )
        conf, _ = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, [blocker], [], []
        )
        assert conf == Confidence.LOW

    def test_low_confidence_multiple_blockers(self):
        blockers = [
            BlockerInfo(type=BlockerType.WIP_OVERFLOW, description="WIP"),
            BlockerInfo(type=BlockerType.BLOCKED_ITEM, description="Blocked"),
        ]
        conf, _ = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, blockers, [], []
        )
        assert conf == Confidence.LOW

    def test_medium_confidence_evaluate(self):
        conf, _ = LoopEngine._derive_confidence_and_impact(
            Phase.EVALUATE, [], [], []
        )
        assert conf == Confidence.MEDIUM

    def test_broad_scope_sprint_review(self):
        _, scope = LoopEngine._derive_confidence_and_impact(
            Phase.SPRINT_REVIEW, [], [], []
        )
        assert scope == ImpactScope.BROAD

    def test_broad_scope_many_transitions(self):
        transitions = [
            ("O1", "KR1", "I1", "pending → in_progress"),
            ("O1", "KR1", "I2", "in_progress → done"),
            ("O1", "KR2", "I1", "pending → in_progress"),
        ]
        _, scope = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, [], transitions, []
        )
        assert scope == ImpactScope.BROAD

    def test_moderate_scope_with_transitions(self):
        transitions = [("O1", "KR1", "I1", "pending → in_progress")]
        _, scope = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, [], transitions, []
        )
        assert scope == ImpactScope.MODERATE

    def test_moderate_scope_execute_no_transitions(self):
        okrs = [Objective(id="O1", description="Test")]
        _, scope = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, [], [], okrs
        )
        assert scope == ImpactScope.MODERATE

    def test_narrow_scope_no_okrs_no_transitions(self):
        _, scope = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, [], [], []
        )
        assert scope == ImpactScope.NARROW

    def test_broad_scope_retrospect(self):
        _, scope = LoopEngine._derive_confidence_and_impact(
            Phase.RETROSPECT, [], [], []
        )
        assert scope == ImpactScope.BROAD


# =====================================================================
# Integration: decision log records confidence/impact_scope
# =====================================================================


class TestDecisionLogIntegration:
    @pytest.fixture
    def tmp_history(self, tmp_path):
        return DecisionHistory(path=tmp_path / "decisions.jsonl")

    async def test_record_with_confidence_and_scope(self, tmp_history):
        await tmp_history.record(
            decision="Cycle 5 (execute) completed",
            rationale="Ship auth module",
            cycle=5,
            phase="execute",
            confidence=Confidence.HIGH,
            impact_scope=ImpactScope.MODERATE,
        )
        entries = await tmp_history.get_recent(5)
        assert len(entries) == 1
        assert entries[0]["confidence"] == "high"
        assert entries[0]["impact_scope"] == "moderate"

    async def test_confidence_searchable(self, tmp_history):
        await tmp_history.record(
            decision="Cycle 1 (brainstorm) completed",
            cycle=1,
            phase="brainstorm",
            confidence=Confidence.LOW,
            impact_scope=ImpactScope.NARROW,
        )
        await tmp_history.record(
            decision="Cycle 3 (execute) completed",
            cycle=3,
            phase="execute",
            confidence=Confidence.HIGH,
            impact_scope=ImpactScope.BROAD,
        )
        results = await tmp_history.search("low")
        assert len(results) == 1
        assert results[0]["confidence"] == "low"


# =====================================================================
# Full pipeline: consensus → OKR update → serialize → re-parse
# =====================================================================


class TestFullOKRPipeline:
    """End-to-end: extract → transition → recompute → serialize → verify."""

    CONSENSUS_BEFORE = (
        "## Company State\nBuilding MVP\n\n"
        "## OKR\n"
        "### O1: Launch MVP\n"
        "- KR1: Core features [progress: 0.50]\n"
        "  - I1: Build auth [status: pending]\n"
        "  - I2: Build API [status: in_progress]\n"
        "- KR2: Security audit [progress: 0.00]\n"
        "  - I1: Run OWASP scan [status: pending]\n\n"
        "## Next Action\nComplete auth module\n"
    )

    CONSENSUS_AFTER = (
        "## Company State\nBuilding MVP\n\n"
        "## OKR\n"
        "### O1: Launch MVP\n"
        "- KR1: Core features [progress: 0.50]\n"
        "  - I1: Build auth [status: done] @builder\n"
        "  - I2: Build API [status: done] @worker\n"
        "- KR2: Security audit [progress: 0.00]\n"
        "  - I1: Run OWASP scan [status: in_progress]\n\n"
        "## Next Action\nRun security audit\n"
    )

    def test_full_pipeline(self):
        # 1. Extract OKRs before and after
        old_okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_BEFORE)
        new_okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_AFTER)

        # 2. Detect transitions
        transitions = ConsensusMemory.transition_initiative_statuses(
            old_okrs, new_okrs
        )
        assert len(transitions) == 3  # I1: pending→done, I2: ip→done, I1: pending→ip

        # 3. Recompute KR progress
        ConsensusMemory.update_kr_progress_from_initiatives(new_okrs)
        # KR1: 2/2 done = 1.0, KR2: 0/1 done = 0.0
        assert new_okrs[0].key_results[0].progress == pytest.approx(1.0)
        assert new_okrs[0].key_results[1].progress == pytest.approx(0.0)
        # O1 progress = (1.0 + 0.0) / 2 = 0.5
        assert new_okrs[0].progress == pytest.approx(0.5)

        # 4. Serialize back to consensus
        updated = ConsensusMemory.update_okr_section(
            self.CONSENSUS_AFTER, new_okrs
        )
        assert "progress: 1.00" in updated
        assert "progress: 0.00" in updated

        # 5. Re-parse and verify
        final_okrs = ConsensusMemory.extract_okrs(updated)
        assert final_okrs[0].key_results[0].progress == pytest.approx(1.0)
        assert final_okrs[0].key_results[1].progress == pytest.approx(0.0)

        # 6. Other sections preserved
        assert "## Company State" in updated
        assert "## Next Action" in updated

    def test_confidence_derivation_for_pipeline(self):
        """Confidence/impact should reflect the pipeline state."""
        old_okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_BEFORE)
        new_okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_AFTER)
        transitions = ConsensusMemory.transition_initiative_statuses(
            old_okrs, new_okrs
        )

        # 3 transitions in execute phase → HIGH confidence, BROAD scope
        conf, scope = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, [], transitions, new_okrs
        )
        assert conf == Confidence.HIGH
        assert scope == ImpactScope.BROAD

    def test_stagnation_degrades_pipeline_confidence(self):
        """Stagnation blocker overrides to LOW confidence."""
        old_okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_BEFORE)
        new_okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_AFTER)
        transitions = ConsensusMemory.transition_initiative_statuses(
            old_okrs, new_okrs
        )

        stagnation = BlockerInfo(
            type=BlockerType.STAGNATION,
            description="Same action for 2 cycles",
            severity="critical",
        )
        conf, scope = LoopEngine._derive_confidence_and_impact(
            Phase.EXECUTE, [stagnation], transitions, new_okrs
        )
        assert conf == Confidence.LOW
        # Scope still broad due to 3 transitions
        assert scope == ImpactScope.BROAD

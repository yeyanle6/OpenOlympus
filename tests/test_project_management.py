"""Tests for project management methodology fusion.

Covers: Sprint boundaries, Kanban blocking detection, OKR parsing,
performance metrics, decision history extensions, WBS task decomposition,
OKR-sprint alignment, decision types with alternatives, and Room config
sprint context.
"""

import pytest

from olympus.loop.convergence import ConvergenceController, Phase, SprintConfig
from olympus.loop.stagnation import BlockerType, StagnationDetector
from olympus.memory.consensus import ConsensusMemory, KeyResult, Objective
from olympus.memory.history import (
    Alternative,
    DecisionHistory,
    DecisionType,
    PerformanceMetrics,
    SprintSummary,
)
from olympus.memory.wbs import TaskBreakdown, TaskStatus, WBSNode
from olympus.types import RoomConfig


# =====================================================================
# Sprint boundary checks (convergence controller)
# =====================================================================


class TestSprintBoundaries:
    def test_no_sprint_config_preserves_original_behaviour(self):
        cc = ConvergenceController()
        assert cc.get_phase(1) == Phase.BRAINSTORM
        assert cc.get_phase(2) == Phase.EVALUATE
        assert cc.get_phase(3) == Phase.EXECUTE
        assert cc.get_phase(5) == Phase.RETROSPECT

    def test_sprint_boundary_triggers_review(self):
        cc = ConvergenceController(sprint=SprintConfig(sprint_length=5))
        assert cc.get_phase(5) == Phase.SPRINT_REVIEW
        assert cc.get_phase(10) == Phase.SPRINT_REVIEW

    def test_sprint_start_triggers_planning(self):
        cc = ConvergenceController(sprint=SprintConfig(sprint_length=5))
        # Cycle 6 is start of sprint 2
        assert cc.get_phase(6) == Phase.SPRINT_PLANNING
        assert cc.get_phase(11) == Phase.SPRINT_PLANNING

    def test_sprint_cycle_1_is_still_brainstorm(self):
        cc = ConvergenceController(sprint=SprintConfig(sprint_length=5))
        assert cc.get_phase(1) == Phase.BRAINSTORM

    def test_mid_sprint_is_execute(self):
        cc = ConvergenceController(sprint=SprintConfig(sprint_length=10))
        assert cc.get_phase(3) == Phase.EXECUTE
        assert cc.get_phase(7) == Phase.EXECUTE

    def test_sprint_review_overrides_retrospect(self):
        # Sprint length = 5, retrospect_interval = 5 → sprint review wins
        cc = ConvergenceController(
            retrospect_interval=5, sprint=SprintConfig(sprint_length=5)
        )
        assert cc.get_phase(5) == Phase.SPRINT_REVIEW

    def test_current_sprint_number(self):
        cc = ConvergenceController(sprint=SprintConfig(sprint_length=5))
        assert cc.current_sprint(1) == 1
        assert cc.current_sprint(5) == 1
        assert cc.current_sprint(6) == 2
        assert cc.current_sprint(10) == 2
        assert cc.current_sprint(11) == 3

    def test_current_sprint_no_config(self):
        cc = ConvergenceController()
        assert cc.current_sprint(5) == 0

    def test_is_sprint_boundary(self):
        cc = ConvergenceController(sprint=SprintConfig(sprint_length=4))
        assert not cc.is_sprint_boundary(3)
        assert cc.is_sprint_boundary(4)
        assert cc.is_sprint_boundary(8)

    def test_disabled_planning(self):
        cc = ConvergenceController(
            sprint=SprintConfig(sprint_length=5, planning_enabled=False)
        )
        # Cycle 6 would be sprint planning, but it's disabled
        assert cc.get_phase(6) == Phase.EXECUTE

    def test_disabled_review(self):
        cc = ConvergenceController(
            sprint=SprintConfig(sprint_length=5, review_enabled=False),
            retrospect_interval=5,
        )
        # Cycle 5 would be sprint review, but review is disabled → retrospect
        assert cc.get_phase(5) == Phase.RETROSPECT

    def test_phase_rules_for_sprint_phases(self):
        cc = ConvergenceController()
        planning_rules = cc.get_phase_rules(Phase.SPRINT_PLANNING)
        assert "Sprint Planning" in planning_rules
        assert "backlog" in planning_rules.lower()

        review_rules = cc.get_phase_rules(Phase.SPRINT_REVIEW)
        assert "Sprint Review" in review_rules
        assert "velocity" in review_rules.lower()


# =====================================================================
# Kanban blocking detection (stagnation detector)
# =====================================================================


class TestKanbanBlocking:
    def test_wip_overflow_detection(self):
        sd = StagnationDetector(wip_limit=2)
        consensus = (
            "## Tasks\n"
            "- [~] Build API\n"
            "- [~] Write tests\n"
            "- [~] Deploy staging\n"
        )
        blockers = sd.detect_blockers(consensus)
        wip_blockers = [b for b in blockers if b.type == BlockerType.WIP_OVERFLOW]
        assert len(wip_blockers) == 1
        assert "3/2" in wip_blockers[0].description

    def test_no_wip_overflow_under_limit(self):
        sd = StagnationDetector(wip_limit=5)
        consensus = "- [~] Build API\n- [~] Write tests\n"
        blockers = sd.detect_blockers(consensus)
        wip_blockers = [b for b in blockers if b.type == BlockerType.WIP_OVERFLOW]
        assert len(wip_blockers) == 0

    def test_wip_disabled_by_default(self):
        sd = StagnationDetector()
        consensus = "- [~] A\n- [~] B\n- [~] C\n"
        blockers = sd.detect_blockers(consensus)
        assert all(b.type != BlockerType.WIP_OVERFLOW for b in blockers)

    def test_blocked_item_detection(self):
        sd = StagnationDetector()
        consensus = (
            "## Sprint Backlog\n"
            "- [x] Setup DB\n"
            "- [ ] Auth service [BLOCKED]\n"
            "- [~] Frontend\n"
        )
        blockers = sd.detect_blockers(consensus)
        blocked = [b for b in blockers if b.type == BlockerType.BLOCKED_ITEM]
        assert len(blocked) == 1
        assert "Auth service" in blocked[0].description

    def test_multiple_blocker_types(self):
        sd = StagnationDetector(threshold=2, wip_limit=1)
        sd.record_action("same thing")
        sd.record_action("same thing")
        consensus = "- [~] A\n- [~] B\n- C [BLOCKED]\n"
        blockers = sd.detect_blockers(consensus)
        types = {b.type for b in blockers}
        assert BlockerType.STAGNATION in types
        assert BlockerType.WIP_OVERFLOW in types
        assert BlockerType.BLOCKED_ITEM in types

    def test_get_blocker_warning_empty_when_clean(self):
        sd = StagnationDetector()
        assert sd.get_blocker_warning("clean consensus") == ""

    def test_get_blocker_warning_nonempty(self):
        sd = StagnationDetector(wip_limit=1)
        consensus = "- [~] A\n- [~] B\n"
        warning = sd.get_blocker_warning(consensus)
        assert "Kanban blockers" in warning
        assert "wip_overflow" in warning

    def test_extract_wip_items_multiple_formats(self):
        consensus = (
            "- [~] Task A\n"
            "- [WIP] Task B\n"
            "- Task C [IN PROGRESS]\n"
            "- [x] Done task\n"
            "- [ ] Pending task\n"
        )
        items = StagnationDetector.extract_wip_items(consensus)
        assert len(items) == 3
        assert "Task A" in items
        assert "Task B" in items
        assert "Task C" in items

    def test_extract_blocked_items(self):
        consensus = (
            "- Auth service [BLOCKED]\n"
            "- [~] Deploy [blocked]\n"
            "- Normal task\n"
        )
        blocked = StagnationDetector.extract_blocked_items(consensus)
        assert len(blocked) == 2


# =====================================================================
# OKR fields in consensus
# =====================================================================


class TestOKRExtraction:
    SAMPLE_OKR = (
        "## Company State\n"
        "- Product: MVP\n\n"
        "## OKR\n"
        "### O1: Launch MVP by Q2\n"
        "- KR1: Complete core features [progress: 0.8]\n"
        "- KR2: Pass security audit [progress: 0.3]\n"
        "### O2: Grow user base\n"
        "- KR1: Reach 1000 signups [progress: 0.5]\n"
        "- KR2: Achieve 40% retention [progress: 0.2]\n\n"
        "## Next Action\n"
        "Ship auth module\n"
    )

    def test_extract_objectives(self):
        okrs = ConsensusMemory.extract_okrs(self.SAMPLE_OKR)
        assert len(okrs) == 2
        assert okrs[0].id == "O1"
        assert okrs[1].id == "O2"

    def test_extract_key_results(self):
        okrs = ConsensusMemory.extract_okrs(self.SAMPLE_OKR)
        assert len(okrs[0].key_results) == 2
        assert okrs[0].key_results[0].id == "KR1"
        assert okrs[0].key_results[0].progress == pytest.approx(0.8)
        assert okrs[0].key_results[1].progress == pytest.approx(0.3)

    def test_objective_progress_is_average(self):
        okrs = ConsensusMemory.extract_okrs(self.SAMPLE_OKR)
        assert okrs[0].progress == pytest.approx(0.55)  # (0.8 + 0.3) / 2
        assert okrs[1].progress == pytest.approx(0.35)  # (0.5 + 0.2) / 2

    def test_no_okr_section_returns_empty(self):
        consensus = "## Next Action\nDo stuff\n## Company State\nOK\n"
        assert ConsensusMemory.extract_okrs(consensus) == []

    def test_progress_clamped_to_range(self):
        consensus = (
            "## OKR\n"
            "### O1: Test\n"
            "- KR1: Over [progress: 1.5]\n"
            "- KR2: Under [progress: -0.2]\n"
        )
        okrs = ConsensusMemory.extract_okrs(consensus)
        assert okrs[0].key_results[0].progress == 1.0
        assert okrs[0].key_results[1].progress == 0.0

    def test_missing_progress_defaults_zero(self):
        consensus = (
            "## OKR\n"
            "### O1: Test\n"
            "- KR1: No progress tag\n"
        )
        okrs = ConsensusMemory.extract_okrs(consensus)
        assert okrs[0].key_results[0].progress == 0.0

    def test_validate_okr_valid(self):
        issues = ConsensusMemory.validate_okr_section(self.SAMPLE_OKR)
        assert issues == []

    def test_validate_okr_duplicate_objective(self):
        consensus = (
            "## OKR\n"
            "### O1: First\n"
            "- KR1: A [progress: 0.5]\n"
            "### O1: Duplicate\n"
            "- KR1: B [progress: 0.5]\n"
        )
        issues = ConsensusMemory.validate_okr_section(consensus)
        assert any("Duplicate" in i for i in issues)

    def test_validate_okr_no_key_results(self):
        consensus = (
            "## OKR\n"
            "### O1: Empty objective\n"
        )
        issues = ConsensusMemory.validate_okr_section(consensus)
        assert any("no key results" in i for i in issues)

    def test_validate_no_okr_section_is_valid(self):
        issues = ConsensusMemory.validate_okr_section("## Next Action\nStuff\n")
        assert issues == []


# =====================================================================
# Performance metrics and decision history
# =====================================================================


class TestPerformanceMetrics:
    def test_velocity_calculation(self):
        m = PerformanceMetrics(tasks_completed=7, tasks_committed=10)
        assert m.velocity == pytest.approx(0.7)

    def test_velocity_zero_committed(self):
        m = PerformanceMetrics(tasks_completed=0, tasks_committed=0)
        assert m.velocity == 0.0

    def test_sprint_summary_velocity(self):
        s = SprintSummary(
            sprint=1, total_tasks_completed=8, total_tasks_committed=10
        )
        assert s.velocity == pytest.approx(0.8)


class TestDecisionHistoryExtended:
    @pytest.fixture
    def tmp_history(self, tmp_path):
        return DecisionHistory(path=tmp_path / "decisions.jsonl")

    async def test_record_with_sprint_and_metrics(self, tmp_history):
        metrics = PerformanceMetrics(
            cycle_duration_ms=5000,
            cost_usd=0.15,
            tokens_used=3000,
            tasks_completed=2,
            tasks_committed=3,
            blockers_detected=1,
            blocker_types=["wip_overflow"],
        )
        await tmp_history.record(
            decision="Sprint 1 cycle 3",
            cycle=3,
            phase="execute",
            sprint=1,
            sprint_goal="Launch MVP",
            metrics=metrics,
        )

        entries = await tmp_history.get_recent(5)
        assert len(entries) == 1
        e = entries[0]
        assert e["sprint"] == 1
        assert e["sprint_goal"] == "Launch MVP"
        assert e["metrics"]["cost_usd"] == pytest.approx(0.15)
        assert e["metrics"]["tasks_completed"] == 2
        assert e["metrics"]["blocker_types"] == ["wip_overflow"]

    async def test_record_without_sprint_omits_field(self, tmp_history):
        await tmp_history.record(decision="No sprint", cycle=1, phase="brainstorm")
        entries = await tmp_history.get_recent(5)
        assert "sprint" not in entries[0]

    async def test_sprint_summary(self, tmp_history):
        for i in range(3):
            metrics = PerformanceMetrics(
                cycle_duration_ms=1000 * (i + 1),
                cost_usd=0.1,
                tokens_used=1000,
                tasks_completed=2,
                tasks_committed=3,
            )
            await tmp_history.record(
                decision=f"Cycle {i+1}",
                cycle=i + 1,
                sprint=1,
                metrics=metrics,
            )
        # Add a cycle from sprint 2 (should not be counted)
        await tmp_history.record(
            decision="Sprint 2",
            cycle=4,
            sprint=2,
            metrics=PerformanceMetrics(cost_usd=0.5),
        )

        summary = await tmp_history.get_sprint_summary(1)
        assert summary.sprint == 1
        assert summary.cycles == 3
        assert summary.total_cost_usd == pytest.approx(0.3)
        assert summary.total_tokens == 3000
        assert summary.total_tasks_completed == 6
        assert summary.total_tasks_committed == 9
        assert summary.velocity == pytest.approx(6 / 9)

    async def test_sprint_summary_empty(self, tmp_history):
        summary = await tmp_history.get_sprint_summary(99)
        assert summary.cycles == 0
        assert summary.velocity == 0.0

    async def test_record_with_decision_type_and_alternatives(self, tmp_history):
        alts = [
            Alternative("Use Redis caching", "Added complexity not justified"),
            Alternative("Skip caching entirely", "Performance SLA requires it"),
        ]
        await tmp_history.record(
            decision="Implement in-memory LRU cache",
            rationale="Simplest approach that meets SLA",
            cycle=5,
            phase="execute",
            sprint=1,
            decision_type=DecisionType.SCOPE_CHANGE,
            alternatives=alts,
            impact="Reduces p99 latency by ~40%",
        )

        entries = await tmp_history.get_recent(5)
        assert len(entries) == 1
        e = entries[0]
        assert e["decision_type"] == "scope_change"
        assert len(e["alternatives"]) == 2
        assert e["alternatives"][0]["description"] == "Use Redis caching"
        assert e["alternatives"][0]["rejected_reason"] == "Added complexity not justified"
        assert e["impact"] == "Reduces p99 latency by ~40%"

    async def test_record_default_decision_type(self, tmp_history):
        await tmp_history.record(decision="Basic decision", cycle=1, phase="brainstorm")
        entries = await tmp_history.get_recent(5)
        assert entries[0]["decision_type"] == "general"

    async def test_record_no_alternatives_omits_field(self, tmp_history):
        await tmp_history.record(
            decision="Simple choice",
            cycle=2,
            decision_type=DecisionType.GO_NO_GO,
        )
        entries = await tmp_history.get_recent(5)
        assert "alternatives" not in entries[0]


# =====================================================================
# WBS task decomposition
# =====================================================================


class TestWBSTaskBreakdown:
    def test_add_and_retrieve_nodes(self):
        tb = TaskBreakdown(sprint_goal="Launch MVP")
        tb.add(WBSNode(id="1", title="Feature A"))
        tb.add(WBSNode(id="1.1", title="Design API", parent_id="1"))
        tb.add(WBSNode(id="1.2", title="Implement API", parent_id="1"))

        assert len(tb.all_nodes) == 3
        assert tb.get("1.1").title == "Design API"

    def test_roots_and_children(self):
        tb = TaskBreakdown()
        tb.add(WBSNode(id="1", title="A"))
        tb.add(WBSNode(id="2", title="B"))
        tb.add(WBSNode(id="1.1", title="A1", parent_id="1"))

        roots = tb.roots()
        assert len(roots) == 2
        assert tb.children("1") == [tb.get("1.1")]
        assert tb.children("2") == []

    def test_leaves(self):
        tb = TaskBreakdown()
        tb.add(WBSNode(id="1", title="Parent"))
        tb.add(WBSNode(id="1.1", title="Child", parent_id="1"))
        tb.add(WBSNode(id="2", title="Standalone"))

        leaves = tb.leaves()
        ids = {n.id for n in leaves}
        assert ids == {"1.1", "2"}

    def test_completion_pct(self):
        tb = TaskBreakdown()
        tb.add(WBSNode(id="1", title="Parent"))
        tb.add(WBSNode(id="1.1", title="Done", parent_id="1", status=TaskStatus.DONE))
        tb.add(WBSNode(id="1.2", title="Pending", parent_id="1", status=TaskStatus.PENDING))
        tb.add(WBSNode(id="2", title="Done too", status=TaskStatus.DONE))

        # Leaves: 1.1 (done), 1.2 (pending), 2 (done) → 2/3
        assert tb.completion_pct() == pytest.approx(2 / 3)

    def test_completion_pct_empty(self):
        tb = TaskBreakdown()
        assert tb.completion_pct() == 0.0

    def test_by_status(self):
        tb = TaskBreakdown()
        tb.add(WBSNode(id="1", title="A", status=TaskStatus.BLOCKED))
        tb.add(WBSNode(id="2", title="B", status=TaskStatus.DONE))
        tb.add(WBSNode(id="3", title="C", status=TaskStatus.BLOCKED))

        blocked = tb.by_status(TaskStatus.BLOCKED)
        assert len(blocked) == 2

    def test_by_assignee(self):
        tb = TaskBreakdown()
        tb.add(WBSNode(id="1", title="A", assignee="builder"))
        tb.add(WBSNode(id="2", title="B", assignee="tester"))
        tb.add(WBSNode(id="3", title="C", assignee="builder"))

        builder_tasks = tb.by_assignee("builder")
        assert len(builder_tasks) == 2

    def test_total_estimated_and_actual_cycles(self):
        tb = TaskBreakdown()
        tb.add(WBSNode(id="1", title="Parent"))
        tb.add(WBSNode(id="1.1", title="A", parent_id="1", estimated_cycles=3, actual_cycles=2))
        tb.add(WBSNode(id="1.2", title="B", parent_id="1", estimated_cycles=2, actual_cycles=3))

        assert tb.total_estimated_cycles() == 5
        assert tb.total_actual_cycles() == 5

    def test_remove_node(self):
        tb = TaskBreakdown()
        tb.add(WBSNode(id="1", title="A"))
        removed = tb.remove("1")
        assert removed.title == "A"
        assert tb.get("1") is None
        assert tb.remove("nonexistent") is None

    def test_serialization_round_trip(self):
        tb = TaskBreakdown(sprint_goal="Ship v1")
        tb.add(WBSNode(id="1", title="Feature", assignee="builder", okr_link="O1/KR1"))
        tb.add(WBSNode(id="1.1", title="Subtask", parent_id="1", status=TaskStatus.DONE))

        data = tb.to_list()
        tb2 = TaskBreakdown.from_list(data, sprint_goal="Ship v1")
        assert len(tb2.all_nodes) == 2
        assert tb2.get("1.1").status == TaskStatus.DONE
        assert tb2.get("1").okr_link == "O1/KR1"

    def test_markdown_round_trip(self):
        tb = TaskBreakdown(sprint_goal="Launch MVP")
        tb.add(WBSNode(id="1", title="Feature A", assignee="builder", okr_link="O1/KR1"))
        tb.add(WBSNode(id="1.1", title="Design API", parent_id="1", status=TaskStatus.DONE))
        tb.add(WBSNode(id="1.2", title="Implement", parent_id="1", status=TaskStatus.IN_PROGRESS, assignee="worker"))
        tb.add(WBSNode(id="2", title="Feature B", status=TaskStatus.BLOCKED))

        md = tb.to_markdown()
        assert "Launch MVP" in md
        assert "[x]" in md  # DONE marker
        assert "[~]" in md  # IN_PROGRESS marker
        assert "[!]" in md  # BLOCKED marker

        # Parse back
        tb2 = TaskBreakdown.from_markdown(md)
        assert tb2.sprint_goal == "Launch MVP"
        assert len(tb2.all_nodes) == 4
        assert tb2.get("1.1").status == TaskStatus.DONE
        assert tb2.get("1.2").assignee == "worker"
        assert tb2.get("1").okr_link == "O1/KR1"

    def test_okr_link_on_nodes(self):
        tb = TaskBreakdown()
        tb.add(WBSNode(id="1", title="Aligned task", okr_link="O1/KR2"))
        assert tb.get("1").okr_link == "O1/KR2"


# =====================================================================
# OKR-Sprint alignment validation
# =====================================================================


class TestOKRSprintAlignment:
    CONSENSUS_WITH_OKR = (
        "## OKR\n"
        "### O1: Launch MVP by Q2\n"
        "- KR1: Complete core features [progress: 0.8]\n"
        "### O2: Grow user base\n"
        "- KR1: Reach 1000 signups [progress: 0.5]\n\n"
        "## Next Action\nShip it\n"
    )

    def test_aligned_sprint_goal(self):
        issues = ConsensusMemory.validate_sprint_okr_alignment(
            self.CONSENSUS_WITH_OKR,
            "Complete O1 features and begin O2 outreach",
        )
        assert issues == []

    def test_empty_sprint_goal(self):
        issues = ConsensusMemory.validate_sprint_okr_alignment(
            self.CONSENSUS_WITH_OKR, ""
        )
        assert any("empty" in i.lower() for i in issues)

    def test_no_okr_reference_in_goal(self):
        issues = ConsensusMemory.validate_sprint_okr_alignment(
            self.CONSENSUS_WITH_OKR,
            "Just do some stuff",
        )
        assert any("does not reference" in i for i in issues)

    def test_unknown_objective_reference(self):
        issues = ConsensusMemory.validate_sprint_okr_alignment(
            self.CONSENSUS_WITH_OKR,
            "Work on O1 and O99",
        )
        assert any("O99" in i for i in issues)

    def test_no_okr_section_is_vacuously_ok(self):
        issues = ConsensusMemory.validate_sprint_okr_alignment(
            "## Next Action\nStuff\n",
            "Sprint goal without OKR section",
        )
        assert issues == []


# =====================================================================
# RoomConfig sprint context
# =====================================================================


class TestRoomConfigSprintContext:
    def test_default_no_sprint(self):
        cfg = RoomConfig()
        assert cfg.sprint == 0
        assert cfg.sprint_goal == ""
        assert cfg.okr_ids == []

    def test_sprint_context_fields(self):
        cfg = RoomConfig(
            sprint=2,
            sprint_goal="Deliver auth module (O1)",
            okr_ids=["O1", "O1/KR1", "O1/KR2"],
        )
        assert cfg.sprint == 2
        assert cfg.sprint_goal == "Deliver auth module (O1)"
        assert "O1/KR1" in cfg.okr_ids

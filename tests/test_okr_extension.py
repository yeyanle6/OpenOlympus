"""Tests for OKR field extension, decision log enhancements, and JSON Schema.

Covers:
- Initiative layer in OKR hierarchy (Objective → KeyResult → Initiative)
- Confidence and impact_scope fields in decision log
- JSON Schema definitions and lightweight validation
- Compatibility with existing consensus history archiving
"""

import pytest

from olympus.memory.consensus import (
    ConsensusMemory,
    Initiative,
    KeyResult,
    Objective,
)
from olympus.memory.history import (
    Alternative,
    Confidence,
    DecisionHistory,
    DecisionType,
    ImpactScope,
    PerformanceMetrics,
)
from olympus.memory.schemas import (
    DECISION_ENTRY_SCHEMA,
    OKR_SCHEMA,
    okr_to_dicts,
    validate_decision_entry,
    validate_okr,
)


# =====================================================================
# Initiative layer in OKR hierarchy
# =====================================================================


class TestInitiativeLayer:
    CONSENSUS_WITH_INITIATIVES = (
        "## OKR\n"
        "### O1: Launch MVP by Q2\n"
        "- KR1: Complete core features [progress: 0.8]\n"
        "  - I1: Build auth module [status: done] @builder\n"
        "  - I2: Implement API endpoints [status: in_progress] @worker\n"
        "- KR2: Pass security audit [progress: 0.3]\n"
        "  - I1: Run OWASP scan [status: pending]\n"
        "### O2: Grow user base\n"
        "- KR1: Reach 1000 signups [progress: 0.5]\n"
    )

    def test_parse_initiatives(self):
        okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_WITH_INITIATIVES)
        kr1 = okrs[0].key_results[0]
        assert len(kr1.initiatives) == 2
        assert kr1.initiatives[0].id == "I1"
        assert kr1.initiatives[0].description == "Build auth module"
        assert kr1.initiatives[0].status == "done"
        assert kr1.initiatives[0].owner == "builder"

    def test_initiative_status_parsing(self):
        okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_WITH_INITIATIVES)
        kr1 = okrs[0].key_results[0]
        assert kr1.initiatives[1].status == "in_progress"
        assert kr1.initiatives[1].owner == "worker"

    def test_initiative_default_status(self):
        consensus = (
            "## OKR\n"
            "### O1: Test\n"
            "- KR1: Something [progress: 0.5]\n"
            "  - I1: No status tag\n"
        )
        okrs = ConsensusMemory.extract_okrs(consensus)
        assert okrs[0].key_results[0].initiatives[0].status == "pending"

    def test_initiative_no_owner(self):
        okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_WITH_INITIATIVES)
        kr2 = okrs[0].key_results[1]
        assert len(kr2.initiatives) == 1
        assert kr2.initiatives[0].owner == ""

    def test_kr_without_initiatives(self):
        okrs = ConsensusMemory.extract_okrs(self.CONSENSUS_WITH_INITIATIVES)
        # O2/KR1 has no initiatives
        assert okrs[1].key_results[0].initiatives == []

    def test_existing_okr_parsing_still_works(self):
        """Ensure the Initiative extension doesn't break existing OKR parsing."""
        consensus = (
            "## OKR\n"
            "### O1: Launch MVP\n"
            "- KR1: Features [progress: 0.8]\n"
            "- KR2: Audit [progress: 0.3]\n"
        )
        okrs = ConsensusMemory.extract_okrs(consensus)
        assert len(okrs) == 1
        assert len(okrs[0].key_results) == 2
        assert okrs[0].key_results[0].progress == pytest.approx(0.8)

    def test_validate_duplicate_initiative(self):
        consensus = (
            "## OKR\n"
            "### O1: Test\n"
            "- KR1: A [progress: 0.5]\n"
            "  - I1: First [status: done]\n"
            "  - I1: Duplicate [status: pending]\n"
        )
        issues = ConsensusMemory.validate_okr_section(consensus)
        assert any("I1" in i and "duplicated" in i for i in issues)

    def test_validate_invalid_initiative_status(self):
        consensus = (
            "## OKR\n"
            "### O1: Test\n"
            "- KR1: A [progress: 0.5]\n"
            "  - I1: Bad status [status: unknown_status]\n"
        )
        issues = ConsensusMemory.validate_okr_section(consensus)
        assert any("invalid status" in i for i in issues)

    def test_validate_valid_initiatives(self):
        issues = ConsensusMemory.validate_okr_section(
            self.CONSENSUS_WITH_INITIATIVES
        )
        assert issues == []


# =====================================================================
# Initiative dataclass
# =====================================================================


class TestInitiativeDataclass:
    def test_initiative_defaults(self):
        init = Initiative(id="I1", description="Test task")
        assert init.status == "pending"
        assert init.owner == ""

    def test_initiative_full(self):
        init = Initiative(
            id="I1", description="Build it", status="done", owner="builder"
        )
        assert init.id == "I1"
        assert init.status == "done"
        assert init.owner == "builder"

    def test_key_result_with_initiatives(self):
        kr = KeyResult(
            id="KR1",
            description="Test",
            progress=0.5,
            initiatives=[
                Initiative(id="I1", description="A"),
                Initiative(id="I2", description="B", status="done"),
            ],
        )
        assert len(kr.initiatives) == 2
        assert kr.initiatives[1].status == "done"


# =====================================================================
# Decision log: confidence and impact_scope
# =====================================================================


class TestDecisionConfidenceAndScope:
    @pytest.fixture
    def tmp_history(self, tmp_path):
        return DecisionHistory(path=tmp_path / "decisions.jsonl")

    async def test_record_with_confidence_and_scope(self, tmp_history):
        await tmp_history.record(
            decision="Adopt microservice architecture",
            rationale="Better scalability",
            cycle=5,
            phase="evaluate",
            decision_type=DecisionType.SCOPE_CHANGE,
            confidence=Confidence.HIGH,
            impact_scope=ImpactScope.BROAD,
        )
        entries = await tmp_history.get_recent(5)
        assert len(entries) == 1
        assert entries[0]["confidence"] == "high"
        assert entries[0]["impact_scope"] == "broad"

    async def test_confidence_omitted_when_none(self, tmp_history):
        await tmp_history.record(decision="Simple", cycle=1)
        entries = await tmp_history.get_recent(5)
        assert "confidence" not in entries[0]
        assert "impact_scope" not in entries[0]

    async def test_confidence_enum_values(self):
        assert Confidence.HIGH.value == "high"
        assert Confidence.MEDIUM.value == "medium"
        assert Confidence.LOW.value == "low"

    async def test_impact_scope_enum_values(self):
        assert ImpactScope.NARROW.value == "narrow"
        assert ImpactScope.MODERATE.value == "moderate"
        assert ImpactScope.BROAD.value == "broad"

    async def test_full_decision_record(self, tmp_history):
        """Test a decision record with all fields populated."""
        await tmp_history.record(
            decision="Switch to PostgreSQL",
            rationale="Need JSONB support",
            cycle=3,
            phase="execute",
            agents=["architect", "builder"],
            sprint=2,
            sprint_goal="Complete O1 data layer",
            metrics=PerformanceMetrics(
                cycle_duration_ms=3000,
                cost_usd=0.05,
                tokens_used=2000,
            ),
            decision_type=DecisionType.PIVOT,
            alternatives=[
                Alternative("Keep SQLite", "Cannot handle concurrent writes"),
            ],
            impact="All data access patterns change",
            confidence=Confidence.MEDIUM,
            impact_scope=ImpactScope.MODERATE,
        )
        entries = await tmp_history.get_recent(5)
        e = entries[0]
        assert e["decision_type"] == "pivot"
        assert e["confidence"] == "medium"
        assert e["impact_scope"] == "moderate"
        assert e["impact"] == "All data access patterns change"
        assert len(e["alternatives"]) == 1


# =====================================================================
# JSON Schema definitions
# =====================================================================


class TestOKRSchema:
    def test_schema_structure(self):
        assert OKR_SCHEMA["type"] == "array"
        assert "items" in OKR_SCHEMA
        obj_props = OKR_SCHEMA["items"]["properties"]
        assert "id" in obj_props
        assert "key_results" in obj_props
        kr_props = obj_props["key_results"]["items"]["properties"]
        assert "initiatives" in kr_props
        init_props = kr_props["initiatives"]["items"]["properties"]
        assert "status" in init_props
        assert "owner" in init_props

    def test_validate_okr_valid(self):
        data = [
            {
                "id": "O1",
                "description": "Launch MVP",
                "key_results": [
                    {
                        "id": "KR1",
                        "description": "Core features",
                        "progress": 0.8,
                        "initiatives": [
                            {"id": "I1", "description": "Build auth", "status": "done", "owner": "builder"},
                        ],
                    },
                ],
            },
        ]
        assert validate_okr(data) == []

    def test_validate_okr_missing_key_results(self):
        data = [{"id": "O1", "description": "Test", "key_results": []}]
        issues = validate_okr(data)
        assert any("at least one" in i for i in issues)

    def test_validate_okr_duplicate_objective(self):
        data = [
            {"id": "O1", "description": "A", "key_results": [{"id": "KR1", "description": "x", "progress": 0.0}]},
            {"id": "O1", "description": "B", "key_results": [{"id": "KR1", "description": "y", "progress": 0.0}]},
        ]
        issues = validate_okr(data)
        assert any("duplicate" in i for i in issues)

    def test_validate_okr_progress_out_of_range(self):
        data = [
            {
                "id": "O1",
                "description": "Test",
                "key_results": [{"id": "KR1", "description": "x", "progress": 1.5}],
            },
        ]
        issues = validate_okr(data)
        assert any("progress" in i for i in issues)

    def test_validate_okr_invalid_initiative_status(self):
        data = [
            {
                "id": "O1",
                "description": "Test",
                "key_results": [
                    {
                        "id": "KR1",
                        "description": "x",
                        "progress": 0.5,
                        "initiatives": [
                            {"id": "I1", "description": "bad", "status": "invalid"},
                        ],
                    },
                ],
            },
        ]
        issues = validate_okr(data)
        assert any("invalid status" in i for i in issues)

    def test_validate_okr_not_a_list(self):
        issues = validate_okr("not a list")
        assert issues == ["OKR data must be a list of objectives"]


class TestDecisionEntrySchema:
    def test_schema_has_new_fields(self):
        props = DECISION_ENTRY_SCHEMA["properties"]
        assert "confidence" in props
        assert props["confidence"]["enum"] == ["high", "medium", "low"]
        assert "impact_scope" in props
        assert props["impact_scope"]["enum"] == ["narrow", "moderate", "broad"]

    def test_validate_valid_entry(self):
        entry = {
            "timestamp": "2026-03-24T12:00:00+00:00",
            "decision": "Use PostgreSQL",
            "decision_type": "pivot",
            "confidence": "high",
            "impact_scope": "broad",
        }
        assert validate_decision_entry(entry) == []

    def test_validate_missing_decision(self):
        entry = {"timestamp": "2026-03-24T12:00:00+00:00", "decision_type": "general"}
        issues = validate_decision_entry(entry)
        assert any("decision" in i for i in issues)

    def test_validate_invalid_confidence(self):
        entry = {
            "timestamp": "2026-03-24T12:00:00+00:00",
            "decision": "Test",
            "decision_type": "general",
            "confidence": "very_high",
        }
        issues = validate_decision_entry(entry)
        assert any("confidence" in i for i in issues)

    def test_validate_invalid_impact_scope(self):
        entry = {
            "timestamp": "2026-03-24T12:00:00+00:00",
            "decision": "Test",
            "decision_type": "general",
            "impact_scope": "huge",
        }
        issues = validate_decision_entry(entry)
        assert any("impact_scope" in i for i in issues)

    def test_validate_invalid_decision_type(self):
        entry = {
            "timestamp": "2026-03-24T12:00:00+00:00",
            "decision": "Test",
            "decision_type": "invalid_type",
        }
        issues = validate_decision_entry(entry)
        assert any("decision_type" in i for i in issues)

    def test_validate_minimal_valid_entry(self):
        entry = {
            "timestamp": "2026-03-24T12:00:00+00:00",
            "decision": "Do something",
            "decision_type": "general",
        }
        assert validate_decision_entry(entry) == []


# =====================================================================
# okr_to_dicts bridge function
# =====================================================================


class TestOkrToDicts:
    def test_round_trip_with_initiatives(self):
        consensus = (
            "## OKR\n"
            "### O1: Launch MVP\n"
            "- KR1: Features [progress: 0.8]\n"
            "  - I1: Build auth [status: done] @builder\n"
            "- KR2: Audit [progress: 0.3]\n"
        )
        okrs = ConsensusMemory.extract_okrs(consensus)
        dicts = okr_to_dicts(okrs)

        # Validate with our schema validator
        assert validate_okr(dicts) == []

        # Check structure
        assert len(dicts) == 1
        assert dicts[0]["id"] == "O1"
        assert len(dicts[0]["key_results"]) == 2
        kr1 = dicts[0]["key_results"][0]
        assert kr1["progress"] == pytest.approx(0.8)
        assert len(kr1["initiatives"]) == 1
        assert kr1["initiatives"][0]["id"] == "I1"
        assert kr1["initiatives"][0]["status"] == "done"
        assert kr1["initiatives"][0]["owner"] == "builder"

    def test_round_trip_without_initiatives(self):
        consensus = (
            "## OKR\n"
            "### O1: Test\n"
            "- KR1: Something [progress: 0.5]\n"
        )
        okrs = ConsensusMemory.extract_okrs(consensus)
        dicts = okr_to_dicts(okrs)
        assert "initiatives" not in dicts[0]["key_results"][0]


# =====================================================================
# History archiving compatibility
# =====================================================================


class TestArchivingCompatibility:
    """Ensure the OKR extension works with consensus history archiving."""

    @pytest.fixture
    def consensus(self, tmp_path):
        return ConsensusMemory(
            path=tmp_path / "consensus.md",
            history_dir=tmp_path / "history",
        )

    async def test_write_and_archive_with_initiatives(self, consensus):
        content = (
            "## OKR\n"
            "### O1: Test\n"
            "- KR1: Feature [progress: 0.5]\n"
            "  - I1: Build it [status: in_progress] @builder\n"
            "## Next Action\nDo stuff\n"
        )
        await consensus.write(content)
        # Write again to trigger archive
        await consensus.write(content + "\n## Updated\n")

        history = await consensus.get_history(limit=5)
        assert len(history) == 1
        ts, archived = history[0]
        # Archived content should contain the initiative data
        assert "I1: Build it" in archived

    async def test_okr_extraction_from_archived(self, consensus):
        content = (
            "## OKR\n"
            "### O1: Test\n"
            "- KR1: Feature [progress: 0.7]\n"
            "  - I1: Task A [status: done] @worker\n"
        )
        await consensus.write(content)
        await consensus.write("## Empty\n")

        history = await consensus.get_history(limit=5)
        _, archived = history[0]
        okrs = ConsensusMemory.extract_okrs(archived)
        assert len(okrs) == 1
        assert okrs[0].key_results[0].initiatives[0].status == "done"

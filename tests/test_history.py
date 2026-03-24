"""Tests for decision history."""

import pytest

from olympus.memory.history import DecisionHistory


@pytest.fixture
def tmp_history(tmp_path):
    return DecisionHistory(path=tmp_path / "decisions.jsonl")


async def test_record_and_get(tmp_history):
    await tmp_history.record("Launch MVP", cycle=1, phase="execute")
    await tmp_history.record("Add auth", cycle=2, phase="execute")

    entries = await tmp_history.get_recent(10)
    assert len(entries) == 2
    assert entries[0]["decision"] == "Launch MVP"
    assert entries[1]["decision"] == "Add auth"


async def test_get_recent_empty(tmp_history):
    entries = await tmp_history.get_recent()
    assert entries == []


async def test_get_recent_with_limit(tmp_history):
    for i in range(5):
        await tmp_history.record(f"Decision {i}", cycle=i)

    entries = await tmp_history.get_recent(limit=2)
    assert len(entries) == 2
    assert entries[0]["decision"] == "Decision 3"


async def test_search(tmp_history):
    await tmp_history.record("Build landing page")
    await tmp_history.record("Setup CI/CD pipeline")
    await tmp_history.record("Fix landing page bug")

    results = await tmp_history.search("landing")
    assert len(results) == 2

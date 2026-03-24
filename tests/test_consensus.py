"""Tests for consensus memory with history."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from olympus.memory.consensus import ConsensusMemory


@pytest.fixture
def tmp_consensus(tmp_path):
    return ConsensusMemory(
        path=tmp_path / "consensus.md",
        history_dir=tmp_path / "history",
    )


async def test_write_and_read(tmp_consensus):
    await tmp_consensus.write("# Test\n\nHello")
    content = await tmp_consensus.read()
    assert "Hello" in content


async def test_read_empty(tmp_consensus):
    content = await tmp_consensus.read()
    assert content == ""


async def test_write_archives_previous(tmp_consensus):
    await tmp_consensus.write("Version 1")
    await asyncio.sleep(1.1)  # Ensure different timestamp
    await tmp_consensus.write("Version 2")

    content = await tmp_consensus.read()
    assert content == "Version 2"

    history = await tmp_consensus.get_history()
    assert len(history) == 1
    assert history[0][1] == "Version 1"


async def test_backup_and_restore(tmp_consensus):
    await tmp_consensus.write("Original content")
    await tmp_consensus.backup()
    await tmp_consensus.write("Bad content")

    restored = await tmp_consensus.restore()
    assert restored is True

    content = await tmp_consensus.read()
    assert content == "Original content"


async def test_restore_without_backup(tmp_consensus):
    result = await tmp_consensus.restore()
    assert result is False


async def test_has_changed_since_backup(tmp_consensus):
    await tmp_consensus.write("Content A")
    await tmp_consensus.backup()
    await tmp_consensus.write("Content B")

    changed = await tmp_consensus.has_changed_since_backup()
    assert changed is True

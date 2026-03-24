"""Tests for stagnation detection."""

from olympus.loop.stagnation import StagnationDetector


def test_not_stagnant_initially():
    sd = StagnationDetector()
    assert not sd.is_stagnant()


def test_stagnant_after_repeated_actions():
    sd = StagnationDetector(threshold=2)
    sd.record_action("Build the MVP")
    sd.record_action("Build the MVP")
    assert sd.is_stagnant()


def test_not_stagnant_with_different_actions():
    sd = StagnationDetector(threshold=2)
    sd.record_action("Build the MVP")
    sd.record_action("Deploy to production")
    assert not sd.is_stagnant()


def test_stagnant_normalizes_whitespace():
    sd = StagnationDetector(threshold=2)
    sd.record_action("Build  the   MVP")
    sd.record_action("build the mvp")
    assert sd.is_stagnant()


def test_warning_message_when_stagnant():
    sd = StagnationDetector(threshold=2)
    sd.record_action("Ship it")
    sd.record_action("Ship it")
    warning = sd.get_warning()
    assert "Stagnation" in warning
    assert "ship it" in warning


def test_no_warning_when_not_stagnant():
    sd = StagnationDetector()
    assert sd.get_warning() == ""


def test_extract_next_action():
    consensus = """# Consensus

## What We Did
- stuff

## Next Action
Build the landing page

## Company State
- Product: MVP
"""
    action = StagnationDetector.extract_next_action(consensus)
    assert action == "Build the landing page"


def test_extract_next_action_missing():
    assert StagnationDetector.extract_next_action("no sections here") == ""

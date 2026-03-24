"""Tests for convergence controller."""

from olympus.loop.convergence import ConvergenceController, Phase


def test_phase_cycle_1_is_brainstorm():
    cc = ConvergenceController()
    assert cc.get_phase(1) == Phase.BRAINSTORM


def test_phase_cycle_2_is_evaluate():
    cc = ConvergenceController()
    assert cc.get_phase(2) == Phase.EVALUATE


def test_phase_cycle_3_plus_is_execute():
    cc = ConvergenceController()
    assert cc.get_phase(3) == Phase.EXECUTE
    assert cc.get_phase(4) == Phase.EXECUTE


def test_phase_retrospect_every_5_cycles():
    cc = ConvergenceController(retrospect_interval=5)
    assert cc.get_phase(5) == Phase.RETROSPECT
    assert cc.get_phase(10) == Phase.RETROSPECT
    assert cc.get_phase(6) == Phase.EXECUTE


def test_phase_rules_not_empty():
    cc = ConvergenceController()
    for phase in Phase:
        rules = cc.get_phase_rules(phase)
        assert len(rules) > 0
        assert "##" in rules

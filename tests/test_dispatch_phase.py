from __future__ import annotations

import pytest

from basicly import dispatch_phase, run_record


def test_a_write_phase_is_an_agent_doing_a_nodes_work() -> None:

    assert set(dispatch_phase.WRITE_PHASES) == {
        dispatch_phase.BUILD_PHASE,
        dispatch_phase.LANE_PHASE,
    }
    assert dispatch_phase.is_write_phase(dispatch_phase.BUILD_PHASE)
    assert dispatch_phase.is_write_phase(dispatch_phase.LANE_PHASE)


def test_a_helper_dispatch_is_not_a_write_phase() -> None:

    assert not dispatch_phase.is_write_phase(dispatch_phase.VALIDATE_PHASE)
    assert not dispatch_phase.is_write_phase(dispatch_phase.DECIDE_PHASE)
    assert not dispatch_phase.is_write_phase(dispatch_phase.PROPOSE_PHASE)


@pytest.mark.parametrize("recorded", [None, "", 1, True, [], {"phase": "lane"}, "LANE"])
def test_a_phase_that_cannot_be_read_is_not_evidence_that_a_lane_ran(recorded: object) -> None:

    assert not dispatch_phase.is_write_phase(recorded)


def test_every_phase_name_is_distinct() -> None:
    names = (
        dispatch_phase.BUILD_PHASE,
        dispatch_phase.LANE_PHASE,
        dispatch_phase.VALIDATE_PHASE,
        dispatch_phase.DECIDE_PHASE,
        dispatch_phase.PROPOSE_PHASE,
    )

    assert len(set(names)) == len(names)


def test_run_record_re_exports_the_same_objects() -> None:

    assert run_record.BUILD_PHASE is dispatch_phase.BUILD_PHASE
    assert run_record.LANE_PHASE is dispatch_phase.LANE_PHASE
    assert run_record.VALIDATE_PHASE is dispatch_phase.VALIDATE_PHASE
    assert run_record.DECIDE_PHASE is dispatch_phase.DECIDE_PHASE
    assert run_record.PROPOSE_PHASE is dispatch_phase.PROPOSE_PHASE
    assert run_record.WRITE_PHASES is dispatch_phase.WRITE_PHASES
    assert run_record.is_write_phase is dispatch_phase.is_write_phase

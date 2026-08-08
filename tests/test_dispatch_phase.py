"""One vocabulary for what phase a dispatch was recorded under.

Moved out of `test_run_record_spend.py` when the §9.4 naming gate was made binding
(basicly-u2hl.14). The defect this module was extracted to fix (basicly-tcmy.5) was not
a wrong value in either filter but that there were **two** of them — `decompose`'s lane
bound admitted only `lane` while the calibration admitted something else — so the tests
that pin the set belong with the definition, not with either consumer.

Asserted twice on purpose: once against :mod:`basicly.dispatch_phase`, which owns the
names, and once against the `run_record` re-exports, which is how every caller in the
tree spells them. A rename here that left the re-export behind would pass the first and
fail the second.
"""

from __future__ import annotations

import pytest

from basicly import dispatch_phase, run_record


def test_a_write_phase_is_an_agent_doing_a_nodes_work() -> None:
    """The interactive build and the supervised lane are the same kind of work.

    `loop._run_agent` records one and `supervise._dispatch_lane` the other, so a
    consumer asking what a lane costs wants both — bounding on `lane` alone read 24
    samples on this repo's history while 128 records of the documented default were
    invisible to it.
    """
    assert set(dispatch_phase.WRITE_PHASES) == {
        dispatch_phase.BUILD_PHASE,
        dispatch_phase.LANE_PHASE,
    }
    assert dispatch_phase.is_write_phase(dispatch_phase.BUILD_PHASE)
    assert dispatch_phase.is_write_phase(dispatch_phase.LANE_PHASE)


def test_a_helper_dispatch_is_not_a_write_phase() -> None:
    """A judge and a decider read and answer; neither writes code.

    A calibration that sampled them would measure the cost of a helper and report it
    as the cost of the work.
    """
    assert not dispatch_phase.is_write_phase(dispatch_phase.VALIDATE_PHASE)
    assert not dispatch_phase.is_write_phase(dispatch_phase.DECIDE_PHASE)
    assert not dispatch_phase.is_write_phase(dispatch_phase.PROPOSE_PHASE)


@pytest.mark.parametrize("recorded", [None, "", 1, True, [], {"phase": "lane"}, "LANE"])
def test_a_phase_that_cannot_be_read_is_not_evidence_that_a_lane_ran(recorded: object) -> None:
    """The argument is the raw persisted value, so every unreadable shape fails closed.

    Absent, null (every dispatch recorded before the field existed), a wrong type or a
    different casing all answer False rather than raising — the caller is reading a
    record off disk, where the alternative to answering is a traceback mid-report.
    """
    assert not dispatch_phase.is_write_phase(recorded)


def test_every_phase_name_is_distinct() -> None:
    """Two names collapsing onto one string would silently merge two populations."""
    names = (
        dispatch_phase.BUILD_PHASE,
        dispatch_phase.LANE_PHASE,
        dispatch_phase.VALIDATE_PHASE,
        dispatch_phase.DECIDE_PHASE,
        dispatch_phase.PROPOSE_PHASE,
    )

    assert len(set(names)) == len(names)


def test_run_record_re_exports_the_same_objects() -> None:
    """Every caller spells these `run_record.<NAME>`; the re-export is the live contract.

    Identity, not equality: two equal strings would pass while the module had quietly
    grown a second copy of the vocabulary, which is the defect being prevented.
    """
    assert run_record.BUILD_PHASE is dispatch_phase.BUILD_PHASE
    assert run_record.LANE_PHASE is dispatch_phase.LANE_PHASE
    assert run_record.VALIDATE_PHASE is dispatch_phase.VALIDATE_PHASE
    assert run_record.DECIDE_PHASE is dispatch_phase.DECIDE_PHASE
    assert run_record.PROPOSE_PHASE is dispatch_phase.PROPOSE_PHASE
    assert run_record.WRITE_PHASES is dispatch_phase.WRITE_PHASES
    assert run_record.is_write_phase is dispatch_phase.is_write_phase

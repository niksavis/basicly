"""Whether the band actually looked at a lane, and whether it says so.

The defect these pin is `basicly-jr0l.60`: a pass of lanes the band never measured
printed nothing at all, so it was indistinguishable at the surface from a pass where
every estimate fitted — and on the run that measured it, all four lanes were the former
and all four overran. So the assertions here are about the *reporting* of absence, not
about the arithmetic: a silent gate and a passing gate must never look the same.
"""

from __future__ import annotations

from basicly import decompose
from basicly.working_set import WorkingSetAdmission, band_coverage


def unsized(issue_id: str, absence: str = decompose.SCOPE_UNDECLARED) -> WorkingSetAdmission:
    """A lane the band could not measure, and the reason it could not."""
    return WorkingSetAdmission(
        issue_id=issue_id, sizing=None, violation=None, refused=False, absence=absence
    )


def test_a_lane_with_no_estimate_is_not_checked() -> None:
    """`checked` reads off the estimate, never off `violation`.

    `violation is None` answers "nothing was wrong", which is also exactly what a lane
    nobody measured looks like — conflating the two is the whole defect.
    """
    assert unsized("basicly-a").checked is False


def test_an_empty_pass_says_so_rather_than_reporting_nothing() -> None:
    """Zero lanes is a statement, not an absence of one — the same distinction again."""
    assert band_coverage(()) == "no lanes to check"


def test_a_pass_of_unmeasured_lanes_is_reported_as_never_checked() -> None:
    """The regression itself: this must not render as silence or as a clean pass."""
    coverage = band_coverage((unsized("basicly-a"), unsized("basicly-b")))
    assert "NEVER CHECKED" in coverage
    assert "basicly-a" in coverage
    assert "basicly-b" in coverage
    assert "checked:" not in coverage


def test_the_absence_reason_is_named_because_the_two_need_different_fixes() -> None:
    """An undeclared scope needs a bead edit; an unreadable one needs a corrected path."""
    coverage = band_coverage((
        unsized("basicly-a", decompose.SCOPE_UNDECLARED),
        unsized("basicly-b", decompose.SCOPE_UNREADABLE),
    ))
    assert decompose.SCOPE_UNDECLARED in coverage
    assert decompose.SCOPE_UNREADABLE in coverage


def test_lanes_are_grouped_by_absence_so_one_reason_lists_its_own_lanes() -> None:
    """Grouping is what makes the line actionable: one fix per group, not per lane."""
    coverage = band_coverage((
        unsized("basicly-a", decompose.SCOPE_UNDECLARED),
        unsized("basicly-b", decompose.SCOPE_UNREADABLE),
        unsized("basicly-c", decompose.SCOPE_UNDECLARED),
    ))
    undeclared = coverage.split(f"NEVER CHECKED ({decompose.SCOPE_UNDECLARED}): ")[1].split(";")[0]
    assert "basicly-a" in undeclared
    assert "basicly-c" in undeclared
    assert "basicly-b" not in undeclared

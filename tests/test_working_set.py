"""Whether the band actually looked at a lane, and whether it says so.

The defect these pin is `basicly-jr0l.60`: a pass of lanes the band never measured
printed nothing at all, so it was indistinguishable at the surface from a pass where
every estimate fitted — and on the run that measured it, all four lanes were the former
and all four overran. So the assertions here are about the *reporting* of absence, not
about the arithmetic: a silent gate and a passing gate must never look the same.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import br, decompose
from basicly.config import load_sizing_config
from basicly.working_set import WorkingSetAdmission, admit_working_set, band_coverage

if TYPE_CHECKING:
    import pytest


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


# --- The band prices what a lane reads, not what it owns (basicly-efw2) ------
#
# `## Scope` answers the merge collision gate, which wants every path the diff touches
# named; the band prices what the declaration reads. One field served both, so declaring
# honestly for the first inflated the second — measured at 78,709, then 197,646, then
# 245,466 inside a single landing, with the ceiling raised twice and the diff never
# changing width.

# Five modules at the read cap, so each contributes the same 4,000 tokens and the two
# declarations differ only in how many of them they name.
_OWNS = "".join(f"- `src/{name}.py`\n" for name in "abcde")
_READS = "## Working Set\n\n- `src/a.py`\n"


def _tree(repo: Path) -> None:
    (repo / "src").mkdir()
    for name in "abcde":
        (repo / "src" / f"{name}.py").write_text("x" * 16_000, encoding="utf-8")


def _admit(
    monkeypatch: pytest.MonkeyPatch, repo: Path, body: str, ceiling: int
) -> WorkingSetAdmission:
    """Size a bead whose description is *body* against a band ending at *ceiling*."""
    record = {"issue_type": "task", "description": body}
    monkeypatch.setattr(br, "read_record", lambda _r, _b: record)
    sizing = replace(load_sizing_config(repo), working_set_max=ceiling)
    return admit_working_set(repo, "basicly-a", sizing)


def test_completing_a_scope_leaves_the_band_verdict_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC: one diff, two declaration completenesses, one estimate and one verdict."""
    _tree(tmp_path)
    ceiling = decompose.instruction_overhead(tmp_path) + 16_000
    narrow = _admit(monkeypatch, tmp_path, f"## Scope\n\n- `src/a.py`\n\n{_READS}", ceiling)
    complete = _admit(monkeypatch, tmp_path, f"## Scope\n\n{_OWNS}\n{_READS}", ceiling)

    assert narrow.sizing is not None and complete.sizing is not None
    assert narrow.sizing.estimate.total == complete.sizing.estimate.total
    assert (narrow.violation, complete.violation, complete.refused) == (None, None, False)


def test_the_same_completion_still_refuses_when_nothing_declares_a_working_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control: the fallback reproduces the defect, so the test above is not inert.

    It also pins the remedy. The operator who raised the ceiling twice in one landing was
    told to split a lane that had not grown; the refusal must name the other way out.
    """
    _tree(tmp_path)
    ceiling = decompose.instruction_overhead(tmp_path) + 16_000
    refused = _admit(monkeypatch, tmp_path, f"## Scope\n\n{_OWNS}", ceiling)

    assert refused.refused is True
    assert refused.violation is not None
    assert decompose.WORKING_SET_HEADING in refused.violation

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import decompose, tracker
from basicly.config import load_sizing_config
from basicly.working_set import WorkingSetAdmission, admit_working_set, band_coverage

if TYPE_CHECKING:
    import pytest


def unsized(issue_id: str, absence: str = decompose.SCOPE_UNDECLARED) -> WorkingSetAdmission:
    return WorkingSetAdmission(
        issue_id=issue_id, sizing=None, violation=None, refused=False, absence=absence
    )


def test_a_lane_with_no_estimate_is_not_checked() -> None:

    assert unsized("basicly-a").checked is False


def test_an_empty_pass_says_so_rather_than_reporting_nothing() -> None:
    assert band_coverage(()) == "no lanes to check"


def test_a_pass_of_unmeasured_lanes_is_reported_as_never_checked() -> None:
    coverage = band_coverage((unsized("basicly-a"), unsized("basicly-b")))
    assert "NEVER CHECKED" in coverage
    assert "basicly-a" in coverage
    assert "basicly-b" in coverage
    assert "checked:" not in coverage


def test_the_absence_reason_is_named_because_the_two_need_different_fixes() -> None:
    coverage = band_coverage((
        unsized("basicly-a", decompose.SCOPE_UNDECLARED),
        unsized("basicly-b", decompose.SCOPE_UNREADABLE),
    ))
    assert decompose.SCOPE_UNDECLARED in coverage
    assert decompose.SCOPE_UNREADABLE in coverage


def test_lanes_are_grouped_by_absence_so_one_reason_lists_its_own_lanes() -> None:
    coverage = band_coverage((
        unsized("basicly-a", decompose.SCOPE_UNDECLARED),
        unsized("basicly-b", decompose.SCOPE_UNREADABLE),
        unsized("basicly-c", decompose.SCOPE_UNDECLARED),
    ))
    undeclared = coverage.split(f"NEVER CHECKED ({decompose.SCOPE_UNDECLARED}): ")[1].split(";")[0]
    assert "basicly-a" in undeclared
    assert "basicly-c" in undeclared
    assert "basicly-b" not in undeclared


_OWNS = "".join(f"- `src/{name}.py`\n" for name in "abcde")
_READS = "## Working Set\n\n- `src/a.py`\n"


def _tree(repo: Path) -> None:
    (repo / "src").mkdir()
    for name in "abcde":
        (repo / "src" / f"{name}.py").write_text("x" * 16_000, encoding="utf-8")


def _admit(
    monkeypatch: pytest.MonkeyPatch, repo: Path, body: str, ceiling: int
) -> WorkingSetAdmission:
    record = {"issue_type": "task", "description": body}
    monkeypatch.setattr(tracker, "read_record", lambda _r, _b: record)
    sizing = replace(load_sizing_config(repo), working_set_max=ceiling)
    return admit_working_set(repo, "basicly-a", sizing)


def test_completing_a_scope_leaves_the_band_verdict_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    _tree(tmp_path)
    ceiling = decompose.instruction_overhead(tmp_path) + 16_000
    refused = _admit(monkeypatch, tmp_path, f"## Scope\n\n{_OWNS}", ceiling)

    assert refused.refused is True
    assert refused.violation is not None
    assert decompose.WORKING_SET_HEADING in refused.violation

"""Tests for the one-source capability status view (basicly-e2mz.37).

The defect was two documents maintained over the same capabilities. Three gradings of the
tool-call boundary were live at once, one of them `designed` for four hooks another row
called `shipped`, because the rule that kept the copies in step was prose and nothing ran
it. So the tests that matter are the ones a *disabled* claim would pass: a hand-edited
table, a capability graded twice, and a status column put back into the architecture
document all have to come back red.

The discriminator gets its own test. Refusing every `Status` column would refuse the
decision-record index too, and a gate that cries wolf gets switched off rather than
obeyed — so what is refused is a column carrying a *component state*, and §38's
`accepted` is proven to pass while the parser is proven to have read it.

Every mutation runs against a per-test copy of the repo through ``--root``, so nothing
here writes to the checkout it is testing. The boundary against ``test_docs_claims`` is
subject, not layer: that file owns the gate's own mechanics — splicing, line endings,
``--block`` scoping — and this one owns the status claim.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from tests.doc_blocks import block_body, cells

if TYPE_CHECKING:
    import pytest

REPO = Path(__file__).resolve().parents[1]
ARCHITECTURE_MD = "docs/architecture/architecture.md"
STATUS_MD = "docs/architecture/status.md"
STATUS_SOURCE = "docs/architecture/status.yaml"

# The §1.1 table, before and after a `Status` column is put back on it. The header alone is
# not enough: a graded column no row is long enough to fill is nothing to report.
_UNGRADED = (
    "| Failure | Why it happens | Remedy |\n| --- | --- | --- |",
    "| The agent ignores a rule it read | Guidance is a suggestion |"
    " A gate. A script that runs whether or not anyone asked, and refuses |",
)
_GRADED = (
    "| Failure | Why it happens | Remedy | Status |\n| --- | --- | --- | --- |",
    "| The agent ignores a rule it read | Guidance is a suggestion |"
    " A gate. A script that runs whether or not anyone asked, and refuses | shipped |",
)


def _load_module():
    """Load the docs-claims script module from its path (it is not a package)."""
    script_path = REPO / ".scripts" / "docs_claims.py"
    spec = importlib.util.spec_from_file_location("docs_claims", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


claims = _load_module()
status = claims.status_view


def _run(root: Path, mode: str) -> int:
    """Invoke the script's entry point against *root*."""
    return claims.main([mode, "--root", str(root)])


def _source_rows(root: Path) -> list[tuple[str, str]]:
    """Every (capability, status) pair the source declares, in its own order."""
    source = yaml.safe_load((root / STATUS_SOURCE).read_text(encoding="utf-8"))
    return [
        (capability["name"], capability["status"])
        for section in source["sections"]
        for capability in section["capabilities"]
    ]


def _edit_source(root: Path, old: str, new: str) -> None:
    """Rewrite one literal in the status source, refusing a fixture that no longer matches."""
    path = root / STATUS_SOURCE
    text = path.read_text(encoding="utf-8")
    assert old in text, f"the fixture no longer matches the source it mutates: {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ------------------------------------------------------------------ the view is the source


def test_the_rendered_view_carries_every_row_of_its_source_and_no_others() -> None:
    """One source means the table is a projection of it, not a second list beside it."""
    rendered = [
        (row[0], row[1])
        for row in (
            cells(line)
            for line in block_body((REPO / STATUS_MD).read_text(encoding="utf-8"), "status-view")
            if line.startswith("|")
        )
        if row[0] != "Capability" and set(row[0]) != {"-"}
    ]
    assert rendered == _source_rows(REPO)


def test_check_names_the_view_when_a_source_row_moves_and_fix_repairs_it(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: a state changed in the source and not rendered is a failing commit."""
    _edit_source(
        work_repo,
        "- name: Projection drift gate run by CI\n    status: shipped",
        "- name: Projection drift gate run by CI\n    status: building\n    record: basicly-a3ab",
    )

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert STATUS_MD in err
    assert "[status-view]" in err

    assert _run(work_repo, "--fix") == 0
    assert "| Projection drift gate run by CI | building | basicly-a3ab |" in (
        work_repo / STATUS_MD
    ).read_text(encoding="utf-8")
    assert _run(work_repo, "--check") == 0


def test_a_status_edited_in_the_rendered_table_is_refused(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Editing the view instead of its source is the divergence, so it cannot stick."""
    path = work_repo / STATUS_MD
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "| Worktree isolation per unit of work | shipped |",
            "| Worktree isolation per unit of work | partial |",
            1,
        ),
        encoding="utf-8",
    )

    assert _run(work_repo, "--check") == 1
    assert "[status-view]" in capsys.readouterr().err

    assert _run(work_repo, "--fix") == 0
    assert "| Worktree isolation per unit of work | shipped |" in path.read_text(encoding="utf-8")


def test_a_capability_graded_by_two_rows_is_refused(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect that forced D-30: one capability carrying two states at once."""
    _edit_source(
        work_repo,
        "- name: Worktree isolation per unit of work\n    status: shipped",
        "- name: Worktree isolation per unit of work\n    status: shipped\n    note: ''\n"
        "  - name: Worktree isolation per unit of work\n    status: designed",
    )

    assert _run(work_repo, "--check") == 1
    assert "is graded by two rows" in capsys.readouterr().err


def test_a_status_outside_the_architecture_vocabulary_is_refused(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The closed set has one definition, and the renderer holds the source to it."""
    _edit_source(
        work_repo,
        "- name: Single-track loop driven identically by any supported agent\n    status: shipped",
        "- name: Single-track loop driven identically by any supported agent\n    status: nearly",
    )

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "is 'nearly'" in err
    assert (
        "architecture §2 defines shipped, partial, building, designed, researching, deferred" in err
    )


def test_the_vocabulary_definition_going_missing_fails_loudly(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A vanished anchor must raise, not silently accept every word as a state.

    Reworded prose that stops matching is the failure mode that makes a checker worse
    than none: the gate reports a clean tree forever.
    """
    path = work_repo / ARCHITECTURE_MD
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(
            "| State | Means | Evidence required to claim it |", "| State | Means | Evidence |", 1
        ),
        encoding="utf-8",
    )

    assert _run(work_repo, "--check") == 1
    assert "has no single definition" in capsys.readouterr().err


# --------------------------------------------------- and the architecture document has none


def test_the_committed_architecture_document_grades_no_capability() -> None:
    """The positive control for the claim, on the tree as committed."""
    assert status.architecture_grades_no_capability(REPO) == []


def test_a_status_column_put_back_into_the_architecture_document_is_refused(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§1.1 carried exactly this column, which is why the two documents could disagree."""
    path = work_repo / ARCHITECTURE_MD
    text = path.read_text(encoding="utf-8")
    for old, new in zip(_UNGRADED, _GRADED, strict=True):
        assert old in text, f"the fixture no longer matches the document it mutates: {old!r}"
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")

    assert _run(work_repo, "--check") == 1
    err = capsys.readouterr().err
    assert "[architecture-grading]" in err
    assert "grades The agent ignores a rule it read as 'shipped'" in err


def test_a_decision_records_own_status_column_is_not_read_as_a_grading() -> None:
    """The discriminator, proven against the table it must not refuse.

    A component state is refused; a decision's own state is not. The parser is shown to
    have read §38's column first, so the pass above cannot be a table this never saw.
    """
    tables = list(status._tables((REPO / ARCHITECTURE_MD).read_text(encoding="utf-8")))
    decisions = [rows for _, header, rows in tables if header[:2] == ["Id", "Title"]]
    assert decisions, "the decision-record index no longer carries an Id/Title header"
    assert any("accepted" in row[2] for row in decisions[0])
    assert "Status" in next(header for _, header, _ in tables if header[:2] == ["Id", "Title"])


# ------------------------------------------------------ a promised row names an open record


def test_a_row_that_promises_work_and_names_no_record_is_refused(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The roadmap and the tracker disagreeing is the defect, so it cannot render."""
    _edit_source(work_repo, "    record: basicly-jt0dgi\n", "")

    assert _run(work_repo, "--check") == 1
    assert "Both skill roots written by every skills command" in capsys.readouterr().err


def test_a_row_naming_a_closed_record_is_refused_as_already_held(
    work_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A closed record under a promised row is a row the tree already holds and nobody moved."""
    _edit_source(work_repo, "    record: basicly-jt0dgi\n", "    record: basicly-askx4j\n")

    assert _run(work_repo, "--check") == 1
    assert "basicly-askx4j, which is closed" in capsys.readouterr().err

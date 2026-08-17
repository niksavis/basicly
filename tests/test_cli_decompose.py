"""Tests for what ``basicly decompose`` reports about its own grouping (jr0l.45).

The grouping is computed in ``decompose`` and pinned in ``test_decompose.py``; what
these tests pin is the *surface*. A plan whose scopes support four parallel groups
reporting one, with nothing naming the path that cost the other three, is the silent
half of the failure — so the collapse has to be printed, by the dry run and the real
run alike, from the same computation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import cli, decompose
from basicly.decompose import ChildSpec
from tests import fake_tracker

MANIFEST = "pyproject.toml"


@pytest.fixture(autouse=True)
def _in_empty_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run outside the real repo: no scope material, so sizing is a fixed zero."""
    monkeypatch.chdir(tmp_path)


def _plan(tmp_path: Path, *, shared: bool) -> Path:
    """Four children, each owning one module and touching one shared manifest."""
    children = [
        {
            "title": name,
            "acceptance": ["does the thing"],
            "scope": [f"src/{name}.py", MANIFEST],
            # The plan gate's minimum (basicly-u2hl.1, basicly-u2hl.20); these tests are
            # about the grouping report, so every child declares the fields and none of
            # them declares a dependency that would change the grouping.
            "depends_on": [],
            "budget_tokens": 40000,
            "integrity": "L2",
            "demonstration": f"run `basicly decompose feat --plan plan.json` for {name}",
            **({"shared": [MANIFEST]} if shared else {}),
        }
        for name in ("a", "b", "c", "d")
    ]
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"children": children}), encoding="utf-8")
    return plan


@pytest.fixture
def _no_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    """No tracker to read, which each caller degrades to "nothing frozen".

    ``None`` rather than a raiser, because that is the seam's own answer for a br that is
    not on PATH — a read that raises past the seam is a different fact and no longer this
    fixture's.
    """
    fake_tracker.install(monkeypatch, lambda *_a, **_k: None)


@pytest.mark.usefixtures("_no_tracker")
def test_dry_run_names_the_path_that_collapses_the_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One serial group, and the report says which single path is owed for it."""
    assert (
        cli.main(["decompose", "feat", "--plan", str(_plan(tmp_path, shared=False)), "--dry-run"])
        == 0
    )

    out = capsys.readouterr().out
    assert "4 children in 1 parallel group(s)" in out
    assert "collapsing paths:" in out
    assert f"`{MANIFEST}`: collapses the grouping" in out
    assert "1 group(s) with it and 4 without" in out


@pytest.mark.usefixtures("_no_tracker")
def test_dry_run_keeps_the_modules_parallel_and_still_names_the_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Declaring the manifest shared restores the four groups; the path is still named."""
    assert (
        cli.main(["decompose", "feat", "--plan", str(_plan(tmp_path, shared=True)), "--dry-run"])
        == 0
    )

    out = capsys.readouterr().out
    assert "4 children in 4 parallel group(s)" in out
    assert f"shared (not owned): {MANIFEST}" in out
    assert f"`{MANIFEST}`: declared shared" in out
    assert "no longer collapses the grouping" in out


@pytest.mark.usefixtures("_no_tracker")
def test_a_plan_with_no_deciding_path_reports_no_collapse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silence when nothing collapses: the section is a finding, not a fixed banner."""
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({
            "children": [
                {
                    "title": name,
                    "acceptance": ["ac"],
                    "scope": [f"src/{name}.py"],
                    "depends_on": [],
                    "budget_tokens": 40000,
                    "integrity": "L2",
                    "demonstration": "run `basicly decompose feat --dry-run`",
                }
                for name in ("a", "b")
            ]
        }),
        encoding="utf-8",
    )

    assert cli.main(["decompose", "feat", "--plan", str(plan), "--dry-run"]) == 0
    assert "collapsing paths:" not in capsys.readouterr().out


@pytest.mark.usefixtures("_no_tracker")
def test_the_real_run_reports_the_same_collapse_as_the_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A preview that names a collapse the run does not is not a preview of it.

    The dry run computes the report itself; the real run prints what
    :func:`decompose.decompose` recorded on its result (that the recorded set is
    :func:`decompose.collapsing_paths` of the plan is pinned in ``test_decompose.py``).
    This is the surface half: two code paths, one line (basicly-u6tw's rule).
    """
    plan = _plan(tmp_path, shared=False)
    assert cli.main(["decompose", "feat", "--plan", str(plan), "--dry-run"]) == 0
    preview = capsys.readouterr().out

    recorded = decompose.DecomposeResult(
        "feat",
        (),
        (("feat.1", "feat.2", "feat.3", "feat.4"),),
        decompose.collapsing_paths(decompose.load_plan_file(plan)),
    )
    monkeypatch.setattr(decompose, "decompose", lambda *_a, **_k: recorded)

    assert cli.main(["decompose", "feat", "--plan", str(plan)]) == 0
    run = capsys.readouterr().out

    collapse = f"`{MANIFEST}`: collapses the grouping"
    assert collapse in preview
    assert collapse in run


@pytest.mark.usefixtures("_no_tracker")
def test_shared_is_only_printed_for_a_child_that_declares_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An owning child's line stays as it was — the field is additive, not a new row."""
    children = (
        ChildSpec("a", ("ac",), ("src/a.py", MANIFEST), shared=(MANIFEST,)),
        ChildSpec("b", ("ac",), ("src/b.py",)),
    )
    monkeypatch.setattr(decompose, "load_plan_file", lambda _path: children)

    assert cli.main(["decompose", "feat", "--plan", "plan.toml", "--dry-run"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if "shared (not owned)" in line]
    assert lines == [f"      shared (not owned): {MANIFEST}"]

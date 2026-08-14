"""Tests for the codebase-improvement loop's controller and actuator (basicly-u2hl.27).

Three properties carry the whole bead and each is pinned here:

* **The engine disposes.** Selection is arithmetic over the sensor's measurements, so
  two runs over one tree pick the same target. The tie-break test is what proves the
  order is total rather than merely usually-stable.
* **One open lane.** A run with an unlanded lane already open files nothing and says
  why; a run with none files exactly one. Both directions, because a bound that only
  ever admits is indistinguishable from no bound at all.
* **The drop is reported.** One run selects one of sixty-nine candidates, and a top-1
  that says nothing about the other sixty-eight reads as "nothing else is over the cap".

The logic tests drive the module's functions with synthetic ``Module`` records rather
than building trees: what a given measurement produces is the observable behaviour, and
a `git ls-files` fixture per case would test git. One subprocess run covers the real
tree end to end.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any

from basicly import supervise, wip
from basicly.read_cost import SCOPE_FILE_READ_CAP

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "improvement_controller.py"
CAP = SCOPE_FILE_READ_CAP


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ctl = _load(SCRIPT, "improvement_controller")


def _module(path: str, tokens: int, waiver: str | None = None) -> object:
    return ctl.sensor.Module(path=path, tokens=tokens, waiver=waiver)


def _paths(candidates: tuple[Any, ...]) -> list[str]:
    return [candidate.path for candidate in candidates]


# --- the controller -----------------------------------------------------------------


def test_the_target_is_the_module_furthest_above_the_set_point() -> None:
    """Largest error first: the loop closes the biggest gap it can see."""
    ranked = ctl.candidates([
        _module("src/a.py", CAP + 10),
        _module("src/b.py", CAP + 900),
        _module("src/c.py", CAP + 100),
    ])

    assert _paths(ranked) == ["src/b.py", "src/c.py", "src/a.py"]


def test_a_tie_on_excess_is_broken_by_path_so_two_runs_agree() -> None:
    """The order is total. A tie resolved by iteration order is not re-derivable."""
    modules = [_module("src/z.py", CAP + 5), _module("src/a.py", CAP + 5)]

    assert _paths(ctl.candidates(modules)) == _paths(ctl.candidates(list(reversed(modules))))
    assert _paths(ctl.candidates(modules)) == ["src/a.py", "src/z.py"]


def test_a_module_at_the_set_point_is_not_a_candidate() -> None:
    """The cap is the last admissible size, exactly as the sensor reads it."""
    assert ctl.candidates([_module("src/a.py", CAP)]) == ()


def test_a_waived_module_is_never_selected() -> None:
    """A waiver is a recorded decision; a loop that re-targeted it would re-open it."""
    waived = _module("src/big.py", CAP * 9, waiver="one cohesive traversal")

    assert ctl.candidates([waived, _module("src/a.py", CAP + 1)]) == ctl.candidates([
        _module("src/a.py", CAP + 1)
    ])


def test_the_set_point_is_never_respelled_in_the_controller() -> None:
    """The target is imported from the sizing governor, not written down twice."""
    assert str(CAP) not in SCRIPT.read_text(encoding="utf-8")


# --- the bound ----------------------------------------------------------------------


def test_the_bound_reuses_the_build_admission_record() -> None:
    """One record for both bounds (basicly-u2hl.23), not a second one beside it."""
    admission = ctl.admit((), (ctl.Candidate("src/a.py", CAP + 1, 1),))

    assert isinstance(admission, wip.WipAdmission)
    assert admission.limit == ctl.MAX_OPEN_LANES


def test_an_open_lane_holds_every_candidate() -> None:
    """One unlanded lane is the whole budget, so nothing is admitted beside it."""
    admission = ctl.admit(("basicly-a",), (ctl.Candidate("src/a.py", CAP + 1, 1),))

    assert admission.stalled
    assert _paths(admission.refused) == ["src/a.py"]


def test_open_lanes_counts_a_lane_that_has_not_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wider than `wip.DOWNSTREAM_PHASES`: a lane still building is still work open."""
    monkeypatch.setattr(
        ctl.supervise,
        "lane_selection",
        lambda _root, _label: (("basicly-a", "in_progress"), ("basicly-b", "closed")),
    )

    assert ctl.open_lanes(REPO_ROOT) == ("basicly-a",)


def test_open_lanes_is_empty_when_no_bead_carries_the_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loop that has never filed a lane is an empty bound, not an error."""

    def refuse(_root: Path, label: str) -> tuple[tuple[str, str], ...]:
        raise supervise.LaneSelectionError(f"no bead carries {label!r}")

    monkeypatch.setattr(ctl.supervise, "lane_selection", refuse)

    assert ctl.open_lanes(REPO_ROOT) == ()


# --- the actuator -------------------------------------------------------------------


def test_the_filed_lane_declares_the_measurement_its_scope_and_its_plan() -> None:
    """A target with no number on it, or no scope, is not a dispatchable lane."""
    body = ctl.lane_body(ctl.Candidate("src/basicly/cli.py", CAP + 47078, 47078), dropped=68)

    assert "47078" in body and str(CAP + 47078) in body
    assert "68 other candidate(s)" in body
    assert "- `src/basicly/cli.py`" in body
    assert "- `pyproject.toml`" in body
    assert "- `tests/test_cli*.py`" in body
    for heading in ("## Acceptance Criteria", "## Scope", "## Plan"):
        assert heading in body


def _fake_br(recorded: list[list[str]]) -> object:
    def run_br(_root: Path, args: list[str]) -> object:
        recorded.append(args)
        return SimpleNamespace(stdout=json.dumps({"id": "basicly-new1"}))

    return run_br


def test_dispatch_files_one_lane_carrying_the_loops_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The label is the loop's flow-control state: an unlabelled lane is invisible."""
    recorded: list[list[str]] = []
    monkeypatch.setattr(ctl.br, "run_br", _fake_br(recorded))

    issue_id = ctl.dispatch(REPO_ROOT, ctl.Candidate("src/a.py", CAP + 1, 1), dropped=0)

    assert issue_id == "basicly-new1"
    assert len(recorded) == 1
    assert recorded[0][:2] == ["create", ctl.lane_title(ctl.Candidate("src/a.py", CAP + 1, 1))]
    assert recorded[0][4:6] == ["-l", ctl.LANE_LABEL]


# --- one whole run ------------------------------------------------------------------


def _run_main(
    monkeypatch: pytest.MonkeyPatch, *, open_lanes: tuple[tuple[str, str], ...]
) -> tuple[int, list[list[str]]]:
    """One `main()` pass over two synthetic candidates and a declared lane set."""
    recorded: list[list[str]] = []
    monkeypatch.setattr(
        ctl.sensor,
        "tracked_modules",
        lambda _root: [_module("src/a.py", CAP + 10), _module("src/b.py", CAP + 900)],
    )
    monkeypatch.setattr(ctl.supervise, "lane_selection", lambda _root, _label: open_lanes)
    monkeypatch.setattr(ctl.br, "run_br", _fake_br(recorded))
    return ctl.main([]), recorded


def test_a_run_with_an_unlanded_lane_open_files_nothing_and_says_why(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The acceptance criterion's refusal half: a no-op that names what to land."""
    code, recorded = _run_main(monkeypatch, open_lanes=(("basicly-held", "in_progress"),))

    assert (code, recorded) == (0, [])
    out = capsys.readouterr().out
    assert "no-op" in out and "basicly-held" in out
    assert "selected" not in out


def test_a_run_with_no_open_lane_dispatches_exactly_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The admitting half: one lane, for the worst candidate, and one only."""
    code, recorded = _run_main(monkeypatch, open_lanes=())

    assert code == 0
    assert len(recorded) == 1
    assert "src/b.py" in recorded[0][1]
    out = capsys.readouterr().out
    assert "selected:  src/b.py" in out
    assert "dispatch:  basicly-new1" in out


def test_a_run_reports_the_candidates_it_dropped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Silent truncation reads as "nothing else is over the cap"."""
    _run_main(monkeypatch, open_lanes=())

    assert "dropped:   1 candidate(s)" in capsys.readouterr().out


def test_the_controller_runs_against_the_real_tree() -> None:
    """End to end, read-only: the loop has to survive this repo's actual measurements."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "selected:" in proc.stdout
    assert "dry run:" in proc.stdout

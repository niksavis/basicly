from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from basicly import supervise, wip
from basicly.read_cost import SCOPE_FILE_READ_CAP

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "improvement_controller.py"
CAP = SCOPE_FILE_READ_CAP


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ctl = _load(SCRIPT, "improvement_controller")
wired = _load(REPO_ROOT / ".scripts" / "wired_or_deleted.py", "wired_or_deleted")


def _module(path: str, tokens: int, waiver: str | None = None) -> object:
    return ctl.sensor.Module(path=path, tokens=tokens, waiver=waiver)


def _paths(candidates: tuple[Any, ...]) -> list[str]:
    return [candidate.path for candidate in candidates]


def test_the_target_is_the_module_furthest_above_the_set_point() -> None:
    ranked = ctl.candidates([
        _module("src/a.py", CAP + 10),
        _module("src/b.py", CAP + 900),
        _module("src/c.py", CAP + 100),
    ])

    assert _paths(ranked) == ["src/b.py", "src/c.py", "src/a.py"]


def test_a_tie_on_excess_is_broken_by_path_so_two_runs_agree() -> None:
    modules = [_module("src/z.py", CAP + 5), _module("src/a.py", CAP + 5)]

    assert _paths(ctl.candidates(modules)) == _paths(ctl.candidates(list(reversed(modules))))
    assert _paths(ctl.candidates(modules)) == ["src/a.py", "src/z.py"]


def test_a_module_at_the_set_point_is_not_a_candidate() -> None:
    assert ctl.candidates([_module("src/a.py", CAP)]) == ()


def test_a_waived_module_is_never_selected() -> None:
    waived = _module("src/big.py", CAP * 9, waiver="one cohesive traversal")

    assert ctl.candidates([waived, _module("src/a.py", CAP + 1)]) == ctl.candidates([
        _module("src/a.py", CAP + 1)
    ])


def test_the_set_point_is_never_respelled_in_the_controller() -> None:
    assert str(CAP) not in SCRIPT.read_text(encoding="utf-8")


def test_the_bound_reuses_the_build_admission_record() -> None:
    admission = ctl.admit((), (ctl.Candidate("src/a.py", CAP + 1, 1),))

    assert isinstance(admission, wip.WipAdmission)
    assert admission.limit == ctl.MAX_OPEN_LANES


def test_an_open_lane_holds_every_candidate() -> None:
    admission = ctl.admit(("basicly-a",), (ctl.Candidate("src/a.py", CAP + 1, 1),))

    assert admission.stalled
    assert _paths(admission.refused) == ["src/a.py"]


def test_open_lanes_counts_a_lane_that_has_not_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ctl.supervise,
        "lane_selection",
        lambda _root, _label: (("basicly-a", "in_progress"), ("basicly-b", "closed")),
    )

    assert ctl.open_lanes(REPO_ROOT) == ("basicly-a",)


def test_open_lanes_is_empty_when_no_bead_carries_the_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def refuse(_root: Path, label: str) -> tuple[tuple[str, str], ...]:
        raise supervise.LaneSelectionError(f"no bead carries {label!r}")

    monkeypatch.setattr(ctl.supervise, "lane_selection", refuse)

    assert ctl.open_lanes(REPO_ROOT) == ()


def test_the_filed_lane_declares_the_measurement_its_scope_and_its_plan() -> None:
    body = ctl.lane_body(
        REPO_ROOT, ctl.Candidate("src/basicly/cli.py", CAP + 47078, 47078), dropped=68
    )

    assert "47078" in body and str(CAP + 47078) in body
    assert "68 other candidate(s)" in body
    assert "- `src/basicly/cli.py`" in body
    assert "- `pyproject.toml`" in body
    assert "- `tests/test_cli*.py`" in body
    for heading in ("## Acceptance Criteria", "## Scope", "## Plan"):
        assert heading in body


def test_a_target_already_named_test_renders_no_double_prefixed_glob() -> None:

    target = ctl.Candidate("tests/test_supervise.py", CAP + 56089, 56089)

    assert ctl.lane_scope(REPO_ROOT, target) == (
        "tests/test_supervise.py",
        "tests/test_supervise_*.py",
        "pyproject.toml",
    )


def test_a_source_target_still_declares_the_tests_that_move_with_it() -> None:
    scope = ctl.lane_scope(REPO_ROOT, ctl.Candidate("src/basicly/cli.py", CAP + 1, 1))

    assert scope == (
        "src/basicly/cli.py",
        "src/basicly/cli_*.py",
        "tests/test_cli*.py",
        "pyproject.toml",
    )


def test_every_rendered_glob_names_something_or_names_what_the_lane_will_create() -> None:

    modules = ctl.sensor.tracked_modules(REPO_ROOT)
    assert len(modules) > 100, "empty population: a green here would be the probe, not the property"
    assert ctl.resolves(REPO_ROOT, "pyproject.toml")
    assert not ctl.resolves(REPO_ROOT, "tests/test_test_supervise*.py")

    dead: list[tuple[str, str]] = []
    for module in modules:
        target = ctl.Candidate(module.path, module.tokens, module.tokens - CAP)
        exempt = {target.path, ctl.split_pattern(target)}
        dead += [
            (target.path, glob)
            for glob in ctl.lane_scope(REPO_ROOT, target)
            if glob not in exempt and not ctl.resolves(REPO_ROOT, glob)
        ]

    assert dead == []


def _fake_create(recorded: list[list[str]]) -> object:

    def create_record(_root: Path, args: list[str]) -> str:
        recorded.append(args)
        return "basicly-new1"

    return create_record


def test_dispatch_files_one_lane_carrying_the_loops_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[list[str]] = []
    monkeypatch.setattr(ctl.tracker, "create_record", _fake_create(recorded))

    issue_id = ctl.dispatch(REPO_ROOT, ctl.Candidate("src/a.py", CAP + 1, 1), dropped=0)

    assert issue_id == "basicly-new1"
    assert len(recorded) == 1
    assert recorded[0][:2] == ["create", ctl.lane_title(ctl.Candidate("src/a.py", CAP + 1, 1))]
    assert recorded[0][4:6] == ["-l", ctl.LANE_LABEL]


def _run_main(
    monkeypatch: pytest.MonkeyPatch, *, open_lanes: tuple[tuple[str, str], ...]
) -> tuple[int, list[list[str]]]:
    recorded: list[list[str]] = []
    monkeypatch.setattr(
        ctl.sensor,
        "tracked_modules",
        lambda _root: [_module("src/a.py", CAP + 10), _module("src/b.py", CAP + 900)],
    )
    monkeypatch.setattr(ctl.supervise, "lane_selection", lambda _root, _label: open_lanes)
    monkeypatch.setattr(ctl.tracker, "create_record", _fake_create(recorded))
    return ctl.main([]), recorded


def test_a_run_with_an_unlanded_lane_open_files_nothing_and_says_why(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, recorded = _run_main(monkeypatch, open_lanes=(("basicly-held", "in_progress"),))

    assert (code, recorded) == (0, [])
    out = capsys.readouterr().out
    assert "no-op" in out and "basicly-held" in out
    assert "selected" not in out


def test_a_run_with_no_open_lane_dispatches_exactly_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
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
    _run_main(monkeypatch, open_lanes=())

    assert "dropped:   1 candidate(s)" in capsys.readouterr().out


def test_the_controller_runs_against_the_real_tree() -> None:

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "sensor:" in proc.stdout
    assert ("dry run:" in proc.stdout) != ("no-op:" in proc.stdout), proc.stdout


def test_a_dry_run_prints_the_scope_it_would_have_filed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    monkeypatch.setattr(ctl.supervise, "lane_selection", lambda _root, _label: ())
    target = ctl.candidates(ctl.sensor.tracked_modules(REPO_ROOT))[0]

    assert ctl.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"scope:     {', '.join(ctl.lane_scope(REPO_ROOT, target))}" in out
    assert "test_test_" not in out


def test_the_workflow_alone_credits_the_improve_command() -> None:

    wiring = (REPO_ROOT / ".github" / "workflows" / "improvement-loop.yml").read_text(
        encoding="utf-8"
    )

    assert wired.command_findings([("loop", "improve")], wiring) == []


def test_the_workflow_credits_no_command_it_does_not_name() -> None:

    wiring = (REPO_ROOT / ".github" / "workflows" / "improvement-loop.yml").read_text(
        encoding="utf-8"
    )

    assert len(wired.command_findings([("loop", "supervise")], wiring)) == 1

"""Tests for the declared-dependency gate (basicly-9yyj6i).

Driven against fixture ledgers in `tmp_path`, seeded through the kit the way
`test_check_corpus_drift` seeds its own: a gate asserted on live bead text becomes a report
on whatever the tracker holds today, and any lane editing a `## Plan` turns the suite red.

The bodies are written with :func:`~basicly.plan_record.render_plan_section` rather than by
hand, so the test exercises the reader against the writer's own output — a fixture spelling
the section itself would keep passing after the recorded form changed.

The one real-tree run asserts the entry point runs, identifies itself and reads a non-empty
population. Not its verdict: whether this repository's bodies agree with its graph today is
the gate's answer to give, and pinning it here would redden the suite on any tracker write.
"""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from basicly import plan_record
from tests import flipped_tracker

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_declared_dependencies.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_declared_dependencies")


def _body(*depends_on: str) -> str:
    """A bead body whose `## Plan` declares *depends_on*, in the recorded form."""
    section = plan_record.render_plan_section(depends_on, 70000, "L2", "`pytest -q`")
    return f"## Plan\n\n{section}\n"


def _record(
    issue: str, title: str, *, depends_on: tuple[str, ...] = (), edges: tuple[str, ...] = ()
) -> dict:
    """One export-shaped record: a title, a declaration and its `blocks` edges."""
    return {
        "id": issue,
        "status": "open",
        "title": title,
        "description": _body(*depends_on),
        "dependencies": [{"id": target, "dependency_type": "blocks"} for target in edges],
    }


def test_a_declared_id_with_no_edge_behind_it_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The finding names the record, the declared id and the edges it does have."""
    flipped_tracker.seed_records(
        tmp_path,
        [
            _record("demo-1", "First", depends_on=("demo-2",), edges=("demo-3",)),
            _record("demo-2", "Second"),
            _record("demo-3", "Third"),
        ],
    )

    assert gate.main(["--repo", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "demo-1 declares `demo-2` with no blocks edge behind it" in captured.err
    assert "edges held: demo-3" in captured.err


def test_an_inverted_edge_is_a_disagreement(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The defect that produced this gate: the body declares it, the edge points the other way.

    `basicly-rn0o.4` declared `basicly-rn0o.3` while the edge sat on `.3` naming `.4`, so the
    renderer was the one lane no supervised pass could dispatch and both sides read as correct.
    """
    flipped_tracker.seed_records(
        tmp_path,
        [
            _record("demo-4", "Fourth", depends_on=("demo-3",)),
            _record("demo-3", "Third", edges=("demo-4",)),
        ],
    )

    assert gate.main(["--repo", str(tmp_path)]) == 1
    assert "demo-4 declares `demo-3` with no blocks edge behind it" in capsys.readouterr().err


def test_a_declaration_naming_a_title_resolves_before_a_miss_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`decompose` couples siblings by title, so a title is a declaration and not a defect."""
    flipped_tracker.seed_records(
        tmp_path,
        [
            _record("demo-1", "First", depends_on=("Second",), edges=("demo-2",)),
            _record("demo-2", "Second"),
        ],
    )

    assert gate.main(["--repo", str(tmp_path)]) == 0
    assert "1 declared dependency(ies) reconciled across 2 of 2 open" in capsys.readouterr().out


def test_a_declared_title_whose_id_is_not_an_edge_target_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Title resolution widens what reconciles, never what passes: the id must still be an edge."""
    flipped_tracker.seed_records(
        tmp_path,
        [
            _record("demo-1", "First", depends_on=("Second",), edges=("demo-3",)),
            _record("demo-2", "Second"),
            _record("demo-3", "Third"),
        ],
    )

    assert gate.main(["--repo", str(tmp_path)]) == 1
    assert "declares `Second`, a title held by demo-2" in capsys.readouterr().err


def test_a_declaration_naming_nothing_the_tracker_holds_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dangling declaration is a disagreement too, and says which kind it is."""
    flipped_tracker.seed_records(tmp_path, [_record("demo-1", "First", depends_on=("demo-9",))])

    assert gate.main(["--repo", str(tmp_path)]) == 1
    assert "which names no record in the tracker" in capsys.readouterr().err


def test_an_edge_no_body_declares_is_not_a_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only the misleading direction is checked: `dep add` records couplings a plan never had."""
    flipped_tracker.seed_records(
        tmp_path,
        [
            _record("demo-1", "First", depends_on=("demo-2",), edges=("demo-2", "demo-3")),
            _record("demo-2", "Second"),
            _record("demo-3", "Third"),
        ],
    )

    assert gate.main(["--repo", str(tmp_path)]) == 0
    assert "2 blocks edge(s) over that population" in capsys.readouterr().out


def test_a_closed_record_is_outside_the_population(tmp_path: Path) -> None:
    """A closed body's plan is history, and nothing dispatches against it."""
    records = [
        _record("demo-1", "First", depends_on=("demo-2",)),
        _record("demo-2", "Second", depends_on=("demo-3",), edges=("demo-3",)),
        _record("demo-3", "Third"),
    ]
    records[0]["status"] = "closed"
    flipped_tracker.seed_records(tmp_path, records)

    assert gate.main(["--repo", str(tmp_path)]) == 0


def test_a_population_with_nothing_to_reconcile_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty probe is the parser or the population failing, never the tree agreeing."""
    flipped_tracker.seed_records(
        tmp_path, [_record("demo-1", "First"), _record("demo-2", "Second", edges=("demo-1",))]
    )

    assert gate.main(["--repo", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "nothing was reconciled" in captured.err
    assert "an empty population, which is not an agreement" in captured.out
    assert "fix:" not in captured.err


def test_a_repository_with_no_ledger_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`tracker.all_records` is best-effort, so an unreadable ledger must not read as a pass."""
    assert gate.main(["--repo", str(tmp_path)]) == 1
    assert "no open record was read at all" in capsys.readouterr().err


def test_the_gate_runs_over_this_repository() -> None:
    """The entry point runs from a hardcoded root and covers a non-empty population."""
    completed = subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )

    assert completed.returncode in (0, 1), completed.stderr
    assert completed.stdout.startswith(f"{gate._LABEL}: ")
    assert "0 open record(s)" not in completed.stdout + completed.stderr


def test_the_gate_is_wired_as_a_verify_check() -> None:
    """An instrument built and never connected is this repository's named defect class."""
    fragment = REPO_ROOT / "basicly.d" / "basicly-9yyj6i.toml"
    config = tomllib.loads(fragment.read_text(encoding="utf-8"))
    checks = config["verify"]["checks"]

    wired = [check for check in checks if SCRIPT.name in " ".join(check["command"])]
    assert [check["name"] for check in wired] == ["declared-dependencies"]
    assert wired[0]["command"][:3] == ["uv", "run", "python"]
    assert wired[0]["modes"] == ["fast", "full"]

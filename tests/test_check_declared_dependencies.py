from __future__ import annotations

import importlib.util
import re
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
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_declared_dependencies")


def _body(*depends_on: str) -> str:
    section = plan_record.render_plan_section(depends_on, 70000, "L2", "`pytest -q`")
    return f"## Plan\n\n{section}\n"


def _record(
    issue: str, title: str, *, depends_on: tuple[str, ...] = (), edges: tuple[str, ...] = ()
) -> dict:
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
    flipped_tracker.seed_records(tmp_path, [_record("demo-1", "First", depends_on=("demo-9",))])

    assert gate.main(["--repo", str(tmp_path)]) == 1
    assert "which names no record in the tracker" in capsys.readouterr().err


def test_an_edge_no_body_declares_is_not_a_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    assert gate.main(["--repo", str(tmp_path)]) == 1
    assert "no open record was read at all" in capsys.readouterr().err


def test_the_gate_runs_over_this_repository() -> None:
    completed = subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )

    assert completed.returncode in (0, 1), completed.stderr
    assert completed.stdout.startswith(f"{gate._LABEL}: ")
    counted = re.search(r"across \d+ of (\d+) open record\(s\)", completed.stdout)
    assert counted is not None, completed.stdout
    assert int(counted.group(1)) > 0, "an empty population is the probe failing, not a pass"


def test_the_gate_is_wired_as_a_verify_check() -> None:
    fragment = REPO_ROOT / "basicly.d" / "basicly-9yyj6i.toml"
    config = tomllib.loads(fragment.read_text(encoding="utf-8"))
    checks = config["verify"]["checks"]

    wired = [check for check in checks if SCRIPT.name in " ".join(check["command"])]
    assert [check["name"] for check in wired] == ["declared-dependencies"]
    assert wired[0]["command"][:3] == ["uv", "run", "python"]
    assert wired[0]["modes"] == ["fast", "full"]

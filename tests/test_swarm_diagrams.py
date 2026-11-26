from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.capability_fixtures import DIR_ENV, capability_file
from tests.swarm_fixtures import capability, mermaid

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "diagrams"
DOC = ROOT / "docs" / "design" / "capability-file.md"
NOW = 1_800_000_000.0
CLIMB = {
    "rounds": [
        {"scores": {"coverage": 6.0, "poka-yoke": 7.0}, "important": 2},
        {"scores": {"coverage": 8.0, "poka-yoke": 8.5}, "important": 0},
    ]
}


def _sample(name: str) -> str:
    text = DOC.read_text(encoding="utf-8")
    found = re.search(rf"### Sample: {name}\n\n(```mermaid\n.*?\n```)", text, re.DOTALL)
    assert found is not None
    return found.group(1)


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = mermaid.main(list(argv))
    return code, capsys.readouterr().out


def _chain_args() -> list[str]:
    control = ["--control", "an autouse fixture sets HOME to a temp folder", "--tier", "control"]
    return ["chain", "--links", str(FIXTURES / "chain.txt"), *control]


@pytest.mark.usefixtures("drawn")
def test_each_new_diagram_matches_its_committed_sample(capsys: pytest.CaptureFixture[str]) -> None:
    decision = "continue: revise the artifact, then grade it with at least one new lens"

    assert _run(capsys, "plan", "--plan", str(FIXTURES / "plan.toml")) == (
        0,
        _sample("plan") + "\n",
    )
    assert _run(capsys, *_chain_args()) == (0, _sample("chain") + "\n")
    assert mermaid.climb_diagram(CLIMB, decision) == _sample("climb")


def test_without_a_fresh_capability_file_the_scripts_print_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run(capsys, "plan", "--plan", str(FIXTURES / "plan.toml")) == (0, "")
    assert _run(capsys, *_chain_args()) == (0, "")


@pytest.mark.usefixtures("drawn")
def test_an_unknown_dependency_and_an_empty_chain_are_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = tmp_path / "plan.toml"
    plan.write_text('[[children]]\ntitle = "a"\ndepends_on = ["b"]\n', encoding="utf-8")
    empty = tmp_path / "chain.txt"
    empty.write_text("\n", encoding="utf-8")

    assert mermaid.main(["plan", "--plan", str(plan)]) == 2
    assert "depends_on names 'b', which is no child title" in capsys.readouterr().err
    assert (
        mermaid.main(["chain", "--links", str(empty), "--control", "c", "--tier", "control"]) == 2
    )
    assert "the chain has no link" in capsys.readouterr().err


def _drawn(folder: Path, environ: dict[str, str] | None = None) -> bool:
    return capability.drawn("flowchart", environ or {DIR_ENV: str(folder)}, NOW, "linux", folder)


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ({}, True),
        ({"written_at": NOW - 8 * 24 * 3600}, False),
        ({"written_at": NOW + 600}, False),
        ({"written_at": True}, False),
        ({"writer": "other"}, False),
        ({"version": 2}, False),
        ({"renders": {"mermaid": "flowchart"}}, False),
        ({"renders": {"mermaid": ["sequenceDiagram"]}}, False),
    ],
)
def test_the_gate_counts_only_a_fresh_well_formed_file_that_lists_the_kind(
    tmp_path: Path, changed: dict[str, object], expected: bool
) -> None:
    capability_file(tmp_path, **{"written_at": NOW, **changed})

    assert _drawn(tmp_path) is expected


def test_the_gate_skips_a_symlink_an_oversized_file_and_a_half_written_file(
    tmp_path: Path,
) -> None:
    real = capability_file(tmp_path / "real", written_at=NOW)
    folder = tmp_path / "caps"
    folder.mkdir()
    (folder / "handily-reply-view.json").symlink_to(real)
    big = folder / "big.json"
    big.write_text(json.dumps({"pad": "x" * 70000}), encoding="utf-8")
    (folder / "half.json").write_text('{"format": "basicly.capa', encoding="utf-8")

    assert _drawn(folder) is False
    assert _drawn(tmp_path / "real") is True


def test_a_relative_capabilities_dir_is_ignored_for_the_default_folder(tmp_path: Path) -> None:
    capability_file(tmp_path / ".local" / "state" / "basicly" / "capabilities", written_at=NOW)
    environ = {DIR_ENV: "relative/caps"}

    assert capability.drawn("flowchart", environ, NOW, "linux", tmp_path) is True
    assert capability.folder({DIR_ENV: "relative"}, "win32", tmp_path) == (
        tmp_path / "AppData" / "Local" / "basicly" / "capabilities"
    )

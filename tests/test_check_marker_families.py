from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_marker_families.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_marker_families")

LIVE = tuple(family.marker for family in gate.FROZEN if family.retired is None)
RETIRED = tuple(family.marker for family in gate.FROZEN if family.retired is not None)


def _doc(
    declared: str = "eleven",
    retired: str = "one",
    roster: tuple[str, ...] | None = None,
) -> str:
    named = ", ".join(f"`{marker}`" for marker in (roster or tuple(f.marker for f in gate.FROZEN)))
    return f"2. **{declared}** declared families and **{retired}** retired: {named}.\n"


def _repo(
    tmp_path: Path,
    *,
    declared: tuple[str, ...] = LIVE,
    bodies: tuple[str, ...] | None = None,
    doc: str | None = None,
) -> Path:
    src = tmp_path / gate.SRC_ROOT
    src.mkdir(parents=True)
    for index, marker in enumerate(declared):
        (src / f"module_{index}.py").write_text(f'MARKER = "{marker}"\n', encoding="utf-8")
    log = tmp_path / gate.LOG_GLOB.replace("*", "0001")
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = bodies if bodies is not None else tuple(f"{m} payload" for m in (*LIVE, *RETIRED))
    log.write_text(
        "".join(json.dumps({"kind": "comment", "payload": {"text": text}}) + "\n" for text in rows),
        encoding="utf-8",
    )
    document = tmp_path / gate.ROSTER_DOC
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(doc if doc is not None else _doc(), encoding="utf-8")
    return tmp_path


def _run(repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = gate.main(["--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_clean_fixture_passes_and_states_both_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(_repo(tmp_path), capsys)

    assert code == 0, err
    assert f"{len(LIVE)} declared, {len(RETIRED)} retired" in out


def test_prose_naming_a_marker_is_not_a_declaration(tmp_path: Path) -> None:

    repo = _repo(tmp_path, declared=LIVE)
    (repo / gate.SRC_ROOT / "prose.py").write_text(
        '"""The rescue is [harness-side] because it has to be."""\n'
        "# and a sibling of [harness-ghost], which nothing writes\n"
        'OTHER = "not a marker"\n',
        encoding="utf-8",
    )

    declared = gate.declared_families(repo)

    assert "[harness-side]" not in declared
    assert "[harness-ghost]" not in declared
    assert set(declared) == set(LIVE)


def test_a_declared_family_missing_from_the_literal_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    (repo / gate.SRC_ROOT / "newcomer.py").write_text(
        'MARKER = "[harness-newcomer]"\n', encoding="utf-8"
    )

    code, _, err = _run(repo, capsys)

    assert code == 1
    assert "[harness-newcomer]: declared in src/basicly/newcomer.py" in err
    assert 'add Family("[harness-newcomer]") to FROZEN' in err


def test_a_family_only_in_the_store_is_named_as_permanent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, bodies=(*(f"{m} payload" for m in LIVE), "[harness-ghost] payload"))

    code, _, err = _run(repo, capsys)

    assert code == 1
    assert "[harness-ghost]: 1 rows in the stores and not in the frozen list" in err
    assert "its rows are permanent" in err


def test_a_live_entry_that_lost_its_producer_must_be_marked_retired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(_repo(tmp_path, declared=LIVE[1:]), capsys)

    assert code == 1
    assert f"{LIVE[0]}: frozen as live and declared nowhere in the engine" in err
    assert "rather than deleting it" in err


def test_a_retired_entry_that_gained_a_producer_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(_repo(tmp_path, declared=(*LIVE, RETIRED[0])), capsys)

    assert code == 1
    assert f"{RETIRED[0]}: frozen as retired and declared in" in err


def test_a_marker_quoted_mid_body_is_not_a_row(tmp_path: Path) -> None:

    repo = _repo(
        tmp_path,
        bodies=(
            *(f"{m} payload" for m in (*LIVE, *RETIRED)),
            "a plan that mentions [harness-ghost] without writing one",
        ),
    )

    census = gate.logged_families(repo)

    assert "[harness-ghost]" not in census.rows
    assert census.comments == len(gate.FROZEN) + 1


def test_a_populated_store_matching_nothing_is_an_error_not_a_zero(tmp_path: Path) -> None:
    repo = _repo(tmp_path, bodies=("no marker here", "nor here"))

    with pytest.raises(gate.FamilyError, match="matched no family: bad probe"):
        gate.logged_families(repo)


def test_no_store_at_all_fails_closed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / gate.LOG_GLOB.replace("*", "0001")).unlink()

    with pytest.raises(gate.FamilyError, match="no store to read"):
        gate.logged_families(repo)


def test_a_stated_count_the_tree_refutes_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(_repo(tmp_path, doc=_doc(declared="twelve")), capsys)

    assert code == 1
    assert "states 'twelve' declared families and the tree has eleven" in err
    assert "correct it to **eleven**" in err


def test_a_document_naming_a_non_family_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roster = (*(f.marker for f in gate.FROZEN), "[harness-side]")
    code, _, err = _run(_repo(tmp_path, doc=_doc(roster=roster)), capsys)

    assert code == 1
    assert "[harness-side]: named as a family and not in the frozen list" in err


def test_a_document_omitting_a_frozen_family_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    roster = tuple(f.marker for f in gate.FROZEN if f.marker != "[harness-retro]")
    code, _, err = _run(_repo(tmp_path, doc=_doc(roster=roster)), capsys)

    assert code == 1
    assert "[harness-retro]: frozen here and named nowhere in the document" in err


def test_an_absent_roster_document_fails_rather_than_agreeing_with_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    repo = _repo(tmp_path)
    (repo / gate.ROSTER_DOC).unlink()

    code, _, err = _run(repo, capsys)

    assert code == 1
    assert f"{gate.ROSTER_DOC}: missing" in err


def test_the_retired_family_has_rows_and_no_producer_in_this_repository() -> None:

    declared = gate.declared_families(REPO_ROOT)
    census = gate.logged_families(REPO_ROOT)

    assert RETIRED == ("[harness-overrun]",)
    assert "[harness-overrun]" not in declared
    assert census.rows["[harness-overrun]"] > 0
    assert len(declared) == len(LIVE)
    assert (len(LIVE), len(RETIRED)) == (11, 1)


def test_the_gate_runs_on_this_repository_and_reports_its_two_counts() -> None:
    completed = subprocess.run(  # nosec B603 B607
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )

    expected = (
        f"{gate.LABEL}: {len(LIVE)} declared, {len(RETIRED)} retired ({len(gate.FROZEN)} frozen)"
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith(expected)


def test_the_gate_is_wired_as_a_verify_check() -> None:
    fragment = REPO_ROOT / "basicly.d" / "basicly-vkh0.37.toml"
    config = tomllib.loads(fragment.read_text(encoding="utf-8"))
    checks = config["verify"]["checks"]

    wired = [check for check in checks if SCRIPT.name in " ".join(check["command"])]
    assert [check["name"] for check in wired] == ["marker-families"]
    assert wired[0]["command"][:3] == ["uv", "run", "python"]
    assert wired[0]["modes"] == ["fast", "full"]

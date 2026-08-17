"""Tests for the marker-family gate (basicly-vkh0.37).

Driven against fixture checkouts in `tmp_path`, so a lane that adds a real marker family
does not turn this module red — the two tests that do read this repository assert only what
the frozen literal and the tree must agree on, which is the gate's whole point.

The frozen list is the subject, not an implementation detail: the three historical drifts
were all in the *counting*, so each test below fixes one way the count can go wrong —
prose read as a declaration, a family with rows and no producer, and a document restating
a number nothing checked.
"""

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
    """Load a standalone script by path, the way `uv run python` does."""
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
    """The requirements document's family claim, in the shape the gate anchors on."""
    named = ", ".join(f"`{marker}`" for marker in (roster or tuple(f.marker for f in gate.FROZEN)))
    return f"2. **{declared}** declared families and **{retired}** retired: {named}.\n"


def _repo(
    tmp_path: Path,
    *,
    declared: tuple[str, ...] = LIVE,
    bodies: tuple[str, ...] | None = None,
    doc: str | None = None,
) -> Path:
    """A checkout carrying one module per declared family, a log, and the document."""
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
    document = tmp_path / gate.COUNT_DOC
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(doc if doc is not None else _doc(), encoding="utf-8")
    return tmp_path


def _run(repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """The gate's exit code and its two streams."""
    code = gate.main(["--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_clean_fixture_passes_and_states_both_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pass line carries the two numbers the acceptance criteria are stated in."""
    code, out, err = _run(_repo(tmp_path), capsys)

    assert code == 0, err
    assert f"{len(LIVE)} declared, {len(RETIRED)} retired" in out


def test_prose_naming_a_marker_is_not_a_declaration(tmp_path: Path) -> None:
    """The exact defect that made the list read twelve: a phrase counted as a family.

    `harness-side` reached the requirements document from a `commit.py` sentence. Here the
    same shape sits in a comment and in a docstring, and neither may be counted.
    """
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
    """A marker constant added to the tree without the literal fails, and the gate says which."""
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
    """Rows without a producer are the retired case, so the remedy offered is a retirement."""
    repo = _repo(tmp_path, bodies=(*(f"{m} payload" for m in LIVE), "[harness-ghost] payload"))

    code, _, err = _run(repo, capsys)

    assert code == 1
    assert "[harness-ghost]: 1 rows in the stores and not in the frozen list" in err
    assert "its rows are permanent" in err


def test_a_live_entry_that_lost_its_producer_must_be_marked_retired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deleting a producer does not delete the family — this is what forces the flip."""
    code, _, err = _run(_repo(tmp_path, declared=LIVE[1:]), capsys)

    assert code == 1
    assert f"{LIVE[0]}: frozen as live and declared nowhere in the engine" in err
    assert "rather than deleting it" in err


def test_a_retired_entry_that_gained_a_producer_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The mirror direction: a retirement note that has stopped being true."""
    code, _, err = _run(_repo(tmp_path, declared=(*LIVE, RETIRED[0])), capsys)

    assert code == 1
    assert f"{RETIRED[0]}: frozen as retired and declared in" in err


def test_a_marker_quoted_mid_body_is_not_a_row(tmp_path: Path) -> None:
    """Position is the discriminator in a store, as the AST is in the tree.

    Over whole event JSON the same probe returns families that are only bead prose quoting a
    marker, which is how the ledger appears to hold fifteen.
    """
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
    """The instrument's own positive control: an empty result belongs to the probe."""
    repo = _repo(tmp_path, bodies=("no marker here", "nor here"))

    with pytest.raises(gate.FamilyError, match="matched no family: bad probe"):
        gate.logged_families(repo)


def test_no_store_at_all_fails_closed(tmp_path: Path) -> None:
    """Absent is not clean: a checkout with nothing to read gets no verdict."""
    repo = _repo(tmp_path)
    (repo / gate.LOG_GLOB.replace("*", "0001")).unlink()

    with pytest.raises(gate.FamilyError, match="no store to read"):
        gate.logged_families(repo)


def test_a_stated_count_the_tree_refutes_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The claim that drifted three times, now bound to the derived set."""
    code, _, err = _run(_repo(tmp_path, doc=_doc(declared="twelve")), capsys)

    assert code == 1
    assert "states 'twelve' declared families and the tree has eleven" in err
    assert "correct it to **eleven**" in err


def test_a_document_naming_a_non_family_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`[harness-side]` in the roster is the second half of the same historical error."""
    roster = (*(f.marker for f in gate.FROZEN), "[harness-side]")
    code, _, err = _run(_repo(tmp_path, doc=_doc(roster=roster)), capsys)

    assert code == 1
    assert "[harness-side]: named as a family and not in the frozen list" in err


def test_a_document_omitting_a_frozen_family_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first half: the roster that left out what `retrospective.py` declares."""
    roster = tuple(f.marker for f in gate.FROZEN if f.marker != "[harness-retro]")
    code, _, err = _run(_repo(tmp_path, doc=_doc(roster=roster)), capsys)

    assert code == 1
    assert "[harness-retro]: frozen here and named nowhere in the document" in err


def test_the_retired_family_has_rows_and_no_producer_in_this_repository() -> None:
    """The load-bearing case, asserted on the real tree rather than on a fixture.

    A list derived from the live marker constants would drop this family, and its rows would
    resolve to nothing. That is why the literal is frozen instead of derived.
    """
    declared = gate.declared_families(REPO_ROOT)
    census = gate.logged_families(REPO_ROOT)

    assert RETIRED == ("[harness-overrun]",)
    assert "[harness-overrun]" not in declared
    assert census.rows["[harness-overrun]"] > 0
    assert len(declared) == len(LIVE)
    # The measurement of 2026-08-17, derived twice — an AST rule over every string constant
    # and one over module-level constant assignments only, which agreed. Adding a family is a
    # deliberate edit to FROZEN, so it is a deliberate edit to this line as well.
    assert (len(LIVE), len(RETIRED)) == (11, 1)


def test_the_gate_runs_on_this_repository_and_reports_its_two_counts() -> None:
    """The demonstration, through the command the check entry invokes."""
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
    """An instrument built and never connected is this repository's named defect class."""
    fragment = REPO_ROOT / "basicly.d" / "basicly-vkh0.37.toml"
    config = tomllib.loads(fragment.read_text(encoding="utf-8"))
    checks = config["verify"]["checks"]

    wired = [check for check in checks if SCRIPT.name in " ".join(check["command"])]
    assert [check["name"] for check in wired] == ["marker-families"]
    assert wired[0]["command"][:3] == ["uv", "run", "python"]
    assert wired[0]["modes"] == ["fast", "full"]

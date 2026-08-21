"""Tests for the waiver record and the census over it (basicly-twfj).

Nothing counted waivers across the gates that grant them, and nothing read one again after
it was granted. A count told a reader neither which waivers were permanent nor which stood
in for work, so the two tests that matter here are the ones that *fail*: a waiver stating no
kind, and a waiver bought on cost whose retiring record has closed.

The expiry is asserted against a synthetic status map rather than the real tracker. The
tracker read is a lookup by id and pinning it to a live record would make this suite fail on
the day that record closes — which is the event the gate exists to report, not to be broken
by. One test does run over the real tree, and that is the point of it: today's waivers are
all classified and none has expired.

Every waiver marker below sits inside a string literal, so this file does not waive itself.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from basicly.verify import load_verify_config

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / ".scripts"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


waivers = _load(SCRIPTS / "waivers.py", "waivers")
gate = _load(SCRIPTS / "check_waivers.py", "check_waivers")
module_size = _load(SCRIPTS / "check_module_size.py", "check_module_size")
comment_density = _load(SCRIPTS / "check_comment_density.py", "check_comment_density")

MARKER = "module-size-waiver"


def _read(reason: str) -> Any:
    """The waiver a column-0 marker line carrying *reason* parses to."""
    return waivers.read_waiver("src/basicly/big.py", f"# {MARKER}: {reason}\n", MARKER)


# --- which of the two kinds bought it ------------------------------------------------


def test_a_waiver_bought_on_cohesion_is_permanent_and_names_no_record() -> None:
    """The kind a module whose size *is* its contract carries; nothing is owed back."""
    waiver = _read("cohesion: the kit copies this file flat into one directory")

    assert waiver.kind == waivers.COHESION
    assert waiver.retires is None
    assert waiver.debt is False
    assert waiver.reason == "the kit copies this file flat into one directory"


def test_a_waiver_bought_on_cost_names_the_record_that_retires_it() -> None:
    """Debt, so the record has to say what will remove it — that is the whole distinction."""
    waiver = _read("cost(basicly-kr7t): the ratchet refused any fix to this file")

    assert waiver.kind == waivers.COST
    assert waiver.retires == "basicly-kr7t"
    assert waiver.debt is True
    assert waiver.reason == "the ratchet refused any fix to this file"


@pytest.mark.parametrize(
    "reason",
    [
        "one cohesive dispatch table",
        "cost: no record named, so nothing can expire it",
        "cohesion the colon is missing",
        "permanent: a kind this grammar does not have",
    ],
)
def test_a_waiver_stating_no_kind_is_read_as_unclassified_rather_than_admitted(
    reason: str,
) -> None:
    """Silently reading it as permanent is the blind number this record replaced."""
    waiver = _read(reason)

    assert waiver is not None
    assert waiver.kind == waivers.UNCLASSIFIED
    assert waiver.debt is False


@pytest.mark.parametrize("granting", [module_size, comment_density])
def test_a_granting_gate_fails_a_waiver_that_states_no_kind(granting: ModuleType) -> None:
    """The message belongs to the gate that grants the waiver, and both gates grant one."""
    waiver = waivers.read_waiver(
        "a.py", f"# {granting.WAIVER_MARKER}: one cohesive table\n", granting.WAIVER_MARKER
    )
    finding = waivers.unclassified_waiver(granting.WAIVER_MARKER, waiver)

    assert finding.subject == "a.py"
    assert "states no kind" in finding.detail
    assert waivers.COHESION in finding.remedy
    assert f"{waivers.COST}(<record-id>)" in finding.remedy


# --- the expiry ----------------------------------------------------------------------


def _cost(retires: str) -> Any:
    return waivers.Waiver("src/basicly/big.py", waivers.COST, retires, "stood in for work")


def test_a_cost_waiver_whose_retiring_record_closed_fails() -> None:
    """The acceptance criterion: the work is done and the exemption is still in the file."""
    findings = gate.collect([_cost("basicly-kr7t")], {"basicly-kr7t": "closed"})

    assert len(findings) == 1
    assert findings[0].subject == "src/basicly/big.py"
    assert "basicly-kr7t" in findings[0].detail
    assert "closed" in findings[0].detail


def test_the_same_waiver_passes_while_its_retiring_record_is_open() -> None:
    """The control: without it a gate that failed on every cost waiver would look correct."""
    assert gate.collect([_cost("basicly-kr7t")], {"basicly-kr7t": "open"}) == []


def test_a_cost_waiver_naming_a_record_the_tracker_does_not_hold_fails() -> None:
    """An id that resolves to nothing can never close, so its expiry would never fire."""
    findings = gate.collect([_cost("basicly-typo")], {"basicly-kr7t": "open"})

    assert len(findings) == 1
    assert "no record the tracker holds" in findings[0].detail


def test_a_cohesion_waiver_is_never_expired_however_the_tracker_reads() -> None:
    """Permanent means permanent: only debt has something to outlive."""
    permanent = waivers.Waiver("a.py", waivers.COHESION, None, "the prose is the contract")

    assert gate.collect([permanent], {}) == []


# --- the one line ---------------------------------------------------------------------


def test_the_census_states_the_total_across_the_gates_and_how_many_are_debt() -> None:
    """One line for both gates, because a per-gate count is what nobody read."""
    line = gate.census([
        waivers.Waiver("a.py", waivers.COHESION, None, "prose is the contract"),
        waivers.Waiver("b.py", waivers.COHESION, None, "prose is the contract"),
        _cost("basicly-kr7t"),
    ])

    assert "3 granted" in line
    assert "2 bought on cohesion" in line
    assert "1 debt" in line
    assert "src/basicly/big.py" in line
    for label, _ in gate.GRANTING_GATES:
        assert label in line


def test_the_census_counts_an_unclassified_waiver_apart_from_the_permanent_ones() -> None:
    """Folding it into the cohesion half would restate the number this gate replaced."""
    line = gate.census([waivers.Waiver("a.py", waivers.UNCLASSIFIED, None, "no kind")])

    assert "0 bought on cohesion" in line
    assert "1 unclassified" in line


def test_the_census_reads_the_marker_of_every_gate_that_grants_a_waiver() -> None:
    """Respelling a marker here would let one renamed in its own gate leave the census."""
    assert dict(gate.GRANTING_GATES) == {
        module_size.LABEL: module_size.WAIVER_MARKER,
        comment_density.LABEL: comment_density.WAIVER_MARKER,
    }


# --- this tree -------------------------------------------------------------------------


def test_every_waiver_in_this_tree_states_its_kind_and_none_has_expired() -> None:
    """The acceptance criterion on today's tree, run end to end through the real gate."""
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "check_waivers.py")],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert "unclassified" not in completed.stdout
    assert "granted across" in completed.stdout


def test_the_census_is_wired_to_something_that_runs_it() -> None:
    """An instrument built and never connected is this repo's named defect class."""
    checks = {check.name: check for check in load_verify_config(REPO_ROOT).checks}

    assert "waivers" in checks
    assert checks["waivers"].command[-1].endswith("check_waivers.py")
    assert "fast" in checks["waivers"].modes

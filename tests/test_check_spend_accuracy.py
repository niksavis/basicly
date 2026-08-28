"""Tests for the spend-accuracy ratchet (basicly-helmej).

`collect` is driven with fakes; the one real-tree assertion is that the gate passes on this
repository and measures a populated ledger, which is the positive control the live test in
`tests/test_decompose.py` used to carry.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".scripts" / "check_spend_accuracy.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_spend_accuracy")
ratchet = sys.modules["ratchet"]


def _ratchet(frozen: dict[str, int]) -> object:
    """A recorded table whose count agrees with its entries."""
    return ratchet.Ratchet(frozen=frozen, count=len(frozen))


def test_an_unbanked_violation_is_a_finding_that_names_the_record_and_the_remedy() -> None:
    """The estimator missed and nobody wrote it down: the landing is refused with the fix."""
    findings = gate.collect(
        {"b-1": "b-1 spent 7 tokens against a forecast of 82 (0.089x)"}, _ratchet({})
    )

    assert [f.subject for f in findings] == ["b-1"]
    assert "0.089x" in findings[0].detail
    assert '"b-1" = 1' in findings[0].remedy


def test_a_frozen_record_that_still_violates_is_history_not_a_finding() -> None:
    """Banked with its reason, the pair no longer turns main red for every other lane."""
    assert gate.collect({"b-1": "b-1 spent 7 against 82"}, _ratchet({"b-1": 1})) == []


def test_a_frozen_record_that_came_in_band_has_graduated() -> None:
    """The table only shrinks: an entry the tree no longer produces must be removed."""
    findings = gate.collect({}, _ratchet({"b-1": 1}))

    assert [f.subject for f in findings] == ["b-1"]
    assert '"b-1" = -1' in findings[0].remedy


def test_a_count_that_disagrees_with_the_table_is_a_finding() -> None:
    """A frozen set may not grow in a diff that does not say so."""
    findings = gate.collect({}, ratchet.Ratchet(frozen={}, count=1))

    assert [f.subject for f in findings] == ["pyproject.toml"]


def test_the_live_tree_passes_and_measures_a_populated_ledger(capsys) -> None:
    """The positive control: green on this ledger, over a real population, banking named."""
    assert gate.main([]) == 0
    out = capsys.readouterr().out
    assert out.startswith("spend-accuracy: ")
    measured = int(out.split("spend-accuracy: ")[1].split(" ")[0])
    assert measured >= 20

"""Tripwires tying the harness-loop skill to the loop code it documents.

The skill once said both "Ship does not merge" and "Ship merges to the entry
branch", and warned against relaying a confirm code onto a bare ``policy
checkpoint --approve`` while its phases table prescribed exactly that. Both
wordings have cost this repo a recorded incident (basicly-jr0l.39), so pin them
here instead of letting the source drift back (basicly-tcmy.7).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
import yaml

from basicly import loop

SKILL_YAML = (
    Path(__file__).parent.parent / ".basicly" / "core" / "skills" / "harness-loop" / "skill.yaml"
)


@pytest.fixture(scope="module")
def instructions() -> str:
    """The skill's instruction body, read once for the module."""
    source = yaml.safe_load(SKILL_YAML.read_text(encoding="utf-8"))
    return source["instructions"]


def _approval_rows(instructions: str) -> dict[str, str]:
    """Phases table rows that advance on a human approval, keyed by phase name.

    Selected by the row's own wording rather than a hard-coded phase list, so a
    row added later is held to the same rule.
    """
    rows: dict[str, str] = {}
    for line in instructions.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        name = re.search(r"[a-z][a-z-]+", cells[0])
        if name is None or "approv" not in " ".join(cells[1:]).lower():
            continue
        rows[name.group()] = cells[2]
    return rows


@pytest.fixture(scope="module")
def approval_rows(instructions: str) -> dict[str, str]:
    """The command cell of every phases-table row gated on a human approval."""
    rows = _approval_rows(instructions)
    assert set(rows) == {"classify", "decompose", "verify"}, rows
    return rows


def test_ship_is_never_described_as_merging(instructions: str) -> None:
    """No sentence may claim the ship phase merges — the landing already did."""
    flowed = re.sub(r"\s+", " ", instructions)
    offenders = re.findall(r"[Ss]hip[a-z]* merges[^.]*", flowed)
    assert not offenders, f"skill claims ship merges: {offenders}"


def test_ship_phase_only_tears_down_and_closes() -> None:
    """The claim above is only safe while the merge stays in the landing step."""
    assert "merge_worktree" in inspect.getsource(loop._verify_and_land)
    assert "merge_worktree" not in inspect.getsource(loop._on_ship)


def test_checkpoint_rows_name_the_loop_run_form(approval_rows: dict[str, str]) -> None:
    """Every human-approval row must advance via ``loop run``, not a bare approve."""
    missing = sorted(
        phase for phase, cell in approval_rows.items() if "basicly loop run" not in cell
    )
    assert not missing, f"phases table rows not naming the loop run form: {missing}"


def test_checkpoint_rows_mark_policy_checkpoint_as_inspection_only(
    approval_rows: dict[str, str],
) -> None:
    """A row may still mention ``policy checkpoint --approve`` — only as the trap."""
    unwarned = sorted(
        phase
        for phase, cell in approval_rows.items()
        if "--approve" in cell and "not on a bare" not in cell
    )
    assert not unwarned, (
        f"phases table offers a bare approve with no parked-loop warning: {unwarned}"
    )

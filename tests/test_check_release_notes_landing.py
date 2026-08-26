"""Tests for the landing mode of the release-note gate (basicly-18iz59).

Its own file because the question is a different one. `test_check_release_notes.py` drives
the ratchet over every *closed* record; a landing asks about one record that is still
**open**, which is the case the closed-record gate cannot see at all — it passes at every
landing and refuses the commit that closes the record, by which time ship has torn the
lane's worktree down (basicly-ibzr0f, basicly-mcf2uh).

Fixture ledgers in ``tmp_path``, never this repo's own tracker, for the reason the sibling
file states: a gate asserted on live record text reports on whatever the tracker holds
today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_check_release_notes import (
    MACHINERY_SCOPE,
    SHIPPED_SCOPE,
    _closed,
    _findings,
    _repo,
    gate,
)

FRAGMENT = "changelog.d/fix-1.added.md"


def _open(issue_id: str, description: str = SHIPPED_SCOPE) -> dict[str, str]:
    """One open record — a lane mid-landing, which is when the landing mode asks."""
    return {"id": issue_id, "status": "open", "description": description}


def test_a_landing_is_refused_while_the_lane_still_owes_a_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal this mode exists for, and it must name both ways past it."""
    repo = _repo(tmp_path, [_open("fix-1")])
    assert gate.landing(repo, "fix-1") == 1
    captured = capsys.readouterr()
    printed = captured.out + captured.err
    assert "changelog.d/fix-1.<category>.md" in printed
    assert gate.INVISIBLE_TABLE in printed


def test_a_landing_passes_once_the_fragment_is_in_the_tree(tmp_path: Path) -> None:
    """The control: one file apart from the case above."""
    repo = _repo(tmp_path, [_open("fix-1")], notes={FRAGMENT: "- did a thing\n"})
    assert gate.landing(repo, "fix-1") == 0


def test_a_landing_passes_on_a_record_declared_invisible(tmp_path: Path) -> None:
    """The declaration half has to clear the landing too, or it clears nothing."""
    repo = _repo(
        tmp_path,
        [_open("fix-1")],
        ratchet={"invisible": {"fix-1": "internal"}, "declared_count": 1},
    )
    assert gate.landing(repo, "fix-1") == 0


def test_a_landing_whose_record_declares_only_machinery_owes_nothing(tmp_path: Path) -> None:
    """A lane that touched no shipped path must not be told to announce it."""
    repo = _repo(tmp_path, [_open("fix-1", MACHINERY_SCOPE)])
    assert gate.landing(repo, "fix-1") == 0


def test_a_landing_is_not_refused_for_a_sibling_lanes_debt(tmp_path: Path) -> None:
    """Charging a lane for another record's debt is the basicly-qorx shape."""
    repo = _repo(tmp_path, [_open("fix-1", MACHINERY_SCOPE), _closed("fix-2")])
    assert gate.landing(repo, "fix-1") == 0
    assert [line.split(":")[0] for line in _findings(repo)] == ["fix-2"]


def test_the_flag_is_the_only_difference_and_absent_it_judges_the_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside a landing the gate must behave exactly as it did (this bead's third AC)."""
    monkeypatch.setattr(gate, "landing", lambda *_a: pytest.fail("judged one record"))
    monkeypatch.setattr(gate, "standings", lambda _r: {})
    monkeypatch.setattr(gate, "collect", lambda *_a: [])
    assert gate.main([]) == 0

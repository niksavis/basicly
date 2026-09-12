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
    return {"id": issue_id, "status": "open", "description": description}


def test_a_landing_is_refused_while_the_lane_still_owes_a_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, [_open("fix-1")])
    assert gate.landing(repo, "fix-1") == 1
    captured = capsys.readouterr()
    printed = captured.out + captured.err
    assert "changelog.d/fix-1.<category>.md" in printed
    assert gate.INVISIBLE_TABLE in printed


def test_a_landing_passes_once_the_fragment_is_in_the_tree(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_open("fix-1")], notes={FRAGMENT: "- did a thing\n"})
    assert gate.landing(repo, "fix-1") == 0


def test_a_landing_passes_on_a_record_declared_invisible(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        [_open("fix-1")],
        ratchet={"invisible": {"fix-1": "internal"}, "declared_count": 1},
    )
    assert gate.landing(repo, "fix-1") == 0


def test_a_landing_whose_record_declares_only_machinery_owes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_open("fix-1", MACHINERY_SCOPE)])
    assert gate.landing(repo, "fix-1") == 0


def test_a_landing_is_not_refused_for_a_sibling_lanes_debt(tmp_path: Path) -> None:
    repo = _repo(tmp_path, [_open("fix-1", MACHINERY_SCOPE), _closed("fix-2")])
    assert gate.landing(repo, "fix-1") == 0
    assert [line.split(":")[0] for line in _findings(repo)] == ["fix-2"]


def test_the_flag_is_the_only_difference_and_absent_it_judges_the_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gate, "landing", lambda *_a: pytest.fail("judged one record"))
    monkeypatch.setattr(gate, "standings", lambda _r: {})
    monkeypatch.setattr(gate, "collect", lambda *_a: [])
    assert gate.main([]) == 0

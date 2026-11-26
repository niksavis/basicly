from __future__ import annotations

from pathlib import Path

import pytest

from basicly import loop, owned_store, tracker
from tests import flipped_tracker
from tests.test_kit_tracker_holders import (
    _ctx,
    _ledger_bytes,
    _no_developer_git_config,
    _run,
    cli,
    repo,
    story,
)
from tests.tracker_process_fixture import recorded_review

__all__ = ["_no_developer_git_config", "repo", "story"]


@pytest.mark.parametrize(
    "argv",
    [("claim",), ("assign",), ("update", "--status", "in_progress")],
    ids=["claim", "assign", "update-in-progress"],
)
def test_a_claim_with_only_a_real_git_name_is_refused_with_the_pseudonym_command(
    story: tuple[Path, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
) -> None:
    ledger, record = story
    home = ledger.parent / "real-name-home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Real Name\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Author Name")
    before = _ledger_bytes(ledger)

    code, refused = _run(capsys, argv[0], str(ledger), record, *argv[1:])

    assert code == cli.EXIT_REFUSED
    assert "git config basicly.holder PSEUDONYM" in refused["refused"]
    assert _ledger_bytes(ledger) == before


def test_the_engine_claim_with_only_a_real_git_name_is_refused(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flipped_tracker.seed(
        repo,
        "th-3",
        title="an unheld story",
        description="## Trigger\n\nWhen I claim a story, I want a pseudonym, so no name leaks.",
        acceptance_criteria="Given no pseudonym when a claim runs then it is refused",
        requirements="The holder is a pseudonym",
        issue_type="task",
    )
    recorded_review(owned_store.kit(repo, "commands"), owned_store.ledger_dir(repo), "th-3")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Author Name")

    blocked = loop._hold_for_this_session(_ctx(repo, "th-3"))

    assert blocked is not None
    assert "git config basicly.holder PSEUDONYM" in repr(blocked)
    assert not (tracker.read_record(repo, "th-3") or {}).get("assignee")

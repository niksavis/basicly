from __future__ import annotations

from pathlib import Path

import pytest

from basicly import merge
from tests import fake_tracker

LEDGER = f"{merge.owned_store.LEDGER_DIR.as_posix()}/events-0001.jsonl"


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _fake_git(monkeypatch: pytest.MonkeyPatch, status: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> _Proc:
        calls.append(list(args))
        return _Proc(status if args[0] == "status" else "")

    monkeypatch.setattr(merge, "git", run)
    return calls


def test_the_owned_ledger_is_swept_into_the_tracker_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    calls = _fake_git(monkeypatch, f" M {LEDGER}\n")
    fake_tracker.install(monkeypatch, lambda _r, _args: _Proc())

    assert merge.commit_tracker_state(tmp_path, "basicly-x") is True
    assert ["add", *merge.ENGINE_TRACKER_PATHS] in calls


def test_a_file_beside_the_ledger_is_still_somebody_elses_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_git(monkeypatch, f" M {LEDGER}\n M src/app.py\n")

    assert merge.foreign_dirt(tmp_path) == ("src/app.py",)


def test_a_tree_with_no_dirt_is_not_staged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:

    calls = _fake_git(monkeypatch, f" M {LEDGER}\n")
    fake_tracker.install(monkeypatch, lambda _r, _args: _Proc())

    assert merge.commit_tracker_state(tmp_path, "basicly-x") is True
    assert ["add", ".basicly/ledger"] in calls


def _refusing_git(monkeypatch: pytest.MonkeyPatch, refusals: int) -> list[list[str]]:
    calls: list[list[str]] = []
    left = [refusals]

    def run(args: list[str], **_kwargs: object) -> _Proc:
        calls.append(list(args))
        if args[0] == "commit" and left[0] > 0:
            left[0] -= 1
            raise RuntimeError(
                "a gate refused `git commit`: `pre-commit-script` refused: "
                "FAILED: release-notes (0.06s) \u00b7 checks failed: 32/33 passed"
            )
        return _Proc(f" M {LEDGER}\n" if args[0] == "status" else "")

    monkeypatch.setattr(merge, "git", run)
    fake_tracker.install(monkeypatch, lambda _r, _args: _Proc())
    return calls


def test_a_refused_tracker_commit_is_retried_once_and_says_which_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _refusing_git(monkeypatch, refusals=1)
    notes: list[str] = []

    assert merge.commit_tracker_state(tmp_path, "basicly-x", on_retry=notes.append) is True
    assert [args[0] for args in calls].count("commit") == 2
    assert "retry" in notes[0]
    assert "FAILED: release-notes" in notes[0]


def test_the_retry_is_bounded_at_one_and_names_itself_apart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _refusing_git(monkeypatch, refusals=2)
    notes: list[str] = []

    with pytest.raises(merge.TrackerCommitRefusedError, match="FAILED: release-notes"):
        merge.commit_tracker_state(tmp_path, "basicly-x", on_retry=notes.append)
    assert [args[0] for args in calls].count("commit") == 2
    assert notes == []


def test_nothing_is_restaged_between_the_two_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _refusing_git(monkeypatch, refusals=1)

    merge.commit_tracker_state(tmp_path, "basicly-x")
    verbs = [args[0] for args in calls]
    assert verbs[verbs.index("commit") : verbs.index("commit") + 2] == ["commit", "commit"]


def test_the_landing_carries_the_retry_into_its_own_narrative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _refusing_git(monkeypatch, refusals=1)
    session = merge.Session("w", "harness/x", "main", "abc", str(tmp_path), "now")
    monkeypatch.setattr(merge, "load_session", lambda *_a, **_k: session)
    monkeypatch.setattr(merge, "_pre_merge_state", lambda *_a, **_k: None)
    monkeypatch.setattr(merge, "current_branch", lambda *_a, **_k: "main")
    monkeypatch.setattr(merge, "_assert_base_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        merge, "_replay_verify_merge", lambda *_a, **_k: merge.MergeResult("w", "merged", "landed")
    )

    result = merge.merge_worktree(tmp_path, "w", bead="basicly-x")

    assert result.detail.startswith("landed; ")
    assert "refused once and taken on a retry" in result.detail
    assert "FAILED: release-notes" in result.detail

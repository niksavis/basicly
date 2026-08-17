"""The landing's sweep of the engine's own tracker trees (basicly-vkh0.25).

Its own module rather than a block in ``test_merge.py``: that file sits about five
times over the size cap, so the ratchet allows it to shrink and not to grow.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import merge
from tests import fake_tracker

if TYPE_CHECKING:
    import pytest

LEDGER = f"{merge.owned_store.LEDGER_DIR.as_posix()}/events-0001.jsonl"


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _fake_git(monkeypatch: pytest.MonkeyPatch, status: str) -> list[list[str]]:
    """Stub ``merge.git`` with *status* for ``status``, recording every call."""
    calls: list[list[str]] = []

    def run(args: list[str], **_kwargs: object) -> _Proc:
        calls.append(list(args))
        return _Proc(status if args[0] == "status" else "")

    monkeypatch.setattr(merge, "git", run)
    return calls


def test_the_owned_ledger_is_swept_into_the_tracker_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane dirties the ledger before it lands, so the landing owns it.

    The claim at provisioning, the gate reports and the comments all append there, which
    is why the first landing after the flip was refused and needed a human.
    """
    calls = _fake_git(monkeypatch, f" M {LEDGER}\n")
    fake_tracker.install(monkeypatch, lambda _r, _args: _Proc())

    assert merge.commit_tracker_state(tmp_path, "basicly-x") is True
    assert ["add", *merge.ENGINE_TRACKER_PATHS] in calls


def test_a_file_beside_the_ledger_is_still_somebody_elses_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exemption is two named trees, not a licence to commit whatever is dirty."""
    _fake_git(monkeypatch, f" M {LEDGER}\n M src/app.py\n")

    assert merge.foreign_dirt(tmp_path) == ("src/app.py",)


def test_a_tree_with_no_dirt_is_not_staged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only a tree git reported dirt in is staged: ``git add`` exits 128 on an absent one.

    Decided from what git reported, never from disk: reading the filesystem here made a
    git-level-faked path depend on it, and could stage nothing at all.
    """
    calls = _fake_git(monkeypatch, f" M {LEDGER}\n")
    fake_tracker.install(monkeypatch, lambda _r, _args: _Proc())

    assert merge.commit_tracker_state(tmp_path, "basicly-x") is True
    assert ["add", ".basicly/ledger"] in calls

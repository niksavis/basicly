"""Tests for the one tracker ``redirect`` resolver (basicly-tcmy.19).

The property under test is that **every** question about where the tracker is resolves to
one checkout. Three callers ask it — the ledger read, the store resolver and the commit
pre-check — and each had its own copy of the rule, so in a redirected worktree they
answered with different directories.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import merge, tracker_paths

LEDGER_DIR = tracker_paths.LEDGER_DIR_NAME
REDIRECT = tracker_paths.REDIRECT_NAME


@pytest.fixture
def base(tmp_path: Path) -> Path:
    """A checkout whose ledger holds one record."""
    ledger = tmp_path / "base" / LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / "events-0001.jsonl").write_text('{"record": "base-1"}\n', encoding="utf-8")
    return tmp_path / "base"


def _worktree(tmp_path: Path, target: Path) -> Path:
    """A linked worktree carrying its own ledger copy and a redirect to *target*."""
    worktree = tmp_path / "wt"
    ledger = worktree / LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / "events-0001.jsonl").write_text('{"record": "wt-1"}\n', encoding="utf-8")
    (ledger / REDIRECT).write_text(f"{target}\n", encoding="utf-8")
    return worktree


def test_every_caller_resolves_the_redirect_to_the_same_checkout(
    tmp_path: Path, base: Path
) -> None:
    """The defect: three copies of one rule, disagreeing in exactly the redirected case.

    ``ledger_root`` additionally required the target be named ``.beads``, so it fell back
    to the worktree while the tracker read followed the redirect — and since
    ``owned_store.ledger_dir`` routes the owned event log through ``ledger_root``, the
    split decided where the store lived, not only where the spool did.
    """
    worktree = _worktree(tmp_path, base)

    assert tracker_paths.tracker_root(worktree) == base
    assert tracker_paths.ledger_dir(worktree) == base / LEDGER_DIR
    assert tracker_paths.tracker_root(worktree) == base


def test_a_checkout_with_no_redirect_owns_its_own_tracker(base: Path) -> None:
    """The control: without a redirect every caller answers with the checkout itself."""
    assert tracker_paths.tracker_root(base) == base
    assert tracker_paths.tracker_root(base) == base


@pytest.mark.parametrize("target", ["/nonexistent/elsewhere", ""])
def test_an_unusable_redirect_falls_back_to_the_checkouts_own_ledger(
    tmp_path: Path, target: str
) -> None:
    """A stale or hand-edited redirect must not scatter reads somewhere arbitrary.

    The empty case is not decoration: ``Path("")`` is ``Path(".")``, so a blank file
    would silently redirect every read at whatever the process cwd happens to be.
    """
    ledger = tmp_path / LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / REDIRECT).write_text(f"{target}\n", encoding="utf-8")

    assert tracker_paths.tracker_root(tmp_path) == tmp_path
    assert tracker_paths.tracker_root(tmp_path) == tmp_path


def test_known_bead_ids_reads_the_redirected_ledger(tmp_path: Path, base: Path) -> None:
    """It read *repo_root*'s own store while the commit-msg hook followed the redirect.

    Both exist to satisfy the same gate, so in any redirected checkout the pre-check and
    the hook were answering from different files.
    """
    worktree = _worktree(tmp_path, base)

    assert merge.known_bead_ids(worktree) == {"base-1"}


def test_known_bead_ids_skips_a_line_that_is_not_an_event(base: Path) -> None:
    """A JSON array line raised ``AttributeError`` out of a commit-message pre-check."""
    log = base / LEDGER_DIR / "events-0001.jsonl"
    log.write_text(json.dumps(["base-1"]) + '\n{"record": "base-2"}\n', encoding="utf-8")

    assert merge.known_bead_ids(base) == {"base-2"}


def test_known_bead_ids_is_none_without_a_tracker(tmp_path: Path) -> None:
    """None is "no tracker here", which callers treat differently from an empty set."""
    assert merge.known_bead_ids(tmp_path) is None


def test_an_empty_ledger_reads_as_no_tracker_rather_than_as_no_ids(tmp_path: Path) -> None:
    """The failure mode a commit pre-check must not have.

    An empty store taken as authoritative reports every id as unknown, so every commit
    is refused — and the gate this mirrors takes the same reading.
    """
    (tmp_path / LEDGER_DIR).mkdir(parents=True)

    assert merge.known_bead_ids(tmp_path) is None

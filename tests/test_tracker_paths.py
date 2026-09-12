from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import merge, tracker_paths

LEDGER_DIR = tracker_paths.LEDGER_DIR_NAME
REDIRECT = tracker_paths.REDIRECT_NAME


@pytest.fixture
def base(tmp_path: Path) -> Path:
    ledger = tmp_path / "base" / LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / "events-0001.jsonl").write_text('{"record": "base-1"}\n', encoding="utf-8")
    return tmp_path / "base"


def _worktree(tmp_path: Path, target: Path) -> Path:
    worktree = tmp_path / "wt"
    ledger = worktree / LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / "events-0001.jsonl").write_text('{"record": "wt-1"}\n', encoding="utf-8")
    (ledger / REDIRECT).write_text(f"{target}\n", encoding="utf-8")
    return worktree


def test_every_caller_resolves_the_redirect_to_the_same_checkout(
    tmp_path: Path, base: Path
) -> None:

    worktree = _worktree(tmp_path, base)

    assert tracker_paths.tracker_root(worktree) == base
    assert tracker_paths.ledger_dir(worktree) == base / LEDGER_DIR
    assert tracker_paths.tracker_root(worktree) == base


def test_a_checkout_with_no_redirect_owns_its_own_tracker(base: Path) -> None:
    assert tracker_paths.tracker_root(base) == base
    assert tracker_paths.tracker_root(base) == base


@pytest.mark.parametrize("target", ["/nonexistent/elsewhere", ""])
def test_an_unusable_redirect_falls_back_to_the_checkouts_own_ledger(
    tmp_path: Path, target: str
) -> None:

    ledger = tmp_path / LEDGER_DIR
    ledger.mkdir(parents=True)
    (ledger / REDIRECT).write_text(f"{target}\n", encoding="utf-8")

    assert tracker_paths.tracker_root(tmp_path) == tmp_path
    assert tracker_paths.tracker_root(tmp_path) == tmp_path


def test_known_bead_ids_reads_the_redirected_ledger(tmp_path: Path, base: Path) -> None:

    worktree = _worktree(tmp_path, base)

    assert merge.known_bead_ids(worktree) == {"base-1"}


def test_known_bead_ids_skips_a_line_that_is_not_an_event(base: Path) -> None:
    log = base / LEDGER_DIR / "events-0001.jsonl"
    log.write_text(json.dumps(["base-1"]) + '\n{"record": "base-2"}\n', encoding="utf-8")

    assert merge.known_bead_ids(base) == {"base-2"}


def test_known_bead_ids_is_none_without_a_tracker(tmp_path: Path) -> None:
    assert merge.known_bead_ids(tmp_path) is None


def test_an_empty_ledger_reads_as_no_tracker_rather_than_as_no_ids(tmp_path: Path) -> None:

    (tmp_path / LEDGER_DIR).mkdir(parents=True)

    assert merge.known_bead_ids(tmp_path) is None

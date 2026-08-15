"""Tests for the one ``.beads/redirect`` resolver (basicly-tcmy.19)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import br, merge, tracker_paths, tracker_usage


@pytest.fixture
def base(tmp_path: Path) -> Path:
    """A checkout whose tracker holds one exported bead."""
    beads = tmp_path / "base" / ".beads"
    beads.mkdir(parents=True)
    (beads / "issues.jsonl").write_text('{"id": "base-1"}\n', encoding="utf-8")
    return tmp_path / "base"


def _worktree(tmp_path: Path, target: Path) -> Path:
    """A linked worktree carrying its own tracker copy and a redirect to *target*."""
    worktree = tmp_path / "wt"
    (worktree / ".beads").mkdir(parents=True)
    (worktree / ".beads" / "issues.jsonl").write_text('{"id": "wt-1"}\n', encoding="utf-8")
    (worktree / ".beads" / "redirect").write_text(f"{target}\n", encoding="utf-8")
    return worktree


def test_a_redirect_to_a_directory_not_named_beads_reaches_one_checkout(
    tmp_path: Path, base: Path
) -> None:
    """The tracker read and the ledger write answered with different checkouts.

    ``ledger_root`` additionally required the target be named ``.beads``, so it fell
    back to the worktree while ``beads_dir`` followed the redirect — and since
    ``owned_store.ledger_dir`` routes the owned event log through ``ledger_root``, the
    split decided where the store lived, not only where the spool did.
    """
    elsewhere = base / "tracker"
    elsewhere.mkdir()
    worktree = _worktree(tmp_path, elsewhere)

    assert tracker_paths.beads_dir(worktree) == elsewhere
    assert tracker_usage.ledger_root(worktree) == br.beads_dir(worktree).parent


def test_the_redirect_is_followed_when_the_target_is_named_beads(
    tmp_path: Path, base: Path
) -> None:
    """The control: the shape provisioning writes, which both rules already agreed on."""
    worktree = _worktree(tmp_path, base / ".beads")

    assert tracker_paths.beads_dir(worktree) == base / ".beads"
    assert tracker_usage.ledger_root(worktree) == base


@pytest.mark.parametrize("target", ["/nonexistent/elsewhere", ""])
def test_an_unusable_redirect_falls_back_to_the_checkouts_own_beads(
    tmp_path: Path, target: str
) -> None:
    """A stale or hand-edited redirect must not scatter reads somewhere arbitrary."""
    (tmp_path / ".beads").mkdir()
    (tmp_path / ".beads" / "redirect").write_text(f"{target}\n", encoding="utf-8")

    assert tracker_paths.beads_dir(tmp_path) == tmp_path / ".beads"
    assert tracker_usage.ledger_root(tmp_path) == tmp_path


def test_known_bead_ids_reads_the_redirected_export(tmp_path: Path, base: Path) -> None:
    """It read *repo_root*'s own ``.beads`` while the commit-msg hook followed the redirect.

    Both exist to satisfy the same gate, so in any redirected checkout the pre-check
    and the hook were answering from different files.
    """
    worktree = _worktree(tmp_path, base / ".beads")

    assert merge.known_bead_ids(worktree) == {"base-1"}


def test_known_bead_ids_skips_a_line_that_is_not_a_record(base: Path) -> None:
    """A JSON array line raised ``AttributeError`` out of a commit-message pre-check."""
    export = base / ".beads" / "issues.jsonl"
    export.write_text(json.dumps(["base-1"]) + '\n{"id": "base-2"}\n', encoding="utf-8")

    assert merge.known_bead_ids(base) == {"base-2"}


def test_known_bead_ids_is_none_without_a_workspace(tmp_path: Path) -> None:
    """None is "no tracker here", which callers treat differently from an empty set."""
    assert merge.known_bead_ids(tmp_path) is None

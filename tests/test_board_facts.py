"""Tests for the caller-side derivations the board producer refuses to make.

Every case here asserts the same property from a different side: an unfillable fact is an
**absence**, never a zero. That is the rule `board_sections` documents and the reason this
module exists, so it is what the tests bind rather than any particular value.

Driven against a `tmp_path` repository, never this one: a gate asserted on the live tracker
becomes a report on whatever the tracker holds today, and any lane editing a `## Plan` turns
the suite red.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

from basicly import board_facts, supervise, tracker

REPO_ROOT = Path(__file__).parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)  # nosec B603 B607


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on ``probe``, so the branch name is not the box's.

    Driven against git rather than a stubbed one: `_repo_facts` is a reading of
    ``status --porcelain=v1 -b`` output, so a fake would assert this module's idea of git.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "probe")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _owned_repo(root: Path, *records: str) -> Path:
    """A checkout with the kit installed and *records* open in its own ledger.

    Seeded through the kit for the reason `test_tracker_seam._owned_repo` gives, and hermetic
    for the reason `test_board_snapshot` gives: every count below is this corpus's, so it
    cannot go red on the next landing the way an assertion against the live log would.
    """
    (root / tracker.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, root / tracker.KIT_TRACKER_DIR / source.name)
    (root / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (root / "basicly.toml").write_text('[tracker]\nmode = "owned"\n', encoding="utf-8")
    kit = tracker.kit(root)
    kit.events.append(
        tracker.ledger_dir(root),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in records
        ],
    )
    return root


def test_no_supervisor_lock_yields_no_session_facts(tmp_path: Path) -> None:
    """A guessed root would be a claim about which pass is running, drawn on a wall."""
    assert board_facts.session_facts(tmp_path) is None


def test_no_grant_yields_no_grant(tmp_path: Path) -> None:
    """An absent grant is an absent window, not a zero ceiling."""
    assert board_facts.active_grant(tmp_path, "demo-1") is None


def test_no_grant_yields_no_spend(tmp_path: Path) -> None:
    """Spend under no grant is unknown. A zero here reads as "nothing was spent"."""
    assert board_facts.grant_spend(tmp_path, "demo-1", None) is None


def test_an_unreadable_tracker_yields_no_readiness(tmp_path: Path) -> None:
    """`ready` absent beats `ready: 0`: one says unknown, the other says nothing is ready."""
    assert board_facts.readiness(tmp_path) is None


def test_an_unreadable_tracker_yields_no_phases(tmp_path: Path) -> None:
    """An empty map omits the key per unit rather than inventing a phase for any of them."""
    assert board_facts.phases(tmp_path) == {}


def test_a_document_with_no_asks_yields_no_questions(tmp_path: Path) -> None:
    """Nothing to pair against is not a wait with an empty question."""
    assert board_facts.questions(tmp_path, {}) == {}


def test_the_phase_limit_is_a_positive_bound() -> None:
    """A limit of zero would silently emit no phase at all while looking configured."""
    assert board_facts.PHASE_LIMIT > 0


def test_the_session_section_is_omitted_rather_than_guessed(tmp_path: Path) -> None:
    """A root invented here would be a claim about which pass is running, drawn on a wall."""
    assert board_facts.session_facts(tmp_path) is None


def test_a_held_lock_supplies_the_session_facts_the_producer_may_not_derive(tmp_path: Path) -> None:
    """The C11 inversion, exercised: this layer reads the lock and passes the facts down.

    The producer may not read it itself - the import would close
    `supervise -> board_snapshot -> supervise`, since the supervisor emits a snapshot too.
    """
    lock = tmp_path / supervise.LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "session_id": "abc", "root_issue": "x-1"}))
    facts = board_facts.session_facts(tmp_path)
    assert facts is not None
    assert facts.root_issue == "x-1"
    assert facts.session_id == "abc"
    assert facts.supervised is True
    assert facts.stale is False


def test_the_git_state_the_producer_may_not_read_comes_from_this_layer(git_repo: Path) -> None:
    """basicly-f3tked: `dirty` is a subprocess, so the layer that may spawn one supplies it.

    The clean reading first, because it is the one an implementation can get wrong in the
    dangerous direction: a parser that counts its own header line as a change reports every
    tree dirty, and a wall board that always says dirty says nothing.
    """
    clean = board_facts.repo_facts(git_repo)
    assert clean is not None
    assert clean.branch == "probe"
    assert clean.dirty is False
    assert len(clean.head) >= 7

    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    dirty = board_facts.repo_facts(git_repo)
    assert dirty is not None
    assert dirty.dirty is True
    assert dirty.head == clean.head


def test_a_directory_git_will_not_answer_for_keeps_the_repo_section_to_its_name(
    tmp_path: Path,
) -> None:
    """The negative control on the reading above: no repo, no branch, and no exception."""
    assert board_facts.repo_facts(tmp_path) is None


def test_the_phase_map_is_bounded_because_each_entry_reads_the_whole_log(tmp_path: Path) -> None:
    """`PHASE_LIMIT` is a cost bound, and a map longer than it would be 138 s on this repo.

    The lower bound is the control: an empty map would satisfy the ceiling while emitting no
    phase at all, which is the shape the defect this fixes had.
    """
    records = tuple(f"bd-{index}" for index in range(board_facts.PHASE_LIMIT + 3))
    repo = _owned_repo(tmp_path, *records)

    phases = board_facts.phases(repo)

    assert 0 < len(phases) <= board_facts.PHASE_LIMIT
    assert set(phases) < set(records)
    assert all(value for value in phases.values())

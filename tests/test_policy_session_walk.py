from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import owned_store, policy, tracker
from tests import flipped_tracker

if TYPE_CHECKING:
    import pytest

_CHILDREN = 10


def _seeded(tmp_path: Path) -> Path:
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed_records(
        repo,
        [
            {"id": "demo-root", "status": "open"},
            *(
                {
                    "id": f"demo-root.{index}",
                    "status": "open",
                    "dependencies": [{"id": "demo-root", "dependency_type": "parent-child"}],
                }
                for index in range(1, _CHILDREN + 1)
            ),
            {
                "id": "demo-root.1.1",
                "status": "open",
                "dependencies": [{"id": "demo-root.1", "dependency_type": "parent-child"}],
            },
        ],
    )
    return repo


def test_the_session_walk_reads_the_ledger_once_however_many_beads_it_covers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _seeded(tmp_path)
    kit = owned_store.kit(repo)
    reads: list[int] = []
    real_read = kit.read_ledger

    def counting_read(*args: object, **kwargs: object) -> object:
        reads.append(1)
        return real_read(*args, **kwargs)

    monkeypatch.setattr(kit, "read_ledger", counting_read)

    found = policy.session_issue_ids(repo, "demo-root")

    assert set(found) == {
        "demo-root",
        "demo-root.1.1",
        *(f"demo-root.{n}" for n in range(1, _CHILDREN + 1)),
    }
    assert len(reads) == 1, f"the walk read the ledger {len(reads)} times for {len(found)} beads"


def test_the_walk_answers_the_same_ids_it_did_through_the_per_bead_seam(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    repo = _seeded(tmp_path)
    flipped_tracker.seed_records(
        repo,
        [
            {"id": "demo-gated", "status": "open"},
            {
                "id": "demo-root",
                "status": "open",
                "dependencies": [{"id": "demo-gated", "dependency_type": "blocks"}],
            },
        ],
    )
    monkeypatch.setattr(tracker, "all_records", lambda _repo_root: [])

    through_the_seam = policy.session_issue_ids(repo, "demo-root")
    monkeypatch.undo()
    through_the_population = policy.session_issue_ids(repo, "demo-root")

    assert "demo-gated" in through_the_population
    assert set(through_the_population) == set(through_the_seam)


def test_a_ledger_change_between_two_walks_is_visible_to_the_second(tmp_path: Path) -> None:
    repo = _seeded(tmp_path)
    before = policy.session_issue_ids(repo, "demo-root")

    flipped_tracker.seed_records(
        repo,
        [
            {
                "id": "demo-root.99",
                "status": "open",
                "dependencies": [{"id": "demo-root", "dependency_type": "parent-child"}],
            }
        ],
    )

    after = policy.session_issue_ids(repo, "demo-root")
    assert "demo-root.99" not in before
    assert "demo-root.99" in after

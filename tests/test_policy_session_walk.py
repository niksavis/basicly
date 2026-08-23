"""The session walk's cost: one read of the ledger, not one per bead.

Split out of `tests/test_policy.py` under the `test_<module>_<aspect>` form that
`.scripts/check_test_naming.py` enforces, rather than banked as ratchet debt on a module
already 9x the read cap; these tests need a real ledger and share none of its fixtures.

`policy.session_issue_ids` reached every hop through `tracker.read_record`, and that seam
reads and folds the **whole** ledger per call - so a walk cost one full read per bead in
the session. Measured on this repository's own log before the fix (7082 events, 1090
records): 8 ids in 0.90 s over 8 reads, 87 ids in 8.77 s over 87 reads, ~101 ms a bead.
The board pays it once per build and a supervised build happens once a beat.

The count is spied rather than the duration asserted, the instrument
`tests/test_board_snapshot.py` already uses for the same claim: "reads the log once" is
only evidence when a second read fails the test.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import owned_store, policy, tracker
from tests import flipped_tracker

if TYPE_CHECKING:
    import pytest

# Wide enough that one-read and one-read-per-bead cannot be confused: the session is
# twelve beads - root, ten children, one grandchild - so the old walk read twelve times.
_CHILDREN = 10


def _seeded(tmp_path: Path) -> Path:
    """A real ledger holding a root, ten children of it, and a grandchild under one."""
    repo = flipped_tracker.flipped_repo(tmp_path)
    # One literal rather than an append: a list seeded from a record with no edges infers
    # `dict[str, str]`, and pyright then refuses the edge-carrying ones.
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
    """The defect, instrumented: twelve beads used to cost twelve reads of the whole log."""
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
    """The population read is a cost change and not a coverage change.

    Both edge directions are held to it: parent-child dependents nest fractally and a
    gating dependency is the cross-cutting track, so a walk that lost either would narrow
    a grant silently rather than fail.
    """
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
    """No cross-build staleness: nothing is cached, so the second walk sees the new bead."""
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

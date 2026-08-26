"""Which records owe a release note, measured against the tracker and the tree.

`check_release_notes.py` holds the ratchet that admits or refuses this measurement; this
holds the measurement, and the split is the one the gate's own docstring already draws —
"owed is measured against the shipped surface" is a question about records and files, and
"a ratchet, not a hard gate" is a question about `pyproject.toml`. Nothing here reads a
recorded baseline, and nothing here emits a :class:`ratchet.Finding`.

Two callers ask, and they differ in one thing only. The gate asks about every *closed*
record. A landing asks about one record that is still **open**, because a lane's record
closes at ship: judged only when closed, the answer arrives on the commit that closes it,
with the worktree that would repair it already torn down (basicly-ibzr0f, basicly-mcf2uh).
:func:`landing_standing` is that question, and it routes through the same
:func:`_note_standing` so the two cannot drift apart.

Run nothing: this is imported, not executed.
"""

from __future__ import annotations

import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from basicly import config, plan_record, release, tracker  # noqa: E402 - the path above comes first

LABEL = "release-notes"

# What the wheel carries plus what a consumer reads.
SHIPPED = ("src/basicly/", ".basicly/core/", "README.md", "site/")

# One record owes at most one note, so an entry is a flag counted as a number: the ratchet
# machinery is arithmetic over a per-subject count, and this is that count's only non-zero
# value.
OWED = 1

CLOSED = "closed"

# Why a record owes nothing, in the words a stale-entry finding quotes back.
UNKNOWN = "names no record the tracker holds"
OPEN = "is not closed"
UNSCOPED = "declares no backticked `## Scope`, so nothing says what it touched"
MACHINERY = "declares no shipped path"
NOTED = "already has a release note"


@dataclass(frozen=True)
class Standing:
    """One record's answer to "does this owe a release note", and why not when it does not."""

    record: str
    owed: bool
    reason: str
    # The base-branch fragment this tree predates, still owed so the arithmetic is unchanged.
    behind: str = ""

    @property
    def count(self) -> int:
        """What the ratchet measures for this record."""
        return OWED if self.owed else 0


def standings(repo: Path) -> dict[str, Standing]:
    """Every record the tracker holds, judged."""
    # The `config` call is for its side effect: it installs the tracker mode reader the
    # owned store refuses to answer without. `check_corpus_drift` reaches the committed
    # ledger the same way, so this runs in a fresh clone with no tracker binary.
    config.load_tracker_mode(repo)
    records = tracker.all_records(repo)
    ids = [str(record.get("id")) for record in records]
    accounted = release.accounted_records(repo, ids)
    behind = release.fragments_on_base(repo)
    return {
        issue_id: _standing(issue_id, record, accounted, behind)
        for issue_id, record in zip(ids, records, strict=True)
    }


def landing_standing(repo: Path, record_id: str) -> Standing:
    """*record_id* judged as its own ship close will judge it, while it is still open.

    The status check is the only thing dropped. The caller reads the frozen and declared
    exemptions from the same tables the close does, because a landing refusing what the
    close admits would be a gate nobody can get past.
    """
    config.load_tracker_mode(repo)
    records = tracker.all_records(repo)
    found = next((item for item in records if str(item.get("id")) == record_id), None)
    if found is None:
        return Standing(record_id, False, UNKNOWN)
    ids = [str(item.get("id")) for item in records]
    return _note_standing(
        record_id,
        str(found.get("description") or ""),
        release.accounted_records(repo, ids),
        release.fragments_on_base(repo),
    )


def _standing(
    issue_id: str,
    record: Mapping[str, object],
    accounted: Collection[str],
    behind: Mapping[str, str],
) -> Standing:
    """Whether *record* owes a release note, and the reason when it does not."""
    if record.get("status") != CLOSED:
        return Standing(issue_id, False, OPEN)
    return _note_standing(issue_id, str(record.get("description") or ""), accounted, behind)


def _note_standing(
    issue_id: str,
    description: str,
    accounted: Collection[str],
    behind: Mapping[str, str],
) -> Standing:
    """The judgement itself, the caller having already settled the record's status."""
    scope = plan_record.backticked_entries(description, plan_record.SCOPE_HEADING)
    if not scope:
        return Standing(issue_id, False, UNSCOPED)
    if not any(path.startswith(SHIPPED) for path in scope):
        return Standing(issue_id, False, MACHINERY)
    if issue_id in accounted:
        return Standing(issue_id, False, NOTED)
    return Standing(issue_id, True, "", behind.get(issue_id, ""))


def behind_warnings(found: Mapping[str, Standing]) -> list[str]:
    """What to say about each record whose note the base branch holds and this tree lacks."""
    return [
        f"{LABEL}: {item.record}: `{item.behind}` is on the base branch and absent here: "
        "this tree is behind, not in debt. Rebase - never declare it invisible, such an "
        "entry is true at a branch point and false on arrival"
        for item in sorted(found.values(), key=lambda item: item.record)
        if item.behind
    ]

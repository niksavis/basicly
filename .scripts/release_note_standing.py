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

SHIPPED = ("src/basicly/", ".basicly/core/", "README.md", "site/")

OWED = 1

CLOSED = "closed"

UNKNOWN = "names no record the tracker holds"
OPEN = "is not closed"
UNSCOPED = "declares no backticked `## Scope`, so nothing says what it touched"
MACHINERY = "declares no shipped path"
NOTED = "already has a release note"


@dataclass(frozen=True)
class Standing:
    record: str
    owed: bool
    reason: str
    behind: str = ""

    @property
    def count(self) -> int:
        return OWED if self.owed else 0


def standings(repo: Path) -> dict[str, Standing]:
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
    if record.get("status") != CLOSED:
        return Standing(issue_id, False, OPEN)
    return _note_standing(issue_id, str(record.get("description") or ""), accounted, behind)


def _note_standing(
    issue_id: str,
    description: str,
    accounted: Collection[str],
    behind: Mapping[str, str],
) -> Standing:
    scope = plan_record.backticked_entries(description, plan_record.SCOPE_HEADING)
    if not scope:
        return Standing(issue_id, False, UNSCOPED)
    if not any(path.startswith(SHIPPED) for path in scope):
        return Standing(issue_id, False, MACHINERY)
    if issue_id in accounted:
        return Standing(issue_id, False, NOTED)
    return Standing(issue_id, True, "", behind.get(issue_id, ""))


def behind_warnings(found: Mapping[str, Standing]) -> list[str]:
    return [
        f"{LABEL}: {item.record}: `{item.behind}` is on the base branch and absent here: "
        "this tree is behind, not in debt. Rebase - never declare it invisible, such an "
        "entry is true at a branch point and false on arrival"
        for item in sorted(found.values(), key=lambda item: item.record)
        if item.behind
    ]

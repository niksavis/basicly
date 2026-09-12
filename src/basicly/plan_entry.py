from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import tracker
from .plan_gate import missing_fields
from .plan_record import PLAN_HEADING, has_heading, parse_plan_section


@dataclass(frozen=True)
class EntryVerdict:
    issue_id: str
    missing: tuple[str, ...] = ()
    unreadable: bool = False

    @property
    def admitted(self) -> bool:
        return not self.missing and not self.unreadable

    @property
    def reason(self) -> str:
        if self.unreadable:
            return (
                f"{self.issue_id} could not be read from the tracker, so the plan gate "
                "cannot say whether it carries a plan"
            )
        if not self.missing:
            return ""
        fields = ", ".join(self.missing)
        return (
            f"{self.issue_id} declares no {fields}; the plan gate refuses a lane BUILD "
            "cannot be held to, so declare the missing field before dispatching it"
        )


def entry_verdict_for(issue_id: str, description: str) -> EntryVerdict:

    if not has_heading(description, PLAN_HEADING):
        return EntryVerdict(issue_id)
    recorded = parse_plan_section(description)
    return EntryVerdict(issue_id, missing_fields(recorded))


def build_entry_verdict(repo_root: Path, issue_id: str) -> EntryVerdict:

    record = tracker.read_record(repo_root, issue_id)
    if not isinstance(record, dict):
        return EntryVerdict(issue_id, unreadable=True)
    description = record.get("description")
    if not isinstance(description, str):
        return EntryVerdict(issue_id, unreadable=True)
    return entry_verdict_for(issue_id, description)

from __future__ import annotations

from pathlib import Path
from typing import Any

INVEST = {
    "independent": "The check exercises one ledger operation without unfinished prerequisite work.",
    "negotiable": "The implementation may change while the observable criterion stays fixed.",
    "valuable": "The caller receives the saved result stated by the criterion.",
    "estimable": "The fixture fixes the input, operation and expected observable result.",
    "small": "The card has one bounded ledger behavior to confirm.",
    "testable": "Each criterion has an explicit command and observable expected result.",
}
DEBTS = (
    *(f"## INVEST Review: {quality}" for quality in INVEST),
    "## Conversation",
    "## Confirmation Plan",
)


def review_payload(criteria: tuple[str, ...], reference: int) -> dict[str, Any]:
    return {
        "invest": dict(INVEST),
        "conversation": [reference],
        "checks": [
            {"criterion": criterion, "command": ["fixture-check", criterion], "expected": criterion}
            for criterion in criteria
        ],
    }


def recorded_review(kit: Any, ledger: Path, record: str, *, completed: bool = False) -> None:
    commands = getattr(kit, "commands", kit)
    process = commands.review.process
    commands.comment(
        ledger, record, "The fixture agrees each saved criterion and its observable check."
    )
    found = kit.events.read_events(ledger)[0]
    state = kit.events.fold(found).records[record]
    reference = max(
        event.seq
        for event in found
        if event.record == record and event.kind in kit.events.PROSE_KINDS
    )
    payload = review_payload(process.criteria(state.fields), reference)
    commands.record_process(ledger, record, payload)
    if completed:
        checks = [
            {
                "criterion": check["criterion"],
                "command": check["command"],
                "result": check["expected"],
                "exit_code": 0,
            }
            for check in payload["checks"]
        ]
        commands.record_process(ledger, record, {"checks": checks}, completed=True)

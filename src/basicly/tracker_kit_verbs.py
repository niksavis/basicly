from __future__ import annotations

from collections.abc import Sequence

KIT_CLI = "python3 .basicly/core/kit/tracker/cli.py"
KIT_ONLY_VERBS = frozenset({
    "assign",
    "child",
    "claim",
    "close",
    "comment",
    "confirm",
    "create",
    "dep",
    "mark-ready",
    "review",
    "unassign",
    "update",
})


def refusal(argv: Sequence[str]) -> str | None:
    if len(argv) < 2 or argv[0] != "tracker" or argv[1] not in KIT_ONLY_VERBS:
        return None
    verb = argv[1]
    return (
        f"basicly tracker has no {verb!r}; the tracker kit CLI writes records. "
        f"Run: {KIT_CLI} {verb} .basicly/ledger RECORD ... "
        f"(see {KIT_CLI} {verb} --help)"
    )

"""A `harness-board/v1` producer that is not basicly, for the cross-producer parity suite.

**The point of this file is what it does not import.** `basicly-rn0o.13` records that a
conformance kit exercised against the native producer alone cannot detect parity rot, so this
is the second producer: it reads `export.json` - a fake *external* tracker export, in that
tracker's own vocabulary - and writes a conforming snapshot using the standard library only.
`tests/test_board_parity.py` is what holds it to that.

It follows the kit contract at `.basicly/core/kit/README.md` rather than `src/`'s house style,
because a foreign producer runs on a stranger's interpreter: no syntax newer than Python 3.9,
and **one exception class per handler** rather than the paren-free `except A, B:` form
`python-guidelines` prescribes here.

The design is basicly-k6tpep's record; `README.md` beside this file states the
maintenance cost of keeping a second producer alive.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "harness-board/v1"

# How long a viewer may trust this document. An export is a file that will not change until
# someone exports again, so the source is `one-shot` and there is no cadence to report.
STALE_AFTER_S = 60

# The foreign tracker's state vocabulary, which is deliberately not basicly's: the mapping is
# the adapter work a real adopter does, and having to write it is the finding.
_CLOSED = "Done"
_IN_PROGRESS = "In Progress"


def _units(issues: list[dict[str, Any]]) -> list[dict[str, object]]:
    """The open issues as `units` rows, field-selected and renamed into the contract."""
    return [
        {
            "id": issue["key"],
            "title": issue["summary"][:200],
            "status": issue["state"],
            "priority": issue["severity"],
            "type": issue["kind"],
        }
        for issue in issues
        if issue["state"] != _CLOSED
    ]


def _backlog(issues: list[dict[str, Any]]) -> dict[str, object]:
    """The whole export as counts.

    No `ready` and no `blocked`: this export carries no dependency edges at all, so either
    count would be an estimate dressed as a fact, and the contract's rule is to omit.
    """
    by_priority: dict[str, int] = {}
    for issue in issues:
        label = issue["severity"]
        by_priority[label] = by_priority.get(label, 0) + 1
    closed = sum(1 for issue in issues if issue["state"] == _CLOSED)
    return {
        "total": len(issues),
        "active": len(issues) - closed,
        "in_progress": sum(1 for issue in issues if issue["state"] == _IN_PROGRESS),
        "closed": closed,
        "by_priority": by_priority,
    }


def build_document(export: dict[str, Any]) -> dict[str, object]:
    """A conforming snapshot for *export*.

    `generated_at` is the export's own `exported_at` and never a clock reading, so the
    document is a function of its input and the checked-in `snapshot.json` cannot drift
    from what this file emits.
    """
    issues = export["issues"]
    return {
        "schema": SCHEMA,
        "generated_at": export["exported_at"],
        "freshness": {"source": "one-shot", "cadence_s": None, "stale_after_s": STALE_AFTER_S},
        "generator": {"tool": export["tool"], "version": export["tool_version"]},
        "repo": {"name": export["project"]["name"]},
        "backlog": _backlog(issues),
        "units": _units(issues),
    }


def render(export: dict[str, Any]) -> str:
    """*export* as the snapshot text this producer writes, newline-terminated."""
    return json.dumps(build_document(export), indent=2, sort_keys=False) + "\n"


def main(argv: list[str]) -> int:
    """Read the export named in *argv* and write its snapshot to stdout.

    Fails closed and says which question it could not answer, because a producer that exits 0
    having emitted nothing usable is the fail-open shape the contract is written against.
    """
    if len(argv) != 1:
        sys.stderr.write("usage: produce.py <export.json>\n")
        return 2
    source = Path(argv[0])
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as err:
        sys.stderr.write(f"cannot read {source}: {err}\n")
        return 1
    try:
        export = json.loads(text)
    except ValueError as err:
        sys.stderr.write(f"{source} is not JSON: {err}\n")
        return 1
    try:
        rendered = render(export)
    except KeyError as err:
        sys.stderr.write(f"{source} is missing {err}\n")
        return 1
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "harness-board/v1"

STALE_AFTER_S = 60

_CLOSED = "Done"
_IN_PROGRESS = "In Progress"


def _units(issues: list[dict[str, Any]]) -> list[dict[str, object]]:
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
    return json.dumps(build_document(export), indent=2, sort_keys=False) + "\n"


def main(argv: list[str]) -> int:

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

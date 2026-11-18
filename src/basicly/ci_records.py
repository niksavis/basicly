from __future__ import annotations

import re
import sys
from pathlib import Path

READERS = (
    (re.compile(r"tracker-commit-msg\.py"), "runs the record-existence hook"),
    (re.compile(r"kit/tracker/cli\.py"), "runs the tracker kit"),
    (re.compile(r"\bbasicly(?:\.cli)?\s+tracker\b"), "runs a tracker command"),
    (re.compile(r"hooks/pre-push\.py"), "runs the local full checks without --ci"),
    (re.compile(r"\bverify\b(?!.*\s--ci\b).*\s--mode\b"), "runs verify without --ci"),
)


def violations(text: str) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, reason in READERS:
            if pattern.search(line):
                found.append((number, reason))
    return found


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] or sorted(Path(".github/workflows").glob("*.yml"))
    failed = 0
    for path in paths:
        for number, reason in violations(path.read_text(encoding="utf-8")):
            print(f"{path}:{number}: {reason}; CI never reads tracker records")
            failed = 1
    return failed


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

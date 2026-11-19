from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from . import tracker_paths

READERS = (
    (re.compile(r"tracker-commit-msg\.py"), "runs the record-existence hook"),
    (re.compile(r"kit/tracker/cli\.py"), "runs the tracker kit"),
    (re.compile(r"\bbasicly(?:\.cli)?\s+tracker\b"), "runs a tracker command"),
    (re.compile(r"hooks/pre-push\.py"), "runs the local full checks without --ci"),
    (re.compile(r"\bverify\b(?!.*\s--ci\b).*\s--mode\b"), "runs verify without --ci"),
)


def guard_value(repo_root: Path) -> str:
    roots = dict.fromkeys(
        str(path.resolve()) for path in (repo_root, tracker_paths.tracker_root(repo_root))
    )
    return os.pathsep.join(roots)


def violations(text: str) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for pattern, reason in READERS:
            if pattern.search(line):
                found.append((number, reason))
    return found


OLD_RECORD_HOOK = re.compile(
    r'^ *python3 \.basicly/core/hooks/tracker-commit-msg\.py "\$\{msg_file\}" \|\| failed=1$'
)
OLD_VERIFY = re.compile(r"^(?P<step> *run: .*\sverify --mode full)$")
FIX = "remove the line, or add --ci to verify --mode full"


def migrate(text: str) -> tuple[str, list[str]]:
    kept = []
    changes = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        if OLD_RECORD_HOOK.match(body):
            changes.append("removed the tracker-commit-msg.py step")
            continue
        if found := OLD_VERIFY.match(body):
            kept.append(f"{found['step']} --ci{ending}")
            changes.append("added --ci to verify --mode full")
            continue
        kept.append(line)
    return "".join(kept), changes


def workflow_files(repo_root: Path) -> list[Path]:
    folder = repo_root / ".github" / "workflows"
    return sorted([*folder.glob("*.yml"), *folder.glob("*.yaml")])


def workflow_problems(repo_root: Path) -> list[str]:
    return [
        f"{path.relative_to(repo_root).as_posix()}:{number}: {reason}; "
        f"CI never reads tracker records; fix: {FIX}"
        for path in workflow_files(repo_root)
        for number, reason in violations(path.read_text(encoding="utf-8"))
    ]


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

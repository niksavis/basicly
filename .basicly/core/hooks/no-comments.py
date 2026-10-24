from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


def _kit_cli() -> Path:
    installed = _HERE.parent / "kit" / "comments" / "cli.py"
    if installed.exists():
        return installed
    raise SystemExit("no-comments: the comments kit is missing from beside this hook")


def staged_added_lines(path: str) -> set[int] | None:
    diff = subprocess.run(  # nosec B603 B607
        ["git", "diff", "--cached", "-U0", "--no-color", "--no-ext-diff", "--", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if diff.returncode != 0 or not diff.stdout:
        return None
    added: set[int] = set()
    for hunk in _HUNK.finditer(diff.stdout):
        start = int(hunk.group(1))
        count = 1 if hunk.group(2) is None else int(hunk.group(2))
        added.update(range(start, start + count))
    return added


def _in_scope(line: str, scopes: dict[str, set[int] | None]) -> bool:
    for path in sorted(scopes, key=len, reverse=True):
        if not line.startswith(f"{path}:"):
            continue
        number, _, _ = line[len(path) + 1 :].partition(":")
        added = scopes[path]
        return added is None or not number.isdigit() or int(number) in added
    return True


def main(argv: list[str] | None = None) -> int:
    paths = list(argv if argv is not None else sys.argv[1:])
    if not paths:
        return 0
    completed = subprocess.run(  # nosec B603
        [sys.executable, str(_kit_cli()), "check", *paths],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode == 0:
        return 0
    scopes = {str(Path(path)): staged_added_lines(path) for path in paths}
    reported = [
        line
        for line in completed.stdout.splitlines()
        if not line.startswith("comments: ") and _in_scope(line, scopes)
    ]
    if not reported:
        return 0
    sys.stdout.write("".join(f"{line}\n" for line in reported))
    sys.stdout.write(f"comments: {len(reported)} added by this commit\n")
    sys.stderr.write(completed.stderr)
    sys.stderr.write(
        "no-comments: the code is the source of truth, so a code file carries no prose.\n"
        "Remove the comment, or move the fact it carries into the record, a README or a\n"
        "test name. A directive a tool reads (noqa, nosec, type: ignore, a shebang) is\n"
        "never reported here, so anything above is prose.\n"
        "To strip them all:\n"
        "  uv run --no-project python .basicly/core/kit/comments/cli.py fix <path>\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

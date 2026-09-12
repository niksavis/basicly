from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

FRAGMENT_DIR = "changelog.d"
CAP_CHARS = 400
_NAME = re.compile(
    r"^(?P<record>.+)\.(?P<category>added|changed|deprecated|removed|fixed|security)\.md$"
)


def fragment_paths(repo: Path) -> list[Path]:
    directory = repo / FRAGMENT_DIR
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.md") if _NAME.match(path.name))


def head_size(repo: Path, relative: str) -> int | None:
    completed = subprocess.run(  # nosec B603 B607 - fixed argv, repo-local
        ["git", "-C", str(repo), "show", f"HEAD:{relative}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return len(completed.stdout.decode("utf-8", errors="replace"))


def findings(repo: Path) -> tuple[list[str], int, int]:
    refused: list[str] = []
    new = inherited = 0
    for path in fragment_paths(repo):
        relative = path.relative_to(repo).as_posix()
        text = path.read_text(encoding="utf-8")
        first = next((line for line in text.splitlines() if line.strip()), "")
        if not first.startswith("- "):
            refused.append(
                f"{relative}: the first line must start with `- `; loose prose "
                "orphans the entry from its bullet at assembly"
            )
        committed = head_size(repo, relative)
        if committed is None:
            new += 1
            allowed = CAP_CHARS
            reason = f"a new fragment must fit the {CAP_CHARS}-char cap"
        else:
            inherited += 1
            allowed = max(CAP_CHARS, committed)
            reason = f"a committed fragment may only shrink (HEAD holds {committed})"
        if len(text) > allowed:
            refused.append(f"{relative}: {len(text)} chars over the allowed {allowed} - {reason}")
    return refused, new, inherited


def main() -> int:
    refused, new, inherited = findings(REPO_ROOT)
    for line in refused:
        print(f"release-fragments: {line}", file=sys.stderr)
    total = new + inherited
    print(
        f"release-fragments: {total} fragment(s), {new} new under the {CAP_CHARS}-char cap, "
        f"{inherited} carrying a committed ceiling"
    )
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Refuse a changelog fragment the release would publish badly: oversized, or bulletless.

Measured 2026-08-23 over `changelog.d/`: 114 fragments, median 1,508 chars, 4 under any
useful cap — and the v0.9.0 notes were rewritten 689 lines → 66 by hand at cut time. A
frozen table is the wrong shape for a population this far over the line, so **the baseline
is git itself**: a fragment present in ``HEAD`` keeps its committed size as its own
ceiling and may only shrink past the cap; a fragment absent from ``HEAD`` is new and must
fit :data:`CAP_CHARS`. No table, so nothing to rebaseline and nothing goes stale.

The bullet rule is unconditional: a fragment whose first non-blank line does not start
with ``- `` is folded under a category heading as loose prose and orphans itself from its
entry — the defect that cost five entries their bullet in v0.9.0 (basicly-x8hwwv).

Stdlib-only on purpose: the end-to-end test copies this file alone into a scratch tree.

Run::

    uv run python .scripts/check_release_fragments.py
"""

from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

FRAGMENT_DIR = "changelog.d"
# One entry is one or two bullets; the whole v0.9.0 section averaged ~160 chars a record.
CAP_CHARS = 400
# The name grammar `release.scan_fragments` reads; `basicly release` refuses the rest.
_NAME = re.compile(
    r"^(?P<record>.+)\.(?P<category>added|changed|deprecated|removed|fixed|security)\.md$"
)


def fragment_paths(repo: Path) -> list[Path]:
    """Every well-named fragment, sorted; misnamed files are `basicly release`'s finding."""
    directory = repo / FRAGMENT_DIR
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.md") if _NAME.match(path.name))


def head_size(repo: Path, relative: str) -> int | None:
    """The fragment's size in ``HEAD`` in characters, or None when it is new."""
    completed = subprocess.run(  # nosec B603 B607 - fixed argv, repo-local
        ["git", "-C", str(repo), "show", f"HEAD:{relative}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return len(completed.stdout.decode("utf-8", errors="replace"))


def findings(repo: Path) -> tuple[list[str], int, int]:
    """Every refusal, plus how many fragments are new and how many inherit a ceiling."""
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
    """Report every refusal to stderr and the population summary to stdout."""
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

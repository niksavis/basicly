"""Refuse growth of a retired tracker's name inside Python prose (comments and docstrings).

This repo removed its external tracker, and over a hundred prose lines still describe it by
that name (basicly-e90rue). Nothing gated the vocabulary, so a purge could be silently
undone one comment at a time. **The baseline is git itself**, exactly as
`check_release_fragments.py` does it: a module's allowance is the same prose count computed
over `git show HEAD:<path>`, so a module may only fall to or stay at its committed count,
never rise past it, and a module absent from HEAD starts at zero.

Code identifiers and string literals that are not docstrings never count — only a
`tokenize` COMMENT token or an `ast.get_docstring` result is scanned, so this file spells
the retired name only inside :data:`RETIRED`'s key, never as a bare prose word, and cannot
count itself.

Run::

    uv run python .scripts/check_retired_vocabulary.py
"""

from __future__ import annotations

import ast
import io
import re
import subprocess  # nosec B404
import sys
import tokenize
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# The population every prose count is scanned over, matching `ratchet.SCOPE_ROOTS`.
SCOPE_ROOTS = ("src", "tests", ".scripts", ".basicly/core")

# The retired term list. Held as data rather than typed elsewhere, so no other line in this
# file spells the name as a bare prose word.
RETIRED = {"br": re.compile(r"\bbr\b")}

_DOC_NODE_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def prose_strings(source: str) -> list[str]:
    """Every comment and docstring text in *source*, each as its own string to scan."""
    strings: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        tokens = []
    strings.extend(token.string for token in tokens if token.type == tokenize.COMMENT)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return strings
    for node in ast.walk(tree):
        if isinstance(node, _DOC_NODE_TYPES):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                strings.append(doc)
    return strings


def prose_count(source: str, pattern: re.Pattern[str]) -> int:
    """How many times *pattern* matches inside *source*'s comments and docstrings only."""
    return sum(len(pattern.findall(text)) for text in prose_strings(source))


def tracked_python_files(repo: Path) -> list[str]:
    """Every tracked ``.py`` path under :data:`SCOPE_ROOTS`, repo-relative and sorted."""
    completed = subprocess.run(  # nosec B603 B607 - fixed argv, repo-local
        ["git", "-C", str(repo), "ls-files", "-z", "--", *SCOPE_ROOTS],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return []
    names = completed.stdout.decode("utf-8", errors="replace").split("\0")
    return sorted(name for name in names if name.endswith(".py"))


def head_prose_count(repo: Path, relative: str, pattern: re.Pattern[str]) -> int:
    """*pattern*'s prose count for *relative* as committed in ``HEAD``, or 0 if absent there."""
    completed = subprocess.run(  # nosec B603 B607 - fixed argv, repo-local
        ["git", "-C", str(repo), "show", f"HEAD:{relative}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return 0
    return prose_count(completed.stdout.decode("utf-8", errors="replace"), pattern)


def findings(repo: Path) -> tuple[list[str], int, int]:
    """Every refusal, plus how many modules carry a retired term and their total mentions."""
    refused: list[str] = []
    carrying = total = 0
    for term, pattern in RETIRED.items():
        for relative in tracked_python_files(repo):
            path = repo / relative
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            count = prose_count(text, pattern)
            if count == 0:
                continue
            carrying += 1
            total += count
            allowance = head_prose_count(repo, relative, pattern)
            if count > allowance:
                refused.append(
                    f"{relative}: '{term}' grew to {count} mention(s), over the HEAD "
                    f"allowance of {allowance}"
                )
    return refused, carrying, total


def main() -> int:
    """Report every refusal to stderr and the population summary to stdout."""
    refused, carrying, total = findings(REPO_ROOT)
    for line in refused:
        print(f"retired-vocabulary: {line}", file=sys.stderr)
    terms = "', '".join(RETIRED)
    print(
        f"retired-vocabulary: {carrying} module(s) carrying '{terms}' ({total} mention(s)), "
        "all at or under their HEAD allowance"
    )
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())

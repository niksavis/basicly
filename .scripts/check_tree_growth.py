"""Report how much the whole tree grew over a window, which no per-file gate can see.

Every structural gate in this stack is a per-file or per-symbol predicate: `module-size`
bounds one module, `comment-density` one module's prose share, `noqa-debt` one code's
suppressions, `vulture` and `wired-or-deleted` one symbol. A tree can add fifty
individually compliant modules and none of them notices, which is what happened —
`src/basicly/` went from 50 modules holding 408,954 tokens on 2026-08-07 to 91 holding
476,002 on 2026-08-14 with every one of those gates green throughout (basicly-5p49).

The pair is worse than blind, it is complicit: the cheapest way to satisfy a module-size
ratchet is to add a module, which carries no frozen baseline and starts clean. So the
instrument that bounds file size is the instrument that drives module count, and the
signal above them must not be satisfiable by that same action.

**Net tokens is the signal; module count is the shape.** Chosen against this repo's own
history rather than by preference:

* Module count alone cannot tell growth from redistribution. `ca7c68e` ("split what it
  grew") added 14 modules holding 30,974 tokens, of which only 10,172 was new.
* Mean tokens per module confounds them the other way: it falls under a split *and* under
  honest addition, because a new compliant module is under the 4,000-token cap while the
  frozen mean is far above it (8,179 -> 5,558 across 2026-08-08, still falling since).
* Net tokens is flat under a pure split — the origin loses what the new module gains — and
  moves by the full amount when a compliant module is added.

So the report decomposes the net into what sits in modules absent at the window's start,
what modules present at both ends did, and what deletion removed. `feat(gates): ratchet
comment density` reads +4,958 net of +4,958 new; `feat(models): ... split the generic
tier resolution` reads +4,314 net of +14,966 new.

**It never blocks, and that is the requirement rather than a concession.** D23
(`docs/requirements/factory-loop.md` §15.7): a sizing control with no recorded correct
firing is observability. This one has no firing history at all, having not existed, so it
prints a number and exits 0 — including when it cannot reach one. It earns the right to
refuse after it has refused something correctly.

The window ends at the working tree and starts at the last commit at or before HEAD's own
committer date minus :data:`WINDOW_DAYS`. HEAD's date and not the wall clock, so one
checkout always answers the same thing. A checkout holding no commit that old — CI clones
`quality-gates.yml`'s matrix job at depth 1 — says the window is uncovered rather than
inventing a baseline.

Run::

    uv run python .scripts/check_tree_growth.py
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from check_module_size import (  # noqa: E402 - the path above comes first
    SCOPE_ROOTS,
    RatchetError,
    module_tokens,
    tracked_modules,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# A week, because that is the unit the finding is denominated in and the unit that spans a
# session: this repo's tree moved on 30 of the 31 days measured, so a shorter window reports
# one lane and a longer one reports a release.
WINDOW_DAYS = 7

_LABEL = "tree-growth"

# `git cat-file --batch` answers each request with `<oid> <type> <size>\n<contents>\n`.
_BATCH_HEADER_FIELDS = 3


class GitError(Exception):
    """git could not answer, so the window has no baseline to be measured against."""


@dataclass(frozen=True)
class Tree:
    """One end of the window: which tree it is, when it is, and each module's tokens."""

    ref: str
    when: str
    tokens: Mapping[str, int]

    @property
    def total(self) -> int:
        """Every in-scope module's tokens, summed."""
        return sum(self.tokens.values())


@dataclass(frozen=True)
class Growth:
    """A window's net token change, split into the three ways a tree can move.

    The split is what separates growth from redistribution: a module extracted out of
    another adds to :attr:`in_new` and takes the same out of :attr:`in_existing`, leaving
    :attr:`net` flat, while a compliant *addition* moves it by its whole size.
    """

    base: Tree
    now: Tree
    days: int

    @property
    def net(self) -> int:
        """Tokens the tree gained over the window; negative when it shrank."""
        return self.now.total - self.base.total

    @property
    def new_paths(self) -> frozenset[str]:
        """Modules present at the window's end and absent at its start."""
        return frozenset(self.now.tokens) - frozenset(self.base.tokens)

    @property
    def in_new(self) -> int:
        """Tokens held by the modules that appeared during the window."""
        return sum(self.now.tokens[path] for path in self.new_paths)

    @property
    def in_deleted(self) -> int:
        """Tokens held at the start by modules that are gone, as a negative."""
        gone = frozenset(self.base.tokens) - frozenset(self.now.tokens)
        return -sum(self.base.tokens[path] for path in gone)

    @property
    def in_existing(self) -> int:
        """Net change across the modules present at both ends of the window."""
        return self.net - self.in_new - self.in_deleted


def report_lines(growth: Growth) -> tuple[str, str]:
    """The value and its window, then the decomposition that names the shape.

    The second line states the ratio and no verdict: which mix of addition and
    redistribution is acceptable is the judgement this signal has never yet made, and
    printing a threshold it has not earned is how the demoted controls got theirs.
    """
    share = f"{100 * growth.net / growth.in_new:.0f}% of the new tokens" if growth.in_new else "-"
    return (
        f"{_LABEL}: {growth.net:+d} tokens over {growth.days}d "
        f"({growth.base.when} {growth.base.ref} -> {growth.now.when} {growth.now.ref}), "
        f"{len(growth.base.tokens)} -> {len(growth.now.tokens)} tracked modules",
        f"{_LABEL}:   {growth.in_new:+d} in {len(growth.new_paths)} new, "
        f"{growth.in_existing:+d} in modules that already existed, "
        f"{growth.in_deleted:+d} deleted; net is {share}",
    )


def _git(repo: Path, *args: str) -> str:
    """One git question, with its stderr promoted to a :class:`GitError`."""
    completed = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"git {args[0]} exited {completed.returncode}"
        raise GitError(detail)
    return completed.stdout


def _blob_texts(repo: Path, oids: Sequence[str]) -> list[str]:
    """Each blob's decoded text, in request order, through one `cat-file --batch`.

    One subprocess, because the baseline tree is ~300 blobs and 300 `git show` spawns would
    cost more than every gate this sits beside. Bytes, because the header's size is a byte
    count and decoding first would misplace every boundary after a non-ASCII character.

    Raises:
        GitError: git refused, or answered with a header this cannot parse.
    """
    if not oids:
        return []
    completed = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=("\n".join(oids) + "\n").encode(),
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise GitError(completed.stderr.decode(errors="replace").strip() or "cat-file failed")
    out, texts, pos = completed.stdout, [], 0
    for oid in oids:
        end = out.find(b"\n", pos)
        header = out[pos:end].decode(errors="replace").split()
        if end < 0 or len(header) != _BATCH_HEADER_FIELDS:
            raise GitError(f"unreadable cat-file header for {oid}")
        size = int(header[2])
        texts.append(out[end + 1 : end + 1 + size].decode("utf-8", errors="replace"))
        pos = end + 1 + size + 1
    return texts


def baseline_ref(repo: Path, days: int = WINDOW_DAYS) -> str | None:
    """The last commit at or before HEAD's committer date minus *days*, or None.

    None means the checkout does not cover the window — a clone shallower than *days*, or a
    repository younger. Reported as uncovered rather than substituted with the oldest commit
    available, which would silently rescale the number.

    Raises:
        GitError: git refused to answer.
    """
    head = _git(repo, "log", "-1", "--format=%cI", "HEAD").strip()
    if not head:
        raise GitError("HEAD has no commit")
    cutoff = datetime.fromisoformat(head) - timedelta(days=days)
    found = _git(repo, "rev-list", "-1", f"--before={cutoff.isoformat()}", "HEAD").strip()
    return found or None


def measure_commit(repo: Path, ref: str) -> Tree:
    """The in-scope modules of *ref*'s tree, measured in `module-size`'s own token unit.

    Raises:
        GitError: git refused to list or read the tree.
    """
    listing = _git(repo, "ls-tree", "-r", "-z", ref, "--", *SCOPE_ROOTS)
    paths, oids = [], []
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        if not path.endswith(".py"):
            continue
        paths.append(path)
        oids.append(meta.split()[2])
    tokens = dict(
        zip(paths, (module_tokens(text) for text in _blob_texts(repo, oids)), strict=True)
    )
    when = _git(repo, "log", "-1", "--format=%cs", ref).strip()
    return Tree(ref=_git(repo, "rev-parse", "--short", ref).strip(), when=when, tokens=tokens)


def measure_working_tree(repo: Path) -> Tree:
    """The tracked modules on disk, through `module-size`'s own reader.

    :func:`check_module_size.tracked_modules` rather than a second walk, so the two gates
    cannot disagree on scope or on a size. On disk rather than at HEAD because the
    pre-commit hook runs this before the commit exists.

    Raises:
        GitError: git refused to list the tree.
    """
    try:
        modules = tracked_modules(repo)
    except RatchetError as err:
        raise GitError(str(err)) from err
    when = _git(repo, "log", "-1", "--format=%cs", "HEAD").strip()
    return Tree(
        ref="working tree", when=when, tokens={module.path: module.tokens for module in modules}
    )


def main() -> int:
    """Print the window's growth, or why it could not be reached. Always exits 0."""
    try:
        base_ref = baseline_ref(REPO_ROOT)
        if base_ref is None:
            print(
                f"{_LABEL}: no commit older than {WINDOW_DAYS}d in this checkout, window unmeasured"
            )
            return 0
        growth = Growth(
            base=measure_commit(REPO_ROOT, base_ref),
            now=measure_working_tree(REPO_ROOT),
            days=WINDOW_DAYS,
        )
    except (GitError, ValueError) as exc:
        print(f"{_LABEL}: unmeasured - {exc}")
        return 0
    for line in report_lines(growth):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

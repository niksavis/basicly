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

from check_module_size import module_tokens, tracked_modules  # noqa: E402 - path set above
from ratchet import (  # noqa: E402 - the path above comes first
    SCOPE_ROOTS,
    RatchetError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

WINDOW_DAYS = 7

_LABEL = "tree-growth"

_BATCH_HEADER_FIELDS = 3


class GitError(Exception):
    pass


@dataclass(frozen=True)
class Tree:
    ref: str
    when: str
    tokens: Mapping[str, int]

    @property
    def total(self) -> int:
        return sum(self.tokens.values())


@dataclass(frozen=True)
class Growth:
    base: Tree
    now: Tree
    days: int

    @property
    def net(self) -> int:
        return self.now.total - self.base.total

    @property
    def new_paths(self) -> frozenset[str]:
        return frozenset(self.now.tokens) - frozenset(self.base.tokens)

    @property
    def in_new(self) -> int:
        return sum(self.now.tokens[path] for path in self.new_paths)

    @property
    def in_deleted(self) -> int:
        gone = frozenset(self.base.tokens) - frozenset(self.now.tokens)
        return -sum(self.base.tokens[path] for path in gone)

    @property
    def in_existing(self) -> int:
        return self.net - self.in_new - self.in_deleted


def report_lines(growth: Growth) -> tuple[str, str]:

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
    completed = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"git {args[0]} exited {completed.returncode}"
        raise GitError(detail)
    return completed.stdout


def _blob_texts(repo: Path, oids: Sequence[str]) -> list[str]:

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

    head = _git(repo, "log", "-1", "--format=%cI", "HEAD").strip()
    if not head:
        raise GitError("HEAD has no commit")
    cutoff = datetime.fromisoformat(head) - timedelta(days=days)
    found = _git(repo, "rev-list", "-1", f"--before={cutoff.isoformat()}", "HEAD").strip()
    return found or None


def measure_commit(repo: Path, ref: str) -> Tree:

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

    try:
        modules = tracked_modules(repo)
    except RatchetError as err:
        raise GitError(str(err)) from err
    when = _git(repo, "log", "-1", "--format=%cs", "HEAD").strip()
    return Tree(
        ref="working tree", when=when, tokens={module.path: module.tokens for module in modules}
    )


def main() -> int:
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

"""Assemble a conventional-commit envelope from engine state (basicly-kjc5.42).

A commit message is mostly derivable and only partly authored. The type follows
the bead's work class, the scope follows the touched paths, the trailing bead id
is the bead under work — and none of that needs judgment, so per design D10 it
belongs in a command rather than in an agent's context. Only the description is
free input.

The ``commit-msg`` / ``beads-commit-msg`` hooks stay the gate: this module
removes the guesswork ahead of them (and refuses a description the charset rules
would reject, naming the offending characters *before* a commit is attempted)
rather than replacing them. The description charset here mirrors
``commit-msg.py`` exactly; ``tests/test_commit.py`` pins the two together so
they cannot drift apart silently.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import loop_state
from .worktree import git

MIN_DESCRIPTION_LENGTH = 3

# Mirrors commit-msg.py: type vocabulary, the description charset, and the
# lowercase-kebab-case scope shape.
ALLOWED_TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)
_ALLOWED_DESCRIPTION_CHAR = re.compile(r"[a-z0-9 -]")
_DESCRIPTION_PATTERN = re.compile(r"^[a-z][a-z0-9 -]*[a-z0-9]$")
_SCOPE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# The br work class of the bead under work decides the commit type. `epic` is
# absent on purpose: an epic is a container, its children carry the commits.
_TYPE_BY_WORK_TYPE = {
    "bug": "fix",
    "chore": "chore",
    "feature": "feat",
    "task": "feat",
}


@dataclass(frozen=True)
class Envelope:
    """The assembled parts of one commit message."""

    type: str
    scope: str | None
    description: str
    bead: str
    breaking: bool = False
    body: str = ""

    @property
    def subject(self) -> str:
        """The single header line both commit-msg hooks validate."""
        scope = f"({self.scope})" if self.scope else ""
        breaking = "!" if self.breaking else ""
        return f"{self.type}{scope}{breaking}: {self.description} ({self.bead})"

    @property
    def message(self) -> str:
        """The full commit message: the header, plus the authored body if any."""
        return f"{self.subject}\n\n{self.body.strip()}\n" if self.body.strip() else self.subject


# --- description rules (the part an agent authors) --------------------------


def disallowed_description_chars(description: str) -> list[str]:
    """Return the distinct out-of-charset characters, in first-seen order.

    Same charset and same first-seen ordering as ``commit-msg.py``'s function of
    the same name — capitals and dots (version numbers, filenames, proper nouns)
    are the usual offenders.
    """
    bad: list[str] = []
    for char in description:
        if not _ALLOWED_DESCRIPTION_CHAR.fullmatch(char) and char not in bad:
            bad.append(char)
    return bad


def check_description(description: str) -> None:
    """Raise ``ValueError`` naming what a hook would reject, or return silently.

    Charset first and by character, because that is the failure the hook reports
    and the one that costs a round trip: an agent gets ``'.'`` named rather than
    a re-derivation of the whole rule set.
    """
    bad = disallowed_description_chars(description)
    if bad:
        rendered = ", ".join(repr(char) for char in bad)
        raise ValueError(
            f"description has disallowed character(s): {rendered} — use only lowercase "
            "letters, digits, spaces, and hyphens; put version numbers, filenames, and "
            "proper-noun capitalization in the body (--body)"
        )
    if len(description) < MIN_DESCRIPTION_LENGTH:
        raise ValueError(
            f"description must be at least {MIN_DESCRIPTION_LENGTH} characters: {description!r}"
        )
    if not _DESCRIPTION_PATTERN.fullmatch(description):
        raise ValueError(
            "description must start with a lowercase letter and end with a letter or "
            f"digit: {description!r}"
        )


# --- derivation from engine state ------------------------------------------


def derive_type(work_type: str, paths: tuple[str, ...]) -> str:
    """The conventional type for a *work_type* bead touching *paths*.

    The bead's work class decides it, with one refinement: a change made
    entirely of documentation, tests, or workflow files is that kind of change
    whatever the bead is classified as, and calling a docs-only diff ``feat``
    would be wrong in exactly the mechanical way this command exists to prevent.
    """
    if paths:
        for conventional, predicate in (
            ("ci", _is_workflow),
            ("test", _is_test),
            ("docs", _is_doc),
        ):
            if all(predicate(path) for path in paths):
                return conventional
    if work_type not in _TYPE_BY_WORK_TYPE:
        raise ValueError(
            f"cannot derive a commit type from work type {work_type!r} "
            f"(known: {', '.join(sorted(_TYPE_BY_WORK_TYPE))}); pass --type"
        )
    return _TYPE_BY_WORK_TYPE[work_type]


def _is_workflow(path: str) -> bool:
    return path.startswith(".github/workflows/")


def _is_test(path: str) -> bool:
    return path.startswith("tests/")


def _is_doc(path: str) -> bool:
    return path.startswith("docs/") or path.endswith(".md")


def _kebab(text: str) -> str:
    """The lowercase-kebab-case form of a path component, as a scope must be."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def scope_candidate(path: str) -> str | None:
    """The scope one path argues for, or None when it argues for none.

    Package modules name themselves (``src/basicly/loop.py`` → ``loop``), a test
    names its subject (``tests/test_loop.py`` → ``loop``), catalog content names
    its kind (``.basicly/core/hooks/...`` → ``hooks``), and a doc names its file
    (``docs/factory-design.md`` → ``factory-design``). Loose root files argue
    for nothing rather than for a made-up scope.
    """
    match path.split("/"):
        case [".github", "workflows", *_]:
            scope = "ci"
        case [".beads", *_]:
            scope = "beads"
        case [".basicly", "core", kind, *_] | [".basicly-local", kind, *_]:
            scope = _kebab(kind)
        case ["docs", *_, name]:
            scope = _kebab(Path(name).stem)
        case ["src", package, "__init__.py"]:
            scope = _kebab(package)
        case ["src", _package, name] if name.endswith(".py"):
            scope = _kebab(Path(name).stem)
        case ["src", _package, subpackage, *_]:
            scope = _kebab(subpackage)
        case ["tests", name]:
            scope = _kebab(Path(name).stem.removeprefix("test_"))
        case ["tests", directory, *_]:
            scope = _kebab(directory.removeprefix("test_"))
        case _:
            scope = None
    return scope


def derive_scope(weights: dict[str, int]) -> str | None:
    """The scope of the heaviest-touched area, or None when nothing argues for one.

    *weights* maps a path to its churn weight (see :func:`staged_weights`). Test
    paths are ignored while any non-test path has a candidate, so a change plus
    its regression test lands under the changed module's scope. Ties break
    alphabetically — an arbitrary but stable answer beats a scope that depends on
    argument order.
    """
    counted = {p: w for p, w in weights.items() if not _is_test(p)} or weights
    totals: dict[str, int] = {}
    for path, weight in counted.items():
        candidate = scope_candidate(path)
        if candidate:
            totals[candidate] = totals.get(candidate, 0) + weight
    if not totals:
        return None
    return min(totals, key=lambda scope: (-totals[scope], scope))


# --- git and tracker reads -------------------------------------------------


def staged_weights(repo_root: Path) -> dict[str, int]:
    """Staged paths mapped to their churn weight (one plus lines added/removed).

    Churn decides which area a mixed change is *about*: a one-line touch in
    ``cli.py`` alongside a new module should not outvote the module. Binary
    paths report ``-`` and fall back to the flat per-path weight. Renames are
    left un-detected so every row carries one plain path to map to a scope.
    """
    out = git(["diff", "--cached", "--numstat", "--no-renames"], cwd=repo_root).stdout
    weights: dict[str, int] = {}
    for line in out.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        added, removed, path = fields[0], fields[1], fields[-1]
        churn = sum(int(value) for value in (added, removed) if value.isdigit())
        weights[path] = 1 + churn
    return weights


def _beads_dir(repo_root: Path) -> Path:
    """The active beads dir, following br's git-ignored ``redirect`` file.

    The same resolution ``beads-commit-msg.py`` does, so this command reads the
    tracker file the gate will read: a harness worktree shares the base
    checkout's tracker via ``.beads/redirect``.
    """
    beads = Path(repo_root) / ".beads"
    redirect = beads / "redirect"
    if redirect.is_file():
        try:
            target = Path(redirect.read_text(encoding="utf-8").strip())
        except OSError:
            return beads
        if target.is_dir():
            return target
    return beads


def _tracker_records(repo_root: Path) -> list[dict]:
    """Every issue record in the beads JSONL, or an empty list when there is none."""
    issues = _beads_dir(repo_root) / "issues.jsonl"
    if not issues.is_file():
        return []
    records: list[dict] = []
    for raw_line in issues.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            records.append(record)
    return records


def bead_under_work(repo_root: Path, branch: str) -> str:
    """The bead id bound to *branch*, from its recorded worktree binding.

    The loop stamps ``worktree:<name>:<branch>`` on the bead it provisions a
    worktree for, so the branch a build commits on identifies its bead with no
    guessing and no naming convention to reverse. A closed bead keeps its
    binding, so an open match wins when a branch name was reused.
    """
    matches = [
        record
        for record in _tracker_records(repo_root)
        if (binding := loop_state.parse_worktree_ref(record.get("external_ref")))
        and binding.branch == branch
    ]
    open_matches = [record for record in matches if record.get("status") != "closed"]
    for candidates in (open_matches, matches):
        if len(candidates) == 1:
            return str(candidates[0]["id"])
    if not matches:
        raise ValueError(
            f"no bead is bound to branch {branch!r} in the tracker, so the commit's "
            "bead id cannot be derived; pass --issue <id> (the loop binds a bead when "
            "it provisions the worktree)"
        )
    ids = ", ".join(sorted(str(record["id"]) for record in matches))
    raise ValueError(f"branch {branch!r} is bound to more than one bead ({ids}); pass --issue <id>")


def _record_for(repo_root: Path, bead: str) -> dict:
    """The tracker record for *bead*, or a ``ValueError`` the gate would also raise."""
    for record in _tracker_records(repo_root):
        if record["id"] == bead:
            return record
    raise ValueError(
        f"unknown bead id {bead!r}: not in the tracker's issues.jsonl — the "
        "beads-commit-msg hook reads that file and would reject the commit "
        "(run 'br sync --flush-only' if the bead was only just created)"
    )


# --- assembly --------------------------------------------------------------


def assemble(  # noqa: PLR0913 — every override is one independently overridable part
    repo_root: Path,
    description: str,
    *,
    bead: str | None = None,
    commit_type: str | None = None,
    scope: str | None = None,
    breaking: bool = False,
    body: str = "",
) -> Envelope:
    """Assemble the envelope for the staged change, validating every part.

    Derives what state determines — the bead from the branch's worktree binding,
    the type from that bead's work class, the scope from the staged paths — and
    takes only *description* (and an optional *body*) as authored input. Every
    derived part can be overridden explicitly; an override is validated the same
    way, so no path through here can emit a subject the hooks would reject.

    Raises ``ValueError`` with the offending part named, before any commit.
    """
    check_description(description)
    if commit_type is not None and commit_type not in ALLOWED_TYPES:
        raise ValueError(
            f"unknown commit type {commit_type!r}; expected one of {', '.join(ALLOWED_TYPES)}"
        )
    if scope is not None and not _SCOPE_PATTERN.fullmatch(scope):
        raise ValueError(f"scope must be lowercase-kebab-case: {scope!r}")

    repo_root = Path(repo_root)
    if bead is None:
        bead = bead_under_work(repo_root, current_branch(repo_root))
    record = _record_for(repo_root, bead)

    weights = staged_weights(repo_root)
    return Envelope(
        type=commit_type or derive_type(str(record.get("issue_type", "")), tuple(weights)),
        scope=scope if scope is not None else derive_scope(weights),
        description=description,
        bead=bead,
        breaking=breaking,
        body=body,
    )


def current_branch(repo_root: Path) -> str:
    """The branch the commit would land on."""
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root).stdout.strip()


def has_staged_changes(repo_root: Path) -> bool:
    """True when there is something staged to commit."""
    return git(["diff", "--cached", "--quiet"], cwd=repo_root, check=False).returncode != 0


@dataclass(frozen=True)
class CommitResult:
    """The outcome of handing the assembled message to ``git commit``."""

    returncode: int
    output: str

    @property
    def committed(self) -> bool:
        """True when git accepted the commit (no hook rejected it)."""
        return self.returncode == 0


def run_commit(repo_root: Path, envelope: Envelope) -> CommitResult:
    """Commit the staged change with *envelope*'s message, hooks and all.

    The hooks are the floor and stay in the path: this passes the assembled
    message to ``git commit`` and reports what git said, so a rejection is still
    a rejection (never a bypass).
    """
    proc = git(["commit", "-m", envelope.message], cwd=repo_root, check=False)
    output = "".join(part for part in (proc.stdout, proc.stderr) if part)
    return CommitResult(proc.returncode, output.strip())

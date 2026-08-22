"""Assemble a conventional-commit envelope from engine state (basicly-kjc5.42).

A commit message is mostly derivable and only partly authored. The type follows
the bead's work class, the scope follows the touched paths, the trailing bead id
is the bead under work — and none of that needs judgment, so per design D10 it
belongs in a command rather than in an agent's context. Only the description is
free input.

The ``commit-msg`` / ``tracker-commit-msg`` hooks stay the gate: this module
removes the guesswork ahead of them (and refuses a description the charset rules
would reject, naming the offending characters *before* a commit is attempted)
rather than replacing them. The description charset here mirrors
``commit-msg.py`` exactly; ``tests/test_commit.py`` pins the two together so
they cannot drift apart silently.

The envelope also carries the dispatch's model provenance as a git trailer
(basicly-kjc5.60). A run record lives in the self-ignored ``.basicly/usage/`` and
does not survive a clone; the commit does, so the model that produced a landed
change is evidence anyone who fetches the history can read.
"""

from __future__ import annotations

import contextlib
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from . import gate_failure, loop_state, run_record, tracker
from .worktree import git, main_checkout

MIN_DESCRIPTION_LENGTH = 3

# The trailer carrying model provenance. Same name ``merge.py`` stamps on a
# landing commit (basicly-140a) on purpose: one trailer name with one meaning —
# the model the harness *pinned* for the dispatch — so a reader of
# ``git log --format='%(trailers)'`` gets the same fact off a work commit and off
# the merge that landed it.
MODEL_TRAILER = "Harness-Model"

# The dispatch phases whose agent writes code, and therefore commits: a leaf's
# build (``loop._run_agent``) and a supervised lane (``supervise``). Recorded
# against the same bead are ``validate`` (the rubric judge) and ``decide`` (the
# decider), either of which can be the *most recent* dispatch by the time the
# build agent commits — a decision answered mid-build is exactly that sequence.
# Neither wrote the code, so neither may supply the trailer.
_WORK_PHASES = ("build", "lane")

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
    # The model the dispatch resolved, verbatim; None when it pinned none. Never
    # an empty string or a placeholder — see :func:`dispatch_model`.
    model: str | None = None

    @property
    def subject(self) -> str:
        """The single header line both commit-msg hooks validate."""
        scope = f"({self.scope})" if self.scope else ""
        breaking = "!" if self.breaking else ""
        return f"{self.type}{scope}{breaking}: {self.description} ({self.bead})"

    @property
    def trailers(self) -> tuple[str, ...]:
        """The engine-stamped trailer lines, in emission order."""
        return (f"{MODEL_TRAILER}: {self.model}",) if self.model else ()

    @property
    def message(self) -> str:
        """The full commit message: the header, the authored body, then the trailers.

        The trailers are their own final paragraph, which is where git looks for
        them (``git interpret-trailers``). Both commit-msg hooks read the first
        line only, so nothing appended here can change their verdict —
        ``tests/test_commit.py`` runs the real hook over the assembled message.
        """
        paragraphs = [self.subject]
        if self.body.strip():
            paragraphs.append(self.body.strip())
        if self.trailers:
            paragraphs.append("\n".join(self.trailers))
        # A single-paragraph message is the bare subject, no trailing newline: git
        # adds one, and the existing callers compare against exactly that.
        return "\n\n".join(paragraphs) + ("\n" if len(paragraphs) > 1 else "")


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
    # ``site/`` is the published website; it moved out of ``docs/`` so that
    # GitHub Pages stops dictating the documentation layout, and it stays a doc
    # path here because a landing-page edit is documentation either way.
    return path.startswith(("docs/", "site/")) or path.endswith(".md")


def _kebab(text: str) -> str:
    """The lowercase-kebab-case form of a path component, as a scope must be."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def scope_candidate(path: str) -> str | None:
    """The scope one path argues for, or None when it argues for none.

    Package modules name themselves (``src/basicly/loop.py`` → ``loop``), a test
    names its subject (``tests/test_loop.py`` → ``loop``), catalog content names
    its kind (``.basicly/core/hooks/...`` → ``hooks``), and a doc names its file
    (``docs/requirements/factory-loop.md`` → ``factory-loop``). The website names
    itself rather than its files, because ``index`` is not a scope anyone reads.
    Loose root files argue for nothing rather than for a made-up scope.
    """
    match path.split("/"):
        case [".github", "workflows", *_]:
            scope = "ci"
        case ["site", *_]:
            scope = "site"
        case [".basicly", "ledger", *_]:
            scope = "ledger"
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


def bead_under_work(repo_root: Path, branch: str) -> str:
    """The bead id bound to *branch*, from its recorded worktree binding.

    The loop stamps ``worktree:<name>:<branch>`` on the bead it provisions a
    worktree for, so the branch a build commits on identifies its bead with no
    guessing and no naming convention to reverse. A closed bead keeps its
    binding, so an open match wins when a branch name was reused.
    """
    matches = [
        record
        for record in tracker.all_records(repo_root)
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
    for record in tracker.all_records(repo_root):
        if record["id"] == bead:
            return record
    raise ValueError(
        f"unknown bead id {bead!r}: no event in the committed ledger names it — the "
        "tracker-commit-msg hook folds the same log and would reject the commit"
    )


# --- model provenance (basicly-kjc5.60) ------------------------------------


def _records_root(repo_root: Path) -> Path:
    """Which checkout's ``.basicly/usage/`` holds the run records for *repo_root*.

    The same shape as the tracker's own redirect, for the same reason: a harness worktree
    has no telemetry of its own, because the engine that dispatched into it runs
    from the base checkout and records there. Reading the main checkout is what
    lets a commit made *inside* the worktree see the dispatch that produced it.
    """
    if (repo_root / run_record.RUN_RECORDS_FILE).is_file():
        return repo_root
    # Not a git repo (a unit test's tmp dir), or no git at all: there is simply no
    # dispatch to attribute, which the caller already handles as "no trailer".
    with contextlib.suppress(OSError, RuntimeError):
        return main_checkout(repo_root)
    return repo_root


def _work_dispatch(repo_root: Path, bead: str) -> dict | None:
    """The most recent code-writing dispatch recorded for *bead*, or None.

    Telemetry is read raw here rather than through ``latest_record``: the latest
    record of any kind is the wrong one (see :data:`_WORK_PHASES`), and only the
    model fields are wanted.
    """
    data = run_record.load_run_records(_records_root(repo_root))
    history = (data or {}).get(bead)
    if not isinstance(history, list):
        return None
    work = [
        entry for entry in history if isinstance(entry, dict) and entry.get("phase") in _WORK_PHASES
    ]
    return work[-1] if work else None


def dispatch_model(repo_root: Path, bead: str) -> str | None:
    """The model *bead*'s work dispatch resolved, or None when none was asked for.

    Read off the recorded provenance (basicly-kjc5.59) rather than re-resolved:
    re-resolving would read the model map a second time and could answer
    differently from the dispatch that actually ran — and the commit is evidence
    of that dispatch, not of the config as it stands now. The *pinned* model is
    the answer even when the adapter reports a different observed one: the trailer
    states what the harness asked for, in the surface spelling it asked with, and
    the ``model_mismatch`` field of the run record is where a divergence belongs
    (one trailer cannot carry the several models a session may switch between).

    Raises ``ValueError`` when a tier *was* asked for and no model came of it —
    ``resolve_model`` reports that as ``tier_honoured`` false, meaning the family
    had no flag to pin the tier onto and the dispatch ran on the session's own
    model. The envelope refuses there instead of emitting an empty or placeholder
    trailer: an unanswerable provenance question must not be answered wrongly. A
    dispatch that asked for no model at all is the different fact — nothing was
    resolved because nothing was demanded — and carries no trailer rather than
    blocking every commit in a repo that declares no tier.
    """
    entry = _work_dispatch(repo_root, bead)
    if entry is None:
        return None
    model = entry.get("model")
    if isinstance(model, str) and model:
        return model
    tier = entry.get("model_tier")
    if isinstance(tier, str) and tier:
        source = entry.get("model_source") or "config"
        raise ValueError(
            f"the dispatch for {bead} asked for model tier {tier!r} ({source}) but no "
            f"model was pinned, so the {MODEL_TRAILER} trailer would have to be empty; "
            "give the runner a tier its family can pin (or an explicit "
            "[[runner.agents]] model) and re-dispatch, rather than landing a commit "
            "whose model provenance is unknown"
        )
    return None


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
    the type from that bead's work class, the scope from the staged paths, the
    model trailer from the dispatch's recorded provenance — and takes only
    *description* (and an optional *body*) as authored input. Every derived part
    can be overridden explicitly; an override is validated the same way, so no
    path through here can emit a subject the hooks would reject.

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
        model=dispatch_model(repo_root, bead),
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


# --- salvaging a killed dispatch's worktree (basicly-yvx9) ------------------


# What the harness says when it commits for an agent that never got the chance.
# Plain on purpose: the subject a reader meets in ``git log`` has to say the run
# was cut short, because the agent's own sign-off is exactly what did not happen.
SALVAGE_DESCRIPTION = "salvage the work a killed runner left uncommitted"

# How much of git's own refusal is quoted back. A rejected commit can end in a
# hook's full report; the caller puts this in a one-line block reason.
_REFUSAL_CHARS = 200

# The conventional commit-body wrap.
_BODY_WIDTH = 72


@dataclass(frozen=True)
class Salvage:
    """What became of the uncommitted work in a killed dispatch's worktree."""

    # "committed" — the tree is a commit now, and the landing can judge it.
    # "empty"     — there was no uncommitted work to rescue.
    # "refused"   — git or a hook declined; the tree is left exactly as it was.
    status: str
    detail: str

    @property
    def committed(self) -> bool:
        """True only when a commit now exists — the one thing a caller may act on."""
        return self.status == "committed"


def salvage(worktree_root: Path, bead: str, *, reason: str) -> Salvage:
    """Commit whatever *worktree_root* holds uncommitted, for an agent that was killed.

    Committing is the agent's last step and a hard kill is precisely the event
    that removes it. Three runs in this repo's ledger were killed by ``[runner]
    runner_timeout`` holding their whole finished diff, and the harness discarded
    every one of them — 47.8M tokens, nine tenths of all the work this repo has
    paid for that never landed — while carefully draining a few kilobytes of
    their stdout (basicly-yvx9). The rescue is harness-side because it has to be:
    signalling earlier so the agent could commit itself would depend on a
    third-party CLI treating SIGTERM as "commit now", which none of them promise.

    Nothing here judges the diff. A timeout is the harness's own decision, not
    evidence against the change, and verify is the authority on a diff where a
    clock is not — so the commit only makes the work *judgeable*: a red gate then
    reworks the lane with real findings, where an uncommitted tree could only ever
    produce "not-ready" and a second full dispatch for work already done.

    The hooks stay in the path (:func:`run_commit`), so a tree the gates reject is
    reported ``refused`` and left as the kill left it. *reason* names the kill and
    goes in the commit body, where it survives the run record. Never raises: the
    caller is already handling a killed run, and a failed rescue must not become a
    second failure.
    """
    root = Path(worktree_root)
    try:
        staged = git(["add", "--all"], cwd=root, check=False)
        if staged.returncode != 0:
            return Salvage("refused", f"the worktree could not be staged: {_tail(staged.stderr)}")
        if not has_staged_changes(root):
            return Salvage("empty", "the worktree held no uncommitted work")
        envelope = assemble(root, SALVAGE_DESCRIPTION, bead=bead, body=_salvage_body(reason))
        result = run_commit(root, envelope)
        if not result.committed:
            rejection = _rejection(root, result.output)
            return Salvage("refused", f"the salvage commit was rejected: {rejection}")
        head = git(["rev-parse", "--short", "HEAD"], cwd=root, check=False).stdout.strip()
    except (RuntimeError, OSError, ValueError) as exc:
        return Salvage("refused", f"the worktree could not be committed: {exc}")
    return Salvage("committed", f"the worktree was committed as {head or 'a new commit'}")


def _salvage_body(reason: str) -> str:
    """The body that keeps the kill visible to whoever reads this commit later.

    Wrapped, because this is the one part of the salvage a human meets in ``git
    log`` — and a run record does not survive a clone, where the commit does.
    """
    return textwrap.fill(
        f"The runner was killed by the harness ({reason}) with this work "
        "uncommitted in its worktree, so the harness committed it: a timeout is "
        "the harness's own decision and is not evidence against the diff. No "
        "agent signed this off — the landing's verify gate judges it, and the "
        "kill itself is reported separately.",
        width=_BODY_WIDTH,
    )


def _tail(output: str) -> str:
    """The last line of a command's output, capped, for a one-line refusal reason."""
    lines = (output or "").strip().splitlines()
    detail = lines[-1] if lines else "no output"
    return detail if len(detail) <= _REFUSAL_CHARS else detail[:_REFUSAL_CHARS] + "…"


def _rejection(root: Path, output: str) -> str:
    """Why the salvage commit was refused, naming the check when a hook refused it.

    :func:`_tail` is wrong for a gated commit and was measurably worse than silence here:
    the last line of a hook chain belongs to the hook that ran *last*, so this reported
    ``protect-generated-commit...Passed`` as the rejection reason and sent a reader to
    audit a check that had passed (basicly-fi1i7z). It stays for the staging failure above,
    where git writes one line and no chain runs.
    """
    return gate_failure.summarise(output, repo_root=root) or _tail(output)

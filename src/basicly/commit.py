from __future__ import annotations

import contextlib
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from . import checkout, loop_state, run_record, tracker
from .worktree import git, main_checkout

MIN_DESCRIPTION_LENGTH = 3

MODEL_TRAILER = "Harness-Model"

_WORK_PHASES = ("build", "lane")

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

_TYPE_BY_WORK_TYPE = {
    "bug": "fix",
    "chore": "chore",
    "feature": "feat",
    "task": "feat",
}


@dataclass(frozen=True)
class Envelope:
    type: str
    scope: str | None
    description: str
    bead: str
    breaking: bool = False
    body: str = ""
    model: str | None = None

    @property
    def subject(self) -> str:
        scope = f"({self.scope})" if self.scope else ""
        breaking = "!" if self.breaking else ""
        return f"{self.type}{scope}{breaking}: {self.description} ({self.bead})"

    @property
    def trailers(self) -> tuple[str, ...]:
        return (f"{MODEL_TRAILER}: {self.model}",) if self.model else ()

    @property
    def message(self) -> str:

        paragraphs = [self.subject]
        if self.body.strip():
            paragraphs.append(self.body.strip())
        if self.trailers:
            paragraphs.append("\n".join(self.trailers))
        return "\n\n".join(paragraphs) + ("\n" if len(paragraphs) > 1 else "")


def disallowed_description_chars(description: str) -> list[str]:

    bad: list[str] = []
    for char in description:
        if not _ALLOWED_DESCRIPTION_CHAR.fullmatch(char) and char not in bad:
            bad.append(char)
    return bad


def check_description(description: str) -> None:

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


def derive_type(work_type: str, paths: tuple[str, ...]) -> str:

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
    return path.startswith(("docs/", "site/")) or path.endswith(".md")


def _kebab(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def scope_candidate(path: str) -> str | None:

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

    counted = {p: w for p, w in weights.items() if not _is_test(p)} or weights
    totals: dict[str, int] = {}
    for path, weight in counted.items():
        candidate = scope_candidate(path)
        if candidate:
            totals[candidate] = totals.get(candidate, 0) + weight
    if not totals:
        return None
    return min(totals, key=lambda scope: (-totals[scope], scope))


def staged_weights(repo_root: Path) -> dict[str, int]:

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
    for record in tracker.all_records(repo_root):
        if record["id"] == bead:
            return record
    raise ValueError(
        f"unknown bead id {bead!r}: no event in the committed ledger names it — the "
        "tracker-commit-msg hook folds the same log and would reject the commit"
    )


def _records_root(repo_root: Path) -> Path:

    if (repo_root / run_record.RUN_RECORDS_FILE).is_file():
        return repo_root
    with contextlib.suppress(OSError, RuntimeError):
        return main_checkout(repo_root)
    return repo_root


def _work_dispatch(repo_root: Path, bead: str) -> dict | None:

    data = run_record.load_run_records(_records_root(repo_root))
    history = (data or {}).get(bead)
    if not isinstance(history, list):
        return None
    work = [
        entry for entry in history if isinstance(entry, dict) and entry.get("phase") in _WORK_PHASES
    ]
    return work[-1] if work else None


def dispatch_model(repo_root: Path, bead: str) -> str | None:

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
    return git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root).stdout.strip()


def has_staged_changes(repo_root: Path) -> bool:
    return git(["diff", "--cached", "--quiet"], cwd=repo_root, check=False).returncode != 0


@dataclass(frozen=True)
class CommitResult:
    returncode: int
    output: str

    @property
    def committed(self) -> bool:
        return self.returncode == 0


def run_commit(repo_root: Path, envelope: Envelope) -> CommitResult:

    proc = git(["commit", "-m", envelope.message], cwd=repo_root, check=False)
    output = "".join(part for part in (proc.stdout, proc.stderr) if part)
    return CommitResult(proc.returncode, output.strip())


SALVAGE_DESCRIPTION = "salvage the work a killed runner left uncommitted"

_REFUSAL_CHARS = 200

_BODY_WIDTH = 72


@dataclass(frozen=True)
class Salvage:
    status: str
    detail: str

    @property
    def committed(self) -> bool:
        return self.status == "committed"


def salvage(worktree_root: Path, bead: str, *, reason: str) -> Salvage:

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

    return textwrap.fill(
        f"The runner was killed by the harness ({reason}) with this work "
        "uncommitted in its worktree, so the harness committed it: a timeout is "
        "the harness's own decision and is not evidence against the diff. No "
        "agent signed this off — the landing's verify gate judges it, and the "
        "kill itself is reported separately.",
        width=_BODY_WIDTH,
    )


def _tail(output: str) -> str:
    lines = (output or "").strip().splitlines()
    detail = lines[-1] if lines else "no output"
    return detail if len(detail) <= _REFUSAL_CHARS else detail[:_REFUSAL_CHARS] + "…"


def _rejection(root: Path, output: str) -> str:

    return checkout.gate_refusal(output, repo_root=root) or _tail(output)

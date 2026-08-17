"""Release automation: version, changelog, annotated tag — and nothing outward-facing.

Component 9 (basicly-kjc5.12). Every step here is deterministic and locally
reversible, which is exactly why the engine may own it: a version bump, a
regenerated projection, a changelog section, a commit and an annotated tag are all
undoable on one machine.

The boundary is deliberate and the design's, not an omission: ``git push origin
<tag>`` is where a release becomes public and irreversible — it triggers the
release workflow and publishes a GitHub release — so it stays a human step. This
module refuses to push, and says so in what it prints.

Autonomous invocation (D3) is refused unless the session carries an **L3** grant
that is inside its spend ceiling and whose lights-out preconditions are green.
That is stricter than the rest of the engine on purpose: a release is the one
action whose blast radius reaches every consumer, so "no grant" and "L1/L2" both
mean a human runs it.

**Everything a gate would reject is refused before the first byte is written.** The
commit subject is validated against the repo's own commit-msg rules, the date
against the changelog heading format, the tag against existing tags, the checkout
against being a linked worktree. That ordering is the whole design: a release that
fails halfway leaves a bumped version and a regenerated projection with no commit,
and recovering from that needs a destructive git command.

**A tag may not publish an unexercised capability claim** (basicly-irrm). Every
capability the repo *declares* it ships must have at least one recorded execution in
the ledgers already on disk, and one that has none refuses the release naming it.
:mod:`basicly.capability_proof` answers that question; :func:`blocking_reasons`
collects its reasons beside every other refusal.

One precondition is deliberately *not* re-run here: the deterministic verify suite.
The `release-process` skill opens with "confirm required checks pass", and they are
— by the `pre-commit` hooks on this run's own commit and by `pre-push` on the push
that publishes it. Re-running `verify --mode full` inside the release would add
minutes to a command whose output is local-only until a human pushes, and would
still not be the check that gates publication.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import commit as commit_mod
from . import merge, policy, worktree
from .capability_proof import unexercised_capabilities

if TYPE_CHECKING:
    from .config import PolicyConfig

# The version is single-sourced here and flows into every generated header
# (`renderers.common.generated_header`), so a bump is one edit plus a regeneration.
VERSION_FILE = Path("src") / "basicly" / "__init__.py"
VERSION_RE = re.compile(r'^__version__ = "(?P<version>\d+\.\d+\.\d+)"$', re.MULTILINE)

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Files carrying a `@vX.Y.Z` install pin a consumer copies. None are generated, so
# a bump has to rewrite them and `basicly check` cannot catch a stale one. The two
# bootstrap shims matter as much as the docs: README documents their exact
# `--ref v...` / `-Ref v...` invocations, so missing them leaves the two halves of
# one instruction disagreeing.
#
# The how-to pages are a **glob**, and the enumeration is what failed. Two of them
# carried a pin four releases stale, because a page written after this tuple was
# last edited is a page the release never rewrites, and nothing reads a list to
# notice. A directory that may grow a pinned page is declared as the directory.
PIN_FILES = (
    Path("README.md"),
    Path("site") / "index.html",
    Path(".scripts") / "bootstrap.sh",
    Path(".scripts") / "bootstrap.ps1",
)
PIN_GLOBS = ("docs/how-to/*.md",)

# The tutorial is deliberately absent from both. It quotes the engine version in its
# recorded transcripts, so bumping only its install command leaves the page internally
# inconsistent — it needs re-execution against a fresh repo, which is `basicly-imnu.2`,
# not a rewrite.
# Word-boundary-ish: `v0.5.1` must not match inside `v0.5.10`, and a trailing
# period is prose punctuation rather than part of the version, so it still counts.
PIN_RE_TEMPLATE = r"(?<![\w.])v{version}(?!\w|\.\d)"

CHANGELOG_SCRIPT = Path(".scripts") / "generate_release_changelog.py"
CHANGELOG_FILE = Path("CHANGELOG.md")

# One file per lane, assembled here (basicly-4746). A lane records its user-facing
# change as `changelog.d/<bead-id>.<category>.md` instead of editing CHANGELOG.md.
# The filename carries the bead id, so it is unique by construction and two lanes
# cannot write the same file — the collision becomes *impossible* rather than
# detected. The answer it replaces declared CHANGELOG.md in `[worktree]
# append_only_paths`, which serialized every lane that touched it and still trailed
# the next unenumerated shared file: three of four unattended-run attempts failed on
# two lanes at one anchor, each in a different file nobody had enumerated.
FRAGMENT_DIR = Path("changelog.d")

# The directory's own documentation, not a lane's entry.
FRAGMENT_DOC = "README.md"

# Keep a Changelog's section set, in the order a dated section lists them. This
# ordering is the deterministic half of assembly — category first, then filename —
# so two machines whose directory listings differ still produce identical output.
FRAGMENT_CATEGORIES = ("added", "changed", "deprecated", "removed", "fixed", "security")

# Fragments are folded into this body and the generator promotes it into the dated
# section, so assembly reuses the promotion the release already performs instead of
# becoming a second writer of the same text. Pinned against the generator's own
# constant by ``test_release_changelog``.
UNRELEASED_HEADING = "## [Unreleased]"

# The changelog heading and the release workflow key on this exact date format.
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Dotless by necessity: the repo's commit-msg gate allows only lowercase letters,
# digits, spaces and hyphens in a description, so the version goes in the body.
# `commit.check_description` is asserted against this at plan time, so the rule and
# this string can never drift apart silently.
COMMIT_SUBJECT = "chore(release): bump version refresh install pins and regenerate projections"


@dataclass(frozen=True)
class PinSite:
    """One file that pins the release tag, and how many times it does."""

    path: Path
    occurrences: int


@dataclass(frozen=True)
class ChangelogFragment:
    """One lane's changelog entry, and the section it assembles under."""

    path: Path
    category: str


@dataclass(frozen=True)
class ReleasePlan:
    """Everything a release will change, computed before anything is written."""

    current_version: str
    version: str
    date: str
    pins: tuple[PinSite, ...]
    # In assembly order. Defaulted so a caller that only cares about the version
    # (the CLI's own tests) still constructs a plan in one line. The *misnamed* files
    # are deliberately not a second field: only :func:`blocking_reasons` reads them,
    # and a record field with no consumer beyond its own module is the shape
    # ``wired-or-deleted`` rejects — it re-scans instead.
    fragments: tuple[ChangelogFragment, ...] = ()

    @property
    def tag(self) -> str:
        """The annotated tag for this release."""
        return f"v{self.version}"

    @property
    def current_tag(self) -> str:
        """The tag the working tree currently pins."""
        return f"v{self.current_version}"


@dataclass(frozen=True)
class ReleaseResult:
    """What a release run did, or would have done under ``dry_run``."""

    plan: ReleasePlan
    # Human-readable step lines, in execution order.
    steps: tuple[str, ...]
    dry_run: bool
    tagged: bool
    # Non-empty when the run refused; nothing was written in that case.
    refusals: tuple[str, ...] = ()

    @property
    def refused(self) -> bool:
        """True when preconditions stopped the run before it wrote anything."""
        return bool(self.refusals)


def commit_message(plan: ReleasePlan, issue_id: str) -> str:
    """The release commit message: dotless subject, version in the body.

    The beads id goes on its own trailing line, which is where the
    ``tracker-commit-msg`` hook looks and, unlike the subject, cannot push a dot into
    the description.
    """
    return (
        f"{COMMIT_SUBJECT}\n\n"
        f"Release {plan.tag} dated {plan.date}, up from {plan.current_tag}. The tag "
        f"is annotated locally and deliberately not pushed.\n\n"
        f"{issue_id}"
    )


def _git(repo_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in *repo_root* through the shared helper.

    ``worktree.run`` rather than a local ``subprocess.run``: it raises with the
    command's own stderr attached (a hook rejection is useless without it) and it
    pins ``encoding="utf-8"``, which Windows otherwise resolves to cp1252.
    """
    return worktree.run(["git", "-C", str(repo_root), *args], check=check)


def pin_paths(repo_root: Path) -> tuple[Path, ...]:
    """Every repo-relative file a version bump has to rewrite, globs expanded.

    Sorted and de-duplicated, so a release plan reads the same on any filesystem.
    """
    found = dict.fromkeys(PIN_FILES)
    for pattern in PIN_GLOBS:
        for path in sorted(repo_root.glob(pattern)):
            found[path.relative_to(repo_root)] = None
    return tuple(found)


def read_version(repo_root: Path) -> str:
    """The version currently single-sourced in ``src/basicly/__init__.py``."""
    text = (repo_root / VERSION_FILE).read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    if match is None:
        raise SystemExit(f'no `__version__ = "X.Y.Z"` line found in {VERSION_FILE.as_posix()}')
    return match.group("version")


def _parse(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def _pin_re(version: str) -> re.Pattern[str]:
    return re.compile(PIN_RE_TEMPLATE.format(version=re.escape(version)))


def scan_fragments(repo_root: Path) -> tuple[tuple[ChangelogFragment, ...], tuple[Path, ...]]:
    """Every lane fragment in assembly order, plus every file whose name does not parse.

    A file that does not parse is *reported*, never skipped. Skipping it would drop a
    lane's release note on the floor with nothing to notice — the same silent-loss
    shape the anchor collisions had, just one step later.

    Ordering is ``(category, filename)`` and nothing else: the directory listing order
    differs by filesystem, and a release reproduced on another machine has to produce
    the same section text.
    """
    directory = repo_root / FRAGMENT_DIR
    if not directory.is_dir():
        return (), ()
    fragments: list[ChangelogFragment] = []
    misnamed: list[Path] = []
    for path in sorted(directory.glob("*.md"), key=lambda item: item.name):
        if path.name == FRAGMENT_DOC:
            continue
        bead, _, category = path.stem.rpartition(".")
        if not bead or category not in FRAGMENT_CATEGORIES:
            misnamed.append(path.relative_to(repo_root))
            continue
        fragments.append(ChangelogFragment(path=path.relative_to(repo_root), category=category))
    fragments.sort(key=lambda item: (FRAGMENT_CATEGORIES.index(item.category), item.path.name))
    return tuple(fragments), tuple(misnamed)


def plan_release(repo_root: Path, version: str, *, date: str | None = None) -> ReleasePlan:
    """Compute the release plan for *version* without touching the tree.

    *date* defaults to today. It is a parameter rather than a lookup so a caller
    (and every test) can pin it: the changelog heading embeds it, and a release
    reproduced tomorrow must be able to produce the same text.
    """
    if not SEMVER_RE.match(version):
        raise SystemExit(f"version must be X.Y.Z, got {version!r}")
    current = read_version(repo_root)
    pin_re = _pin_re(current)
    pins = []
    for rel in pin_paths(repo_root):
        path = repo_root / rel
        if not path.exists():
            continue
        hits = len(pin_re.findall(path.read_text(encoding="utf-8")))
        if hits:
            pins.append(PinSite(path=rel, occurrences=hits))
    fragments, _misnamed = scan_fragments(repo_root)
    return ReleasePlan(
        current_version=current,
        version=version,
        # UTC, matching `usage.py`: two maintainers in different zones must not stamp the
        # same release with different days.
        date=date or datetime.now(UTC).date().isoformat(),
        pins=tuple(pins),
        fragments=fragments,
    )


# --- Changelog fragments: one file per lane, assembled at release (basicly-4746) ---


def _unreleased_bounds(lines: list[str]) -> tuple[int, int] | None:
    """``(heading index, index of the next release heading)``, or None with no heading."""
    for idx, line in enumerate(lines):
        if not line.startswith(UNRELEASED_HEADING):
            continue
        end = idx + 1
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        return idx, end
    return None


def _section_end(lines: list[str], heading: str) -> int | None:
    """Where content appended to *heading*'s section belongs, or None if it is absent.

    Stops at ``## `` or ``### `` only, never at a bare ``#``: a curated entry may
    carry a fenced shell snippet, and a ``# comment`` inside one is not a heading.
    """
    for idx, line in enumerate(lines):
        if line.strip() != heading:
            continue
        end = idx + 1
        while end < len(lines) and not lines[end].startswith(("## ", "### ")):
            end += 1
        while end > idx + 1 and not lines[end - 1].strip():
            end -= 1
        return end
    return None


def _fragment_body(repo_root: Path, fragment: ChangelogFragment) -> list[str]:
    """A fragment's lines, trimmed of the blank padding an editor leaves behind."""
    text = (repo_root / fragment.path).read_text(encoding="utf-8")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _merge_unreleased(
    repo_root: Path, body: list[str], fragments: tuple[ChangelogFragment, ...]
) -> list[str]:
    """The ``[Unreleased]`` body with every fragment folded under its category heading.

    A curated section keeps its position and its content: a fragment whose category
    the operator already opened lands at the end of *that* section rather than
    opening a second one. Two ``### Fixed`` under one release heading is both a
    duplicate-sibling markdownlint failure and a section no reader can scan — and
    the hand-curated body publishing beside the fragments is the transition promise,
    not a nicety.
    """
    merged = [line.rstrip() for line in body]
    while merged and not merged[-1].strip():
        merged.pop()
    for category in FRAGMENT_CATEGORIES:
        entries = [fragment for fragment in fragments if fragment.category == category]
        if not entries:
            continue
        block: list[str] = []
        for entry in entries:
            if block:
                block.append("")
            block.extend(_fragment_body(repo_root, entry))
        heading = f"### {category.capitalize()}"
        at = _section_end(merged, heading)
        if at is None:
            if merged:
                merged.append("")
            merged.extend([heading, "", *block])
        else:
            merged[at:at] = ["", *block]
    return merged


def _assemble_fragments(repo_root: Path, plan: ReleasePlan) -> None:
    """Fold every lane's fragment into ``[Unreleased]``, then delete the files.

    A pre-step to the generator rather than a second writer of the dated section:
    the generator already promotes the ``[Unreleased]`` body into ``## vX.Y.Z``, so
    assembling into that body reuses a promotion the release performs anyway, and an
    operator who curated the changelog by hand is never broken.

    The files are deleted in the run that consumed them — a fragment left behind is
    republished by the next release — and the deletions ride the release commit's
    ``git add -A``. A failure afterwards restores them with everything else
    (:func:`_restore`).
    """
    if not plan.fragments:
        return
    path = repo_root / CHANGELOG_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    bounds = _unreleased_bounds(lines)
    if bounds is None:  # pragma: no cover - blocking_reasons refuses this before any write
        raise SystemExit(f"{CHANGELOG_FILE.as_posix()} has no {UNRELEASED_HEADING!r} heading")
    start, end = bounds
    merged = _merge_unreleased(repo_root, lines[start + 1 : end], plan.fragments)
    updated = [*lines[: start + 1], "", *merged, "", *lines[end:]]
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    for fragment in plan.fragments:
        (repo_root / fragment.path).unlink()


def _fragment_reasons(repo_root: Path) -> tuple[str, ...]:
    """Why the fragments on disk cannot be assembled, in report order.

    Every one of these is a lane's release note that would otherwise vanish, so they
    refuse the tag instead of being tidied away: the whole point of the per-lane file
    is that nothing about it is silent.

    Re-scans rather than reading the plan, so the misnamed half never has to be a
    plan field only this function would read.
    """
    fragments, misnamed = scan_fragments(repo_root)
    reasons = [
        f"changelog fragment {path.as_posix()} is not named <bead-id>.<category>.md "
        f"(category: {', '.join(FRAGMENT_CATEGORIES)}); rename it or move it out of "
        f"{FRAGMENT_DIR.as_posix()}/ — a fragment nothing can place is a release note "
        "that would be dropped"
        for path in misnamed
    ]
    reasons.extend(
        f"changelog fragment {fragment.path.as_posix()} is empty; write the entry or "
        "delete the file"
        for fragment in fragments
        if not _fragment_body(repo_root, fragment)
    )
    if fragments and _unreleased_bounds(_changelog_lines(repo_root)) is None:
        reasons.append(
            f"{len(fragments)} changelog fragment(s) to assemble but "
            f"{CHANGELOG_FILE.as_posix()} has no '{UNRELEASED_HEADING}' heading to fold "
            "them into; add it — the release promotes that body into the dated section"
        )
    return tuple(reasons)


def _changelog_lines(repo_root: Path) -> list[str]:
    """The changelog's lines, or none at all when the file does not exist yet."""
    path = repo_root / CHANGELOG_FILE
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def blocking_reasons(repo_root: Path, plan: ReleasePlan, *, issue_id: str) -> tuple[str, ...]:
    """Deterministic reasons this release cannot proceed, in report order.

    All of them are state a human fixes, never a rework-worthy failure. Checked
    together so one run reports every problem instead of one per attempt, and
    checked *before anything is written* because a release that stops halfway
    leaves a bumped version with no commit.

    The commit subject is validated here against the repo's own rules
    (:func:`commit.check_description`) rather than trusted: a release whose commit
    the ``commit-msg`` gate rejects fails after the bump, the regeneration and the
    changelog are already on disk.
    """
    reasons: list[str] = []
    if worktree.is_linked_checkout(repo_root):
        # Tags live in the common git dir, so a release from a harness worktree
        # would create a repo-wide vX.Y.Z pointing at unmerged code.
        reasons.append(
            f"refusing to release from a linked worktree ({worktree.current_branch(repo_root)}); "
            "tags are shared with the primary checkout, so run this from there on the base branch"
        )
    dirty = _git(repo_root, ["status", "--porcelain"]).stdout.strip()
    if dirty:
        # The release-process guardrail: never tag from a dirty tree, because the
        # tag would name a tree nobody can reconstruct from the commit.
        first = dirty.splitlines()[0]
        extra = f" (and {len(dirty.splitlines()) - 1} more)" if "\n" in dirty else ""
        reasons.append(f"working tree is not clean: {first}{extra}")
    if _parse(plan.version) <= _parse(plan.current_version):
        reasons.append(
            f"version must move forward: {plan.version} is not greater than "
            f"the current {plan.current_version}"
        )
    if not DATE_RE.match(plan.date):
        reasons.append(f"date must be YYYY-MM-DD for the changelog heading, got {plan.date!r}")
    existing = _git(repo_root, ["tag", "--list", plan.tag]).stdout.strip()
    if existing:
        reasons.append(f"tag {plan.tag} already exists")
    if not (repo_root / CHANGELOG_SCRIPT).exists():
        reasons.append(f"changelog generator missing: {CHANGELOG_SCRIPT.as_posix()}")
    try:
        commit_mod.check_description(COMMIT_SUBJECT.split(": ", 1)[1])
    except ValueError as exc:
        reasons.append(f"release commit subject would be rejected by the commit-msg gate: {exc}")
    known = merge.known_bead_ids(repo_root)
    if known is not None and issue_id not in known:
        # Exactly merge_worktree's stance: an unknown id is rejected by the
        # tracker-commit-msg gate, and finding that out at commit time strands
        # everything already written.
        reasons.append(
            f"unknown bead id {issue_id!r}: the committed ledger does not hold it — the "
            "tracker-commit-msg gate would reject the release commit"
        )
    reasons.extend(_fragment_reasons(repo_root))
    reasons.extend(unexercised_capabilities(repo_root))
    return tuple(reasons)


def autonomy_refusal(
    repo_root: Path,
    root_issue: str,
    config: PolicyConfig | None = None,
    *,
    shipping: str | None = None,
) -> str | None:
    """Why an *autonomous* release must refuse, or None when L3 covers it (D3).

    Deliberately stricter than every other delegated action: the grant must be
    **L3** exactly — L1 and L2 do not escalate to it — it must be inside its spend
    ceiling (the one halt predicate every other delegation consults), and the
    lights-out preconditions must be green, so one rework escalation or one missing
    fact anywhere drops the release back to a human. An interactive caller never
    reaches this: a human at a terminal *is* the authorization.

    *shipping* names the node whose required gates must be green, defaulting to
    the root. It exists because passing the root is usually **wrong**: an epic's
    own verify gate is missing until the epic closes (basicly-kjc5.39), so a root
    -scoped check refuses every release under an open epic while a grant on a
    *closed* root is already dead — between them there is no state in which an
    autonomous release succeeds. The caller names the node it actually shipped.
    """
    grant = policy.active_grant(repo_root, root_issue)
    if grant is None:
        return f"no active autonomy grant on {root_issue}; a release needs L3"
    if grant.level != "L3":
        return f"grant on {root_issue} is {grant.level}; a release needs L3"
    spend = policy.spend_status(repo_root, root_issue, grant=grant)
    if spend.halted:
        return f"grant on {root_issue} is past its spend ceiling: {spend.detail}"
    violations = policy.lights_out_violations(
        repo_root,
        root_issue,
        config or policy.load_policy(repo_root),
        shipping=shipping or root_issue,
    )
    if violations:
        return "L3 preconditions not green: " + "; ".join(violations)
    return None


def _bump_version_file(repo_root: Path, plan: ReleasePlan) -> None:
    path = repo_root / VERSION_FILE
    text = path.read_text(encoding="utf-8")
    updated = VERSION_RE.sub(f'__version__ = "{plan.version}"', text, count=1)
    path.write_text(updated, encoding="utf-8")


def _rewrite_pins(repo_root: Path, plan: ReleasePlan) -> None:
    """Rewrite every pin, then prove no *literal* occurrence of the old tag survived.

    The verification is the point — a pin the pattern fails to match ships a stale
    install command silently — but it has to be a **literal** search, not the same
    regex that just did the substitution: a pattern can never detect its own
    under-match. (Written with the regex first, and a test that fed it a
    never-matching pattern passed happily.)
    """
    pin_re = _pin_re(plan.current_version)
    for site in plan.pins:
        path = repo_root / site.path
        path.write_text(pin_re.sub(plan.tag, path.read_text(encoding="utf-8")), encoding="utf-8")
    stale = [
        site.path.as_posix()
        for site in plan.pins
        if plan.current_tag in (repo_root / site.path).read_text(encoding="utf-8")
    ]
    if stale:
        raise SystemExit(
            f"{plan.current_tag} still present after rewriting pins in: {', '.join(stale)}"
        )


def _regenerate(repo_root: Path) -> None:
    """Re-project the generated files so their headers carry the new version.

    A **subprocess**, not an in-process call: `cli` binds `__version__` with a
    from-import at module load, so regenerating in this interpreter would stamp the
    version we just replaced — `AGENTS.md`, `.claude/CLAUDE.md` and
    `.github/copilot-instructions.md` would each silently name the previous
    release, and `basicly check` would then report drift on a fresh clone. A new
    interpreter re-reads the file we just wrote.

    Invoked through ``-c`` rather than ``-m basicly`` (the package has no
    ``__main__``) or the console script (its filename is platform-dependent), and
    with *repo_root*'s ``src`` forced to the front of ``PYTHONPATH``. That last
    part is not belt-and-braces: a fresh interpreter still imports whichever
    ``basicly`` is *installed*, which is only the repo being released by
    coincidence. Exercising a release in a clone proved it — the bump landed but
    every header was stamped with the installed copy's older version, and the run
    reported success.
    """
    env = dict(os.environ)
    src = str(repo_root / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    worktree.run(
        [
            sys.executable,
            "-c",
            "import sys; from basicly.cli import main; sys.exit(main(['build']))",
        ],
        cwd=repo_root,
        env=env,
    )


def _write_changelog(repo_root: Path, plan: ReleasePlan) -> None:
    """Generate the tag's changelog section with the repo's existing generator.

    Reused rather than reimplemented: it already computes the commit delta from
    the nearest previous semantic tag and upserts the section idempotently, and it
    has its own tests.

    It promotes whatever sits under `## [Unreleased]` into the dated section — by
    this point that body holds both the prose a human curated *before* this run and
    every lane fragment :func:`_assemble_fragments` just folded into it. Curating
    afterwards cannot work: this commit and the annotated tag are one step, and the
    release workflow reads CHANGELOG.md from the tagged commit (basicly-m3od.1).
    """
    worktree.run(
        [sys.executable, str(repo_root / CHANGELOG_SCRIPT), "--tag", plan.tag, "--date", plan.date],
        cwd=repo_root,
    )


def _restore(repo_root: Path) -> None:
    """Undo the file writes after a mid-sequence failure.

    Safe and bounded because :func:`blocking_reasons` proved the tree clean before
    the first write: every modification is this run's, so restoring the tracked
    tree cannot destroy anyone's work. Without it a failed release leaves a bumped
    version behind and the next attempt refuses with "working tree is not clean",
    pushing the operator toward `git reset --hard` (see merge's rebase --abort for
    the same stance).
    """
    _git(repo_root, ["reset"], check=False)
    _git(repo_root, ["checkout", "--", "."], check=False)


def run_release(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    plan: ReleasePlan,
    *,
    issue_id: str,
    dry_run: bool = False,
    root_issue: str | None = None,
    autonomous: bool = False,
    shipping: str | None = None,
) -> ReleaseResult:
    """Produce the release up to and including the annotated tag. Never pushes.

    *issue_id* is required because the commit-msg hook rejects a commit with no
    beads id, so a release with nothing to reference could not be committed at
    all. *autonomous* asks for the D3 check against *root_issue*, with *shipping*
    naming the node whose gates must be green; an interactive run skips it because
    the human running it is the authorization.

    Under *dry_run* every step is computed and reported and nothing is written —
    the **same** refusal checks run, so a dry run is a genuine pre-flight rather
    than a different code path.

    A failure after the first write restores the tree (:func:`_restore`) and
    re-raises, so the repo is never left half-released.
    """
    refusals: list[str] = []
    if autonomous:
        if root_issue is None:
            refusals.append("autonomous release needs a session root issue for the D3 check")
        else:
            refused = autonomy_refusal(repo_root, root_issue, shipping=shipping)
            if refused is not None:
                refusals.append(refused)
    refusals.extend(blocking_reasons(repo_root, plan, issue_id=issue_id))
    if refusals:
        return ReleaseResult(
            plan=plan, steps=(), dry_run=dry_run, tagged=False, refusals=tuple(refusals)
        )

    pins = ", ".join(f"{site.path.as_posix()} ({site.occurrences})" for site in plan.pins)
    steps = [
        f"bump {VERSION_FILE.as_posix()}: {plan.current_version} -> {plan.version}",
        "regenerate projected files so their headers carry the new version",
        f"rewrite install pins {plan.current_tag} -> {plan.tag}: {pins or '(none found)'}",
    ]
    if plan.fragments:
        # Named in assembly order, so the dry run shows the section's order before
        # the section exists.
        names = ", ".join(fragment.path.name for fragment in plan.fragments)
        steps.append(
            f"assemble {len(plan.fragments)} changelog fragment(s) from "
            f"{FRAGMENT_DIR.as_posix()}/ and delete them: {names}"
        )
    steps.extend([
        f"upsert CHANGELOG.md section '## {plan.tag} - {plan.date}'",
        f"commit '{COMMIT_SUBJECT}' referencing {issue_id}",
        f"annotate tag {plan.tag} with '{plan.tag} ({plan.date})'",
    ])
    if dry_run:
        steps.append("(dry run: nothing was written)")
        return ReleaseResult(plan=plan, steps=tuple(steps), dry_run=True, tagged=False)

    try:
        _bump_version_file(repo_root, plan)
        _regenerate(repo_root)
        _rewrite_pins(repo_root, plan)
        _assemble_fragments(repo_root, plan)
        _write_changelog(repo_root, plan)
        _git(repo_root, ["add", "-A"])
        _git(repo_root, ["commit", "-m", commit_message(plan, issue_id)])
    except (RuntimeError, OSError, SystemExit) as exc:
        _restore(repo_root)
        raise SystemExit(f"release failed and the tree was restored: {exc}") from exc
    # Tag only after the commit exists: a tag on the wrong commit is the one part
    # of this that a `git checkout --` cannot quietly undo.
    _git(repo_root, ["tag", "-a", plan.tag, "-m", f"{plan.tag} ({plan.date})"])
    steps.append(
        f"NOT pushed: run `git push origin main && git push origin {plan.tag}` "
        "to publish — that half stays a human step"
    )
    return ReleaseResult(plan=plan, steps=tuple(steps), dry_run=False, tagged=True)

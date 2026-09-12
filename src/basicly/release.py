from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import checkout, merge, policy, verify, worktree
from . import commit as commit_mod
from .capability_proof import unexercised_capabilities

if TYPE_CHECKING:
    from .config import PolicyConfig

VERSION_FILE = Path("src") / "basicly" / "__init__.py"
VERSION_RE = re.compile(r'^__version__ = "(?P<version>\d+\.\d+\.\d+)"$', re.MULTILINE)

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

PIN_FILES = (
    Path("README.md"),
    Path("site") / "index.html",
    Path(".scripts") / "bootstrap.sh",
    Path(".scripts") / "bootstrap.ps1",
)
PIN_GLOBS = ("docs/how-to/*.md",)


RERECORDED_PATHS = ("docs/tutorial/",)
PIN_RE_TEMPLATE = r"(?<![\w.])v{version}(?!\w|\.\d)"

CHANGELOG_SCRIPT = Path(".scripts") / "generate_release_changelog.py"
CHANGELOG_FILE = Path("CHANGELOG.md")

RELEASE_NOTES_SCRIPT = Path(".scripts") / "check_release_notes.py"

CITATION = re.compile(r"\(([^()]*)\)")
_ID_SUFFIX = r"-[a-z0-9]+(?:\.[0-9]+)*\b"

FRAGMENT_DIR = Path("changelog.d")

FRAGMENT_DOC = "README.md"

BASE_REFS = ("origin/main", "main")

FRAGMENT_CATEGORIES = ("added", "changed", "deprecated", "removed", "fixed", "security")

UNRELEASED_HEADING = "## [Unreleased]"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

COMMIT_SUBJECT = "chore(release): bump version refresh install pins and regenerate projections"


@dataclass(frozen=True)
class PinSite:
    path: Path
    occurrences: int


@dataclass(frozen=True)
class ChangelogFragment:
    path: Path
    category: str


@dataclass(frozen=True)
class ReleasePlan:
    current_version: str
    version: str
    date: str
    pins: tuple[PinSite, ...]
    fragments: tuple[ChangelogFragment, ...] = ()

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def current_tag(self) -> str:
        return f"v{self.current_version}"


@dataclass(frozen=True)
class ReleaseResult:
    plan: ReleasePlan
    steps: tuple[str, ...]
    dry_run: bool
    tagged: bool
    refusals: tuple[str, ...] = ()

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


def commit_message(plan: ReleasePlan, issue_id: str) -> str:

    return (
        f"{COMMIT_SUBJECT}\n\n"
        f"Release {plan.tag} dated {plan.date}, up from {plan.current_tag}. The tag "
        f"is annotated locally and deliberately not pushed.\n\n"
        f"{issue_id}"
    )


def _git(repo_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:

    return worktree.run(["git", "-C", str(repo_root), *args], check=check)


def pin_paths(repo_root: Path) -> tuple[Path, ...]:

    found = dict.fromkeys(PIN_FILES)
    for pattern in PIN_GLOBS:
        for path in sorted(repo_root.glob(pattern)):
            found[path.relative_to(repo_root)] = None
    return tuple(found)


def read_version(repo_root: Path) -> str:
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


def id_pattern(known_ids: Iterable[str]) -> re.Pattern[str]:

    prefixes = sorted({found.split("-", 1)[0] for found in known_ids if "-" in found})
    alternation = "|".join(re.escape(prefix) for prefix in prefixes) or r"(?!)"
    return re.compile(rf"\b(?:{alternation}){_ID_SUFFIX}")


def cited_records(text: str, pattern: re.Pattern[str]) -> set[str]:
    return {found for group in CITATION.findall(text) for found in pattern.findall(group)}


def accounted_records(repo_root: Path, known_ids: Iterable[str]) -> set[str]:

    pattern = id_pattern(known_ids)
    fragments, _misnamed = scan_fragments(repo_root)
    bodies = [(repo_root / item.path).read_text(encoding="utf-8") for item in fragments]
    changelog = repo_root / CHANGELOG_FILE
    if changelog.exists():
        bodies.append(changelog.read_text(encoding="utf-8"))
    named = {item.path.stem.rpartition(".")[0] for item in fragments}
    return named | {found for body in bodies for found in cited_records(body, pattern)}


def fragments_on_base(repo_root: Path) -> dict[str, str]:

    found: dict[str, str] = {}
    directory = FRAGMENT_DIR.as_posix()
    for ref in BASE_REFS:
        names = checkout.names_in(ref, directory, cwd=repo_root)
        point = checkout.git(["merge-base", ref, "HEAD"], cwd=repo_root, check=False)
        if not names or point.returncode != 0:
            continue
        landed = set(checkout.names_in(point.stdout.strip(), directory, cwd=repo_root))
        found.update({
            Path(name).stem.rpartition(".")[0]: f"{directory}/{name}"
            for name in names
            if name not in landed
        })
    return found


def plan_release(repo_root: Path, version: str, *, date: str | None = None) -> ReleasePlan:

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
        date=date or datetime.now(UTC).date().isoformat(),
        pins=tuple(pins),
        fragments=fragments,
    )


def _unreleased_bounds(lines: list[str]) -> tuple[int, int] | None:
    for idx, line in enumerate(lines):
        if not line.startswith(UNRELEASED_HEADING):
            continue
        end = idx + 1
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        return idx, end
    return None


def _section_end(lines: list[str], heading: str) -> int | None:

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

    text = (repo_root / fragment.path).read_text(encoding="utf-8")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    record = fragment.path.stem.rpartition(".")[0]
    if lines and record not in cited_records(text, id_pattern([record])):
        if lines[-1].startswith("```"):
            lines.append(f"  ({record})")
        else:
            lines[-1] = f"{lines[-1]} ({record})"
    return lines


def _merge_unreleased(
    repo_root: Path, body: list[str], fragments: tuple[ChangelogFragment, ...]
) -> list[str]:

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


def _release_note_reasons(repo_root: Path) -> tuple[str, ...]:

    script = repo_root / RELEASE_NOTES_SCRIPT
    if not script.exists():
        return (f"release-note gate missing: {RELEASE_NOTES_SCRIPT.as_posix()}",)
    completed = worktree.run([sys.executable, str(script)], cwd=repo_root, check=False)
    if completed.returncode == 0:
        return ()
    printed = (completed.stderr or completed.stdout).strip().splitlines()
    return tuple(line.strip() for line in printed if line.strip()) or (
        f"{RELEASE_NOTES_SCRIPT.as_posix()} refused the cut and said nothing",
    )


def _summary_missing(lines: list[str]) -> str | None:
    bounds = _unreleased_bounds(lines)
    if bounds is None:
        return None
    for line in lines[bounds[0] + 1 : bounds[1]]:
        if line.startswith("### "):
            break
        if line.strip() and not line.startswith("Delta: "):
            return None
    return (
        f"{UNRELEASED_HEADING} carries no summary above its first `###` heading; write the "
        "release highlights there - the release page publishes that summary, not the section"
    )


def _is_rerecorded(porcelain_line: str) -> bool:
    path = porcelain_line[3:].rsplit(" -> ", maxsplit=1)[-1].strip().strip('"')
    return path.startswith(RERECORDED_PATHS)


def _changelog_lines(repo_root: Path) -> list[str]:
    path = repo_root / CHANGELOG_FILE
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def blocking_reasons(repo_root: Path, plan: ReleasePlan, *, issue_id: str) -> tuple[str, ...]:

    reasons: list[str] = []
    if worktree.is_linked_checkout(repo_root):
        reasons.append(
            f"refusing to release from a linked worktree ({worktree.current_branch(repo_root)}); "
            "tags are shared with the primary checkout, so run this from there on the base branch"
        )
    dirty = [
        line
        for line in _git(repo_root, ["status", "--porcelain"]).stdout.splitlines()
        if line.strip() and not _is_rerecorded(line)
    ]
    if dirty:
        extra = f" (and {len(dirty) - 1} more)" if len(dirty) > 1 else ""
        reasons.append(f"working tree is not clean: {dirty[0]}{extra}")
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
        reasons.append(
            f"unknown bead id {issue_id!r}: the committed ledger does not hold it — the "
            "tracker-commit-msg gate would reject the release commit"
        )
    reasons.extend(_fragment_reasons(repo_root))
    if (summary := _summary_missing(_changelog_lines(repo_root))) is not None:
        reasons.append(summary)
    reasons.extend(_release_note_reasons(repo_root))
    reasons.extend(unexercised_capabilities(repo_root))
    return tuple(reasons)


def autonomy_refusal(
    repo_root: Path,
    root_issue: str,
    config: PolicyConfig | None = None,
    *,
    shipping: str | None = None,
) -> str | None:

    grant = policy.active_grant(repo_root, root_issue)
    if grant is None:
        return f"no active autonomy grant on {root_issue}; a release needs L3"
    if grant.level != "L3":
        return f"grant on {root_issue} is {grant.level}; a release needs L3"
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
    _refresh_generated_docs(repo_root)


def _refresh_generated_docs(repo_root: Path) -> None:

    verify.apply_fixes(repo_root, "fast")


def _write_changelog(repo_root: Path, plan: ReleasePlan) -> None:

    worktree.run(
        [sys.executable, str(repo_root / CHANGELOG_SCRIPT), "--tag", plan.tag, "--date", plan.date],
        cwd=repo_root,
    )


def _restore(repo_root: Path) -> None:

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
    _git(repo_root, ["tag", "-a", plan.tag, "-m", f"{plan.tag} ({plan.date})"])
    steps.append(
        f"NOT pushed: run `git push origin main && git push origin {plan.tag}` "
        "to publish — that half stays a human step"
    )
    return ReleaseResult(plan=plan, steps=tuple(steps), dry_run=False, tagged=True)

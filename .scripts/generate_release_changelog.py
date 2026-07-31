"""Generate or update a dated changelog section for a semantic release tag."""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404
import sys
from datetime import date
from pathlib import Path

TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")

UNRELEASED_HEADING = "## [Unreleased]"

# The one marker that tells a generated section from a curated one, so a re-run
# cannot overwrite release notes a human wrote (basicly-m3od.1).
GENERATED_HEADING = "### Changes"

CHANGELOG_INTRO: str = (
    "# Changelog\n\nAll notable user-facing changes are documented in this file by release tag.\n"
)


def _run_git(*args: str) -> str:
    """Run a git command and return stdout or raise on failure."""
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )  # nosec
    if result.returncode != 0:
        message = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise RuntimeError(message)
    return result.stdout.strip()


def _nearest_previous_tag(tag_to_exclude: str) -> str | None:
    """Return the nearest reachable semantic tag, excluding the target release tag."""
    result = subprocess.run(
        [
            "git",
            "describe",
            "--tags",
            "--abbrev=0",
            "--match",
            "v*",
            "--exclude",
            tag_to_exclude,
        ],
        check=False,
        capture_output=True,
        text=True,
    )  # nosec
    if result.returncode != 0:
        return None
    tag = result.stdout.strip()
    return tag or None


def _collect_commit_subjects(previous_tag: str | None) -> list[str]:
    """Collect commit subjects since the previous tag (or all history for first release)."""
    revision = f"{previous_tag}..HEAD" if previous_tag else "HEAD"
    output = _run_git("log", "--no-merges", "--pretty=%s (%h)", revision)
    return [line.strip() for line in output.splitlines() if line.strip()]


def _build_section(
    tag: str,
    release_date: str,
    previous_tag: str | None,
    commits: list[str],
    curated: list[str] | None = None,
) -> list[str]:
    """Build a markdown changelog section for the target release tag.

    *curated* is the promoted ``[Unreleased]`` body. When it is present it **is**
    the section's content: the commit-subject list is only a traceability fallback
    for a release nobody wrote notes for.
    """
    delta_start = previous_tag or "initial"
    section = [
        f"## {tag} - {release_date}",
        "",
        f"Delta: {delta_start}..{tag}",
        "",
    ]
    if curated:
        section.extend(curated)
        section.append("")
        return section

    # markdownlint runs repo-wide on any staged .md, and MD022/MD032 require a
    # blank line after a heading and before a list. The manual flow never hit
    # this because a human curated the section into a second commit; an
    # automated release commits this text as generated (basicly-kjc5.12).
    section.extend([GENERATED_HEADING, ""])
    if commits:
        section.extend([f"- {commit}" for commit in commits])
    else:
        section.append("- No user-visible changes.")
    section.append("")
    return section


def _ensure_changelog_header(lines: list[str]) -> list[str]:
    """Ensure the changelog starts with a standard header and intro text."""
    if not lines:
        return CHANGELOG_INTRO.splitlines()

    if lines[0].strip() == "# Changelog":
        return lines

    return [*CHANGELOG_INTRO.splitlines(), "", *lines]


def _find_section_bounds(lines: list[str], tag: str) -> tuple[int | None, int | None]:
    """Find start and end line indices for a tag section."""
    start: int | None = None
    end: int | None = None
    for idx, line in enumerate(lines):
        if line.startswith(f"## {tag} - "):
            start = idx
            continue
        if start is not None and line.startswith("## "):
            end = idx
            break

    if start is not None and end is None:
        end = len(lines)

    return start, end


def _insert_index(lines: list[str]) -> int:
    """Return where a new dated release section should be inserted (newest first).

    Prefer the position just after an existing ``## [Unreleased]`` section so
    Unreleased stays pinned at the top and the new ``## vX.Y.Z`` lands directly
    below it. With no Unreleased section, fall back to the first ``## `` heading
    after the intro (above the newest existing release). Inserting at the *first*
    heading unconditionally was the bug: it dropped the new section above
    ``[Unreleased]`` (basicly-pui7).
    """
    for idx, line in enumerate(lines):
        if line.startswith(UNRELEASED_HEADING):
            nxt = idx + 1
            while nxt < len(lines) and not lines[nxt].startswith("## "):
                nxt += 1
            return nxt

    idx = 1
    while idx < len(lines) and lines[idx].strip() == "":
        idx += 1
    while idx < len(lines) and not lines[idx].startswith("## "):
        idx += 1
    return idx


def _collapse_blank_runs(lines: list[str]) -> list[str]:
    """Collapse runs of consecutive blank lines to a single blank (markdownlint MD012).

    A section built with a trailing blank inserted next to an existing blank
    line would otherwise leave two blanks at the seam, which MD012 rejects and
    every release had to hand-fix (basicly-pui7). Applied to the whole document:
    the changelog never carries intentional consecutive blanks.
    """
    out: list[str] = []
    for line in lines:
        if line.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(line)
    return out


def _upsert_section(existing_text: str, tag: str, section_lines: list[str]) -> str:
    """Insert or replace the tag section in the changelog text."""
    lines = _ensure_changelog_header(existing_text.splitlines())

    start, end = _find_section_bounds(lines, tag)
    if start is not None and end is not None:
        updated = lines[:start] + section_lines + lines[end:]
    else:
        insert_at = _insert_index(lines)
        updated = lines[:insert_at] + section_lines + lines[insert_at:]

    return "\n".join(_collapse_blank_runs(updated)).rstrip() + "\n"


def _take_unreleased_body(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split the ``[Unreleased]`` body out of *lines*, leaving the heading in place.

    Returns ``(body, remaining_lines)``. Promoting the body is what makes the
    release tag carry the notes a human wrote: the release commit and the tag are
    one step, and the release workflow reads ``CHANGELOG.md`` from the *tagged*
    commit, so curation left under ``[Unreleased]`` is never published
    (basicly-m3od.1). The heading stays so it keeps its pinned position at the top
    and the next cycle has somewhere to accumulate.
    """
    for idx, line in enumerate(lines):
        if not line.startswith(UNRELEASED_HEADING):
            continue
        end = idx + 1
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        body = list(lines[idx + 1 : end])
        while body and body[0].strip() == "":
            body.pop(0)
        while body and body[-1].strip() == "":
            body.pop()
        if not body:
            return [], lines
        return body, [*lines[: idx + 1], "", *lines[end:]]
    return [], lines


def _is_curated(section: list[str]) -> bool:
    """Report whether an existing dated section holds prose rather than the skeleton."""
    return not any(line.startswith(GENERATED_HEADING) for line in section)


def upsert_release_section(
    existing_text: str,
    tag: str,
    release_date: str,
    previous_tag: str | None,
    commits: list[str],
) -> str:
    """Return the changelog text with *tag*'s dated section present and curated.

    Three cases, in order of precedence:

    1. The dated section already exists and is curated — left untouched, so a
       re-run (a corrected ``--date``, a retried release) cannot replace release
       notes with a commit dump.
    2. ``[Unreleased]`` has a body — promoted into the dated section, Keep a
       Changelog's own release step, and ``[Unreleased]`` is emptied.
    3. Neither — the commit-delta skeleton is generated, as before.
    """
    lines = _ensure_changelog_header(existing_text.splitlines())

    start, end = _find_section_bounds(lines, tag)
    if start is not None and end is not None and _is_curated(lines[start:end]):
        return "\n".join(_collapse_blank_runs(lines)).rstrip() + "\n"

    curated, lines = _take_unreleased_body(lines)
    section_lines = _build_section(tag, release_date, previous_tag, commits, curated=curated)
    return _upsert_section("\n".join(lines), tag, section_lines)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Semantic release tag, e.g. v0.1.0")
    parser.add_argument("--date", required=True, help="Release date in ISO format, e.g. 2026-07-12")
    parser.add_argument(
        "--changelog",
        default="CHANGELOG.md",
        help="Path to changelog file (default: CHANGELOG.md)",
    )
    return parser.parse_args()


def main() -> int:
    """Generate or update the release section in CHANGELOG.md."""
    args = _parse_args()

    if not TAG_PATTERN.fullmatch(args.tag):
        print("ERROR: --tag must match semantic format vMAJOR.MINOR.PATCH", file=sys.stderr)
        return 2

    try:
        date.fromisoformat(args.date)
    except ValueError:
        print("ERROR: --date must be in ISO format YYYY-MM-DD", file=sys.stderr)
        return 2

    changelog_path = Path(args.changelog)
    existing_text = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else ""

    previous_tag = _nearest_previous_tag(args.tag)
    commits = _collect_commit_subjects(previous_tag)
    new_text = upsert_release_section(existing_text, args.tag, args.date, previous_tag, commits)

    changelog_path.write_text(new_text, encoding="utf-8")
    print(f"Updated {changelog_path} for {args.tag} ({args.date})")
    print(f"Commit count in release delta: {len(commits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

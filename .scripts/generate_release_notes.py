"""Render a release page from one tagged ``CHANGELOG.md`` section: summary, counts, breaking.

The changelog is the record; the release page is the summary of it. v0.10.0 published its
whole section as the page - 179,691 bytes, 2,326 lines [measured 2026-08-29, ``awk`` over
the section] - and the owner's verdict was that nobody reads a wall of text. v0.9.0's page,
rewritten by hand after publication, is 3,050 characters. This script produces that shape
from the tagged section alone, so the workflow publishes it without a hand edit
(basicly-xsdvp6).

What the page carries, and nothing else: the prose the release author wrote above the first
category heading, the entry count per category, a link to the section at the tag, every entry
whose bold lead starts with ``BREAKING``, and the pinned install block. An absent summary is
stated, never invented: ``basicly release`` refuses a cut without one, and this script runs
after the tag exists, where a refusal would leave a release with no page at all.

Stdlib-only, like every ``.scripts/`` gate: the workflow runs it on a bare ``python3``.

Run::

    python3 .scripts/generate_release_notes.py --tag v0.11.0 --repo-url https://github.com/o/r
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CHANGELOG = "CHANGELOG.md"

# The generator's own heading shape: `## v0.10.0 - 2026-08-28`. The date is part of the
# contract because the page repeats it and the workflow refused a heading without one.
_HEADING = re.compile(r"^## (?P<tag>v\d+\.\d+\.\d+) - (?P<date>\d{4}-\d{2}-\d{2})[ \t]*$")
# The categories `release.FRAGMENT_CATEGORIES` folds fragments under, capitalised as written.
CATEGORIES = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
_CATEGORY_HEADING = re.compile(r"^### (?P<name>" + "|".join(CATEGORIES) + r")[ \t]*$")
# The one machine line the generator writes above the body; it is not the author's summary.
_DELTA = "Delta: "
BREAKING_LEAD = "- **BREAKING"
NO_SUMMARY = "No summary was written for this release; the changelog section is the record."


def section(lines: list[str], tag: str) -> tuple[str, list[str]]:
    """The tag's heading date and body, or raise when the section is absent or undated."""
    for idx, line in enumerate(lines):
        if not line.startswith(f"## {tag}"):
            continue
        found = _HEADING.match(line)
        if found is None or found.group("tag") != tag:
            raise ValueError(
                f"release heading for {tag} must be `## {tag} - YYYY-MM-DD`, got {line!r}"
            )
        end = idx + 1
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        return found.group("date"), [entry.rstrip() for entry in lines[idx + 1 : end]]
    raise ValueError(f"no changelog section found for {tag} in {CHANGELOG}")


def summary(body: list[str]) -> list[str]:
    """The author's prose above the first category heading, without the generator's line."""
    kept: list[str] = []
    for line in body:
        if _CATEGORY_HEADING.match(line):
            break
        if not line.startswith(_DELTA):
            kept.append(line)
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return kept


def counts(body: list[str]) -> dict[str, int]:
    """Top-level bullets under each category heading, in category order, zeros omitted."""
    found: dict[str, int] = {}
    current: str | None = None
    for line in body:
        heading = _CATEGORY_HEADING.match(line)
        if heading:
            current = str(heading.group("name"))
            found.setdefault(current, 0)
        elif line.startswith("### "):
            current = None
        elif current and line.startswith("- "):
            found[current] += 1
    return {name: found[name] for name in CATEGORIES if found.get(name)}


def breaking(body: list[str]) -> list[list[str]]:
    """Every entry whose bold lead starts with ``BREAKING``, each with its continuation lines."""
    entries: list[list[str]] = []
    current: list[str] | None = None
    for line in body:
        if line.startswith(BREAKING_LEAD):
            current = [line]
            entries.append(current)
        elif current is not None and line.startswith("  ") and line.strip():
            current.append(line)
        else:
            current = None
    return entries


def _anchor(tag: str, date: str) -> str:
    """GitHub's heading anchor for `## vX.Y.Z - date`: lowercase, punctuation dropped."""
    text = f"{tag} - {date}".lower()
    return re.sub(r"[^a-z0-9 -]", "", text).replace(" ", "-")


def render(tag: str, date: str, body: list[str], repo_url: str) -> str:
    """The page, in the shape v0.9.0's hand-written page set."""
    prose = summary(body) or [NO_SUMMARY]
    tally = counts(body)
    total = sum(tally.values())
    parts = ", ".join(f"{n} {name.lower()}" for name, n in tally.items())
    entries = "entry" if total == 1 else "entries"
    link = f"{repo_url}/blob/{tag}/{CHANGELOG}#{_anchor(tag, date)}"
    out = [f"## {tag} - {date}", "", *prose, ""]
    tally_line = f"{total} {entries}" + (f" - {parts}" if parts else "")
    out.append(f"{tally_line}. Full detail in [{CHANGELOG}]({link}).")
    broken = breaking(body)
    if broken:
        out += ["", "### Breaking", ""]
        for idx, entry in enumerate(broken):
            if idx:
                out.append("")
            out.extend(entry)
    install = f"uvx --from git+{repo_url}@{tag} basicly install"
    out += ["", "## Install", "", "```sh", install, "```", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """Print the page for ``--tag`` to stdout; a missing or undated section is exit 1."""
    parser = argparse.ArgumentParser(
        description="Render a release page from one tagged changelog section."
    )
    parser.add_argument("--tag", required=True, help="the release tag, e.g. v0.11.0")
    parser.add_argument("--repo-url", required=True, help="https://github.com/<owner>/<repo>")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root")
    args = parser.parse_args(argv)
    path = args.root / CHANGELOG
    if not path.is_file():
        print(f"{CHANGELOG} not found under {args.root}; no release notes", file=sys.stderr)
        return 1
    try:
        date, body = section(path.read_text(encoding="utf-8").splitlines(), args.tag)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sys.stdout.write(render(args.tag, date, body, args.repo_url.rstrip("/")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

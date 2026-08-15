"""Fail when a document cites a `file.py:line` that no longer holds what it claims.

A requirements document is read as fact by every human and agent that plans from it, and
nothing checked it: `docs-claims` gates generated blocks and `corpus-drift` gates an epic's
problem statement, so a `file:line` written on one day and refuted by the next day's commit
kept asserting itself (basicly-miqr). Four such claims planned a P0 against a remedy the
tree had already replaced.

Two rules, both exact, because a natural-language claim is not checkable and a fuzzy gate
that cries wolf gets waived:

**A cited line must be live code.** Past end-of-file, or blank, is a citation that has
certainly drifted — no reading of the prose can make it right.

**A cited line must fall inside the symbol its own sentence names.** When the citing line
also names a top-level `def`, `class` or assignment of the cited module — in backticks, or
bare inside a fenced block — the two have to agree. That pins the citation to something
stable under editing, rather than to a line number that every insertion above it moves.

A citation whose sentence names no symbol of the cited file is **not** checked and not
counted as a pass; the summary reports the checkable share so the coverage is never
mistaken for the population.

**A ratchet, not a hard gate.** Eight citations were already stale when this landed, in
documents no one lane should rewrite, so the go-live debt is recorded per document in
``[tool.docs_citations.frozen]`` and may only fall. A document absent from that list may
not carry a single stale citation.

Run over every document, or over named ones::

    uv run python .scripts/check_docs_citations.py
    uv run python .scripts/check_docs_citations.py docs/requirements/factory-loop.md
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_LABEL = "docs-citations"
FROZEN_TABLE = "[tool.docs_citations.frozen]"
DOC_GLOB = "docs/**/*.md"
# Directories a cited basename must never resolve into: vendored or generated trees hold
# copies of `src/` modules, and a citation matching two files is reported, not guessed.
_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "site", "__pycache__"})

_CITATION = re.compile(r"(?<![\w/])([\w./-]+\.py):(\d+)")
_BACKTICKED = re.compile(r"`([^`]*)`")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_FENCE = "```"


class RatchetError(RuntimeError):
    """The recorded baseline is missing or malformed."""


@dataclass(frozen=True)
class Finding:
    """One citation that does not point at what the sentence around it claims."""

    doc: str
    doc_line: int
    citation: str
    detail: str


def load_frozen(repo: Path) -> dict[str, int]:
    """The recorded per-document debt from ``pyproject.toml``.

    Raises:
        RatchetError: The table is absent or malformed — an empty default would fail
            every recorded document at once and a permissive one would pass everything.
    """
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get("docs_citations")
    if not isinstance(table, dict) or not isinstance(table.get("frozen"), dict):
        raise RatchetError(f"no {FROZEN_TABLE} in pyproject.toml")
    frozen = table["frozen"]
    if not all(isinstance(value, int) for value in frozen.values()):
        raise RatchetError(f"{FROZEN_TABLE} must map each document path to its go-live count")
    return frozen


def top_level_spans(source: str) -> dict[str, list[tuple[int, int]]]:
    """Line span of every module-level `def`, `class` and assignment, by name.

    Module level only: a local named `reason` or `text` matches half the prose in this
    repo, and admitting one turns the symbol rule from exact into a coin toss.
    """
    spans: dict[str, list[tuple[int, int]]] = {}

    def record(name: str, node: ast.stmt) -> None:
        spans.setdefault(name, []).append((node.lineno, node.end_lineno or node.lineno))

    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            record(node.name, node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    record(target.id, node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            record(node.target.id, node)
    return spans


def resolve(repo_root: Path, cited: str) -> Path | None:
    """The file *cited* names, from a repo-relative path or an unambiguous basename."""
    direct = repo_root / cited
    if direct.is_file():
        return direct
    matches = [
        path
        for path in repo_root.rglob(Path(cited).name)
        if not _SKIP_DIRS & set(path.relative_to(repo_root).parts)
    ]
    return matches[0] if len(matches) == 1 else None


def named_symbols(line: str, spans: dict[str, list[tuple[int, int]]], stem: str) -> set[str]:
    """Top-level symbols of the cited module that *line* names, in ticks or in a fence.

    The module's own stem is excluded: `classify.py:43` beside the word `classify` names
    the file a second time, not a function inside it.
    """
    chunks = [line] if line.startswith(_FENCE) else _BACKTICKED.findall(line)
    return {
        segment
        for chunk in chunks
        for dotted in _DOTTED.findall(chunk)
        for segment in [dotted.split(".")[-1]]
        if segment != stem and segment in spans
    }


def _checked(repo_root: Path, doc: str, doc_line: int, line: str) -> tuple[int, list[Finding]]:
    """Every citation on one line of prose, as (checkable count, findings)."""
    checkable = 0
    found: list[Finding] = []
    for cited, number in _CITATION.findall(line):
        target = resolve(repo_root, cited)
        citation = f"{cited}:{number}"
        if target is None:
            found.append(Finding(doc, doc_line, citation, "no such file, or two files match"))
            continue
        source = target.read_text(encoding="utf-8")
        lines = source.splitlines()
        at = int(number)
        if at > len(lines) or not lines[at - 1].strip():
            found.append(Finding(doc, doc_line, citation, "past end-of-file or a blank line"))
            continue
        spans = top_level_spans(source)
        wanted = named_symbols(line, spans, target.stem)
        if not wanted:
            continue
        checkable += 1
        if any(start <= at <= end for name in wanted for start, end in spans[name]):
            continue
        moved = ", ".join(f"`{name}` is at :{spans[name][0][0]}" for name in sorted(wanted))
        found.append(Finding(doc, doc_line, citation, f"outside the symbol named here — {moved}"))
    return checkable, found


def scan(repo_root: Path, docs: tuple[Path, ...]) -> tuple[int, int, tuple[Finding, ...]]:
    """Scan *docs*, returning (citations seen, checkable against a symbol, findings)."""
    seen = 0
    checkable = 0
    found: list[Finding] = []
    for doc in docs:
        relative = doc.relative_to(repo_root).as_posix()
        in_fence = False
        for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith(_FENCE):
                in_fence = not in_fence
                continue
            seen += len(_CITATION.findall(line))
            # A fenced line has no backticks to mark a symbol, so the whole line is the
            # chunk; `_FENCE` prefixed onto it is how `named_symbols` is told which.
            probe = f"{_FENCE}{line}" if in_fence else line
            one, hits = _checked(repo_root, relative, number, probe)
            checkable += one
            found.extend(hits)
    return seen, checkable, tuple(found)


def verdicts(found: tuple[Finding, ...], frozen: dict[str, int]) -> list[str]:
    """The ratchet's reading of *found*: what grew, what appeared, and what graduated."""
    counts = dict.fromkeys(frozen, 0)
    for finding in found:
        counts[finding.doc] = counts.get(finding.doc, 0) + 1
    lines: list[str] = []
    for doc, count in sorted(counts.items()):
        baseline = frozen.get(doc)
        if baseline is None:
            lines.append(f"{doc}: {count} stale citation(s); this document has no recorded debt")
        elif count > baseline:
            lines.append(
                f"{doc}: {count} stale citation(s), up from the frozen {baseline} — "
                f"{FROZEN_TABLE} may only fall"
            )
        elif count < baseline:
            lines.append(
                f'{doc}: {count} stale citation(s), down from {baseline}; bank it: set "{doc}" '
                f"= {count} in {FROZEN_TABLE}, or delete the entry at zero"
            )
    return lines


def report(found: tuple[Finding, ...], failing: list[str]) -> str:
    """The failing documents, each with the citations behind its count."""
    named = {line.split(":", 1)[0] for line in failing}
    lines: list[str] = []
    for entry in failing:
        lines.append(entry)
        lines += [
            f"  - line {finding.doc_line}: {finding.citation} — {finding.detail}"
            for finding in found
            if finding.doc == entry.split(":", 1)[0]
        ]
    orphans = [finding for finding in found if finding.doc not in named]
    if orphans:
        lines.append(f"({len(orphans)} stale citation(s) within a recorded baseline)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point: report every citation a reader would take as a fact about the code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("doc", nargs="*", help="Only check these documents")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Ignore the recorded baseline: fail on every stale citation, go-live debt "
        "included. What an author runs to see what a reader is still being told.",
    )
    args = parser.parse_args(argv)
    docs = (
        tuple(Path(name).resolve() for name in args.doc)
        if args.doc
        else tuple(sorted(REPO_ROOT.glob(DOC_GLOB)))
    )
    try:
        frozen = {} if args.strict else load_frozen(REPO_ROOT)
    except RatchetError as exc:
        print(f"[{_LABEL}] {exc}", file=sys.stderr)
        return 2
    if args.doc:
        wanted = {doc.relative_to(REPO_ROOT).as_posix() for doc in docs}
        frozen = {doc: count for doc, count in frozen.items() if doc in wanted}
    seen, checkable, found = scan(REPO_ROOT, docs)
    failing = verdicts(found, frozen)
    summary = (
        f"{seen} citation(s) in {len(docs)} document(s), {checkable} checkable against a "
        f"named symbol, {len(found)} stale"
    )
    if not failing:
        print(f"[{_LABEL}] {summary}; no document is above its {FROZEN_TABLE} baseline")
        return 0
    print(f"[{_LABEL}] {summary}; {len(failing)} document(s) off their recorded debt")
    print(report(found, failing))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

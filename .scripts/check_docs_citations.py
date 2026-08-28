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

**A citation nothing can check is a finding, not a pass** (basicly-v5c8ob). When the citing
sentence names no symbol of the cited file, the rule above cannot run — and reporting the
checkable share while exiting zero is the fail-open shape this gate exists to refuse. Probed
on real input, a sentence citing a real module at line 1 with a false claim about what is
there was counted, unchecked, exit 0; 32 of 44 citations were in that state. Each is now
reported and ratcheted, so the 32 are debt and a new one is refused.

**Two ratchets, not a hard gate.** Debt is recorded per document and may only fall: stale
citations in ``[tool.docs_citations.frozen]``, unverifiable ones in
``[tool.docs_citations.unverifiable]``. A document absent from a list may not carry a single
citation of that kind. The repair for an unverifiable citation is to name, in the citing
sentence, a top-level symbol of the module it cites — which is what makes the claim checkable
at all, and what the second rule then holds it to.

Run over every document, or over named ones::

    uv run python .scripts/check_docs_citations.py
    uv run python .scripts/check_docs_citations.py docs/requirements/harness-board.md
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
UNVERIFIABLE_TABLE = "[tool.docs_citations.unverifiable]"
DOC_GLOB = "docs/**/*.md"
# Directories a cited basename must never resolve into: vendored or generated trees hold
# copies of `src/` modules, and a citation matching two files is reported, not guessed.
_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "site", "__pycache__"})

# The `?` is the closing backtick of a backticked path, which a citation writes on the
# outside of the tick when it writes the line number outside too (basicly-v5c8ob). Without
# it, `` `loop.py`:120 `` matched nothing at all — not counted, not checked, not reported,
# which is the one outcome a presence-based gate cannot tell from a document with no
# citations. Loosening it is safe because the path and the line number are both still
# required: measured over all 304 tracked `.md`/`.yaml` files, the old pattern and this one
# both find 53 citations, so the tick admits no prose.
_CITATION = re.compile(r"(?<![\w/])([\w./-]+\.py)`?:(\d+)")
# What a citation nothing can check is reported as. Before basicly-v5c8ob this branch was a
# bare `continue`: 32 of the 44 citations in `docs/` were counted, not verified, and the gate
# exited zero over a sentence citing a real module at line 1 with a false claim about it.
_UNVERIFIABLE = "names no symbol of the cited module, so nothing verifies the claim"
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


def load_frozen(repo: Path, key: str = "frozen") -> dict[str, int]:
    """The recorded per-document debt named by *key* in ``pyproject.toml``.

    Two tables, one shape: ``frozen`` records citations that are wrong and
    ``unverifiable`` records citations nothing checks.

    Raises:
        RatchetError: The table is absent or malformed — an empty default would fail
            every recorded document at once and a permissive one would pass everything.
    """
    named = f"[tool.docs_citations.{key}]"
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get("docs_citations")
    if not isinstance(table, dict) or not isinstance(table.get(key), dict):
        raise RatchetError(f"no {named} in pyproject.toml")
    frozen = table[key]
    if not all(isinstance(value, int) for value in frozen.values()):
        raise RatchetError(f"{named} must map each document path to its go-live count")
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


def _checked(
    repo_root: Path, doc: str, doc_line: int, line: str
) -> tuple[int, list[Finding], list[Finding]]:
    """Every citation on one line of prose, as (checkable count, stale, unverifiable)."""
    checkable = 0
    found: list[Finding] = []
    unchecked: list[Finding] = []
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
            unchecked.append(Finding(doc, doc_line, citation, _UNVERIFIABLE))
            continue
        checkable += 1
        if any(start <= at <= end for name in wanted for start, end in spans[name]):
            continue
        moved = ", ".join(f"`{name}` is at :{spans[name][0][0]}" for name in sorted(wanted))
        found.append(Finding(doc, doc_line, citation, f"outside the symbol named here — {moved}"))
    return checkable, found, unchecked


def scan(
    repo_root: Path, docs: tuple[Path, ...]
) -> tuple[int, int, tuple[Finding, ...], tuple[Finding, ...]]:
    """Scan *docs* as (seen, checkable, stale, unverifiable)."""
    seen = 0
    checkable = 0
    found: list[Finding] = []
    unchecked: list[Finding] = []
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
            one, hits, blind = _checked(repo_root, relative, number, probe)
            checkable += one
            found.extend(hits)
            unchecked.extend(blind)
    return seen, checkable, tuple(found), tuple(unchecked)


def verdicts(
    found: tuple[Finding, ...],
    frozen: dict[str, int],
    noun: str = "stale",
    table: str = FROZEN_TABLE,
) -> list[str]:
    """The ratchet's reading of *found*: what grew, what appeared, and what graduated.

    One reading for both populations. *noun* and *table* say which is being ratcheted,
    because a stale citation and an unverifiable one need the same three verdicts and
    differ only in the repair a reader is being sent to make.
    """
    counts = dict.fromkeys(frozen, 0)
    for finding in found:
        counts[finding.doc] = counts.get(finding.doc, 0) + 1
    lines: list[str] = []
    for doc, count in sorted(counts.items()):
        baseline = frozen.get(doc)
        if baseline is None:
            lines.append(f"{doc}: {count} {noun} citation(s); this document has no recorded debt")
        elif count > baseline:
            lines.append(
                f"{doc}: {count} {noun} citation(s), up from the frozen {baseline} — "
                f"{table} may only fall"
            )
        elif count < baseline:
            lines.append(
                f'{doc}: {count} {noun} citation(s), down from {baseline}; bank it: set "{doc}" '
                f"= {count} in {table}, or delete the entry at zero"
            )
    return lines


def report(found: tuple[Finding, ...], failing: list[str], noun: str = "stale") -> str:
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
        lines.append(f"({len(orphans)} {noun} citation(s) within a recorded baseline)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point: report every citation a reader would take as a fact about the code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("doc", nargs="*", help="Only check these documents")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Ignore both recorded baselines: fail on every stale and every unverifiable "
        "citation, go-live debt included. What an author runs to see what a reader is "
        "still being told.",
    )
    args = parser.parse_args(argv)
    docs = (
        tuple(Path(name).resolve() for name in args.doc)
        if args.doc
        else tuple(sorted(REPO_ROOT.glob(DOC_GLOB)))
    )
    try:
        frozen = {} if args.strict else load_frozen(REPO_ROOT)
        blind_frozen = {} if args.strict else load_frozen(REPO_ROOT, "unverifiable")
    except RatchetError as exc:
        print(f"[{_LABEL}] {exc}", file=sys.stderr)
        return 2
    if args.doc:
        wanted = {doc.relative_to(REPO_ROOT).as_posix() for doc in docs}
        frozen = {doc: count for doc, count in frozen.items() if doc in wanted}
        blind_frozen = {doc: count for doc, count in blind_frozen.items() if doc in wanted}
    seen, checkable, found, unchecked = scan(REPO_ROOT, docs)
    failing = verdicts(found, frozen)
    blind = verdicts(unchecked, blind_frozen, "unverifiable", UNVERIFIABLE_TABLE)
    summary = (
        f"{seen} citation(s) in {len(docs)} document(s), {checkable} checkable against a "
        f"named symbol, {len(found)} stale, {len(unchecked)} unverifiable"
    )
    if not failing and not blind:
        print(f"[{_LABEL}] {summary}; no document is above either baseline")
        return 0
    off = len({line.split(":", 1)[0] for line in (*failing, *blind)})
    print(f"[{_LABEL}] {summary}; {off} document(s) off their recorded debt")
    for stream, entries, noun in ((found, failing, "stale"), (unchecked, blind, "unverifiable")):
        if entries:
            print(report(stream, entries, noun))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

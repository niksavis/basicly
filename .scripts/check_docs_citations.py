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
_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "site", "__pycache__"})

_CITATION = re.compile(r"(?<![\w/])([\w./-]+\.py)`?:(\d+)")
_UNVERIFIABLE = "names no symbol of the cited module, so nothing verifies the claim"
_BACKTICKED = re.compile(r"`([^`]*)`")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_FENCE = "```"


class RatchetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Finding:
    doc: str
    doc_line: int
    citation: str
    detail: str


def load_frozen(repo: Path, key: str = "frozen") -> dict[str, int]:

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
    parser = argparse.ArgumentParser(
        description="Fail when a document cites a `file.py:line` that no longer holds it."
    )
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

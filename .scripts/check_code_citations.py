from __future__ import annotations

import re
import sys
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from ratchet import (  # noqa: E402 - the path above comes first
    Finding,
    Ratchet,
    RatchetError,
    compose_ratchet,
    count_delta_remedy,
    frozen_table,
    rebaseline_clause,
    report,
    stale,
    tracked_sources,
)

_GATE = "code_citations"
FROZEN_TABLE = frozen_table(_GATE)
BINDINGS_TABLE = f"[tool.{_GATE}.bindings]"
_LABEL = "code-citations"

_MARK = "\N{SECTION SIGN}"

_CITATION = re.compile(rf"{_MARK}\s*(\d+(?:\.\d+)*)")
_DOCUMENT = re.compile(r"[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.md")
_HEADING = re.compile(r"^#{1,6}\s+(\d+(?:\.\d+)*)\.?\s")

_DOC_ROOT = "docs"
_MAX_SITES = 4


@dataclass(frozen=True)
class Citation:
    path: str
    line: int
    section: str
    document: str | None

    @property
    def site(self) -> str:
        return f"{self.path}:{self.line}"


def load_ratchet(repo: Path) -> Ratchet[int]:
    return compose_ratchet(repo, _GATE, count_key="binding_count", entry_type=int)


def load_bindings(repo: Path) -> dict[str, str]:

    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get(_GATE, {}).get("bindings")
    if not isinstance(table, dict) or not all(isinstance(value, str) for value in table.values()):
        raise RatchetError(f"{BINDINGS_TABLE} must map each path prefix to one document")
    return table


def bound_document(path: str, bindings: Mapping[str, str]) -> str | None:

    matches = [
        (prefix, document) for prefix, document in bindings.items() if path.startswith(prefix)
    ]
    return max(matches, key=lambda match: len(match[0]))[1] if matches else None


def headings(repo: Path, document: str) -> frozenset[str] | None:
    path = repo / document
    if not path.is_file():
        return None
    return frozenset(
        match.group(1)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if (match := _HEADING.match(line))
    )


def resolve_document(repo: Path, named: str) -> str | None:

    if (repo / named).is_file():
        return named
    matches = [path for path in (repo / _DOC_ROOT).rglob(Path(named).name) if path.is_file()]
    if len(matches) != 1:
        return None
    return matches[0].relative_to(repo).as_posix()


def cited(path: str, text: str, bindings: Mapping[str, str]) -> list[Citation]:

    fallback = bound_document(path, bindings)
    found: list[Citation] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in _CITATION.finditer(line):
            named = _DOCUMENT.findall(line[: match.start()])
            document = named[-1] if named else fallback
            found.append(Citation(path, number, match.group(1), document))
    return found


def scan(repo: Path, bindings: Mapping[str, str]) -> list[Citation]:

    return [
        citation for path, text in tracked_sources(repo) for citation in cited(path, text, bindings)
    ]


def unresolved(repo: Path, citations: Iterable[Citation]) -> dict[Citation, str]:

    known: dict[str, frozenset[str] | None] = {}
    reasons: dict[Citation, str] = {}
    for citation in citations:
        if citation.document is None:
            reasons[citation] = f"{_MARK}{citation.section} names no document"
            continue
        if citation.document not in known:
            resolved = resolve_document(repo, citation.document)
            known[citation.document] = None if resolved is None else headings(repo, resolved)
        defined = known[citation.document]
        if defined is None:
            reasons[citation] = f"`{citation.document}` is not a document in this tree"
        elif citation.section not in defined:
            reasons[citation] = f"`{citation.document}` defines no {_MARK}{citation.section}"
    return reasons


def _sites(module: str, reasons: Mapping[Citation, str]) -> str:
    listed = sorted(
        (citation.line, reason) for citation, reason in reasons.items() if citation.path == module
    )
    shown = ", ".join(f"line {line}: {reason}" for line, reason in listed[:_MAX_SITES])
    return f"{shown}, and {len(listed) - _MAX_SITES} more" if len(listed) > _MAX_SITES else shown


_REPAIR = (
    "name the document on the citing line, or correct the number to a heading that document "
    f"defines. A mark that names no document may instead be bound by prefix in "
    f"{BINDINGS_TABLE}, and {count_delta_remedy(_GATE, 1)}. Never delete the reference to "
    "pass: an unresolved mark is a pointer whose target moved, and finding the target is the "
    "repair"
)


def _unlisted(module: str, count: int, detail: str) -> Finding:
    return Finding(
        subject=module,
        detail=f"{count} unresolved citation(s); this module has no recorded debt ({detail})",
        remedy=_REPAIR,
    )


def _rose(module: str, count: int, baseline: int, detail: str) -> Finding:
    return Finding(
        subject=module,
        detail=(
            f"{count} unresolved citation(s), up from the frozen {baseline}; a recorded "
            f"count may only fall ({detail})"
        ),
        remedy=_REPAIR,
    )


def _fell(module: str, count: int, baseline: int) -> Finding:
    banked = f'set `"{module}" = {count}` in {FROZEN_TABLE}'
    return Finding(
        subject=module,
        detail=f"{count} unresolved citation(s), down from the frozen {baseline}",
        remedy=banked if count else f'delete `"{module}"` from {FROZEN_TABLE}',
    )


def _binding_findings(repo: Path, bindings: Mapping[str, str], present: set[str]) -> list[Finding]:

    findings: list[Finding] = []
    for prefix, document in sorted(bindings.items()):
        if resolve_document(repo, document) is None:
            findings.append(
                Finding(
                    subject=prefix,
                    detail=f"bound to `{document}`, which is not a document in this tree",
                    remedy=f"point the entry in {BINDINGS_TABLE} at a document that exists",
                )
            )
        elif not any(path.startswith(prefix) for path in present):
            findings.append(
                stale(f"{_GATE}.bindings", prefix, "no tracked module in scope has this prefix")
            )
    return findings


def _count_finding(bindings: Mapping[str, str], recorded: int) -> list[Finding]:
    if len(bindings) == recorded:
        return []
    grew = len(bindings) > recorded
    return [
        Finding(
            subject="pyproject.toml",
            detail=(
                f"{len(bindings)} binding(s) declared but binding_count is {recorded} — one "
                f"was {'added' if grew else 'removed'} without saying so"
            ),
            remedy=count_delta_remedy(_GATE, len(bindings) - recorded),
        )
    ]


def collect(
    repo: Path,
    citations: Iterable[Citation],
    reasons: Mapping[Citation, str],
    bindings: Mapping[str, str],
    ratchet: Ratchet[int],
) -> list[Finding]:

    present = {citation.path for citation in citations}
    counts = Counter(citation.path for citation in reasons)
    findings: list[Finding] = []
    for module in sorted(set(counts) | set(ratchet.frozen)):
        count = counts.get(module, 0)
        baseline = ratchet.frozen.get(module)
        if baseline is None:
            findings.append(_unlisted(module, count, _sites(module, reasons)))
        elif count > baseline:
            findings.append(_rose(module, count, baseline, _sites(module, reasons)))
        elif count < baseline:
            findings.append(_fell(module, count, baseline))
    findings.extend(_binding_findings(repo, bindings, present))
    findings.extend(_count_finding(bindings, ratchet.count))
    return sorted(findings, key=lambda finding: (finding.subject, finding.detail))


def summary(
    citations: list[Citation], reasons: Mapping[Citation, str], ratchet: Ratchet[int]
) -> str:
    modules = len({citation.path for citation in citations})
    resolved = len(citations) - len(reasons)
    return (
        f"{len(citations)} citation(s) in {modules} module(s), {resolved} resolved to a "
        f"heading, {len(reasons)} unresolved ({len(ratchet.frozen)} module(s) frozen, "
        f"{ratchet.count} binding(s){rebaseline_clause(ratchet)})"
    )


def main() -> int:
    try:
        ratchet = load_ratchet(REPO_ROOT)
        bindings = load_bindings(REPO_ROOT)
        citations = scan(REPO_ROOT, bindings)
    except RatchetError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    reasons = unresolved(REPO_ROOT, citations)
    findings = collect(REPO_ROOT, citations, reasons, bindings, ratchet)
    if findings:
        report(_LABEL, findings)
        return 1
    print(f"{_LABEL}: {summary(citations, reasons, ratchet)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

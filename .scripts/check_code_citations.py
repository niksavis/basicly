"""Fail when code cites a document section that the document does not define.

`docs-citations` runs one direction: a `file.py:line` written in a document. Nothing ran the
other way (basicly-e2mz.49). A section mark written in a comment or a docstring, pointing at
a requirements document, was checked by nothing at all — so it went stale in silence, and a
mark that resolves to the *wrong* section is worse than none, because it reads as correct.

Measured 2026-08-20 over tracked Python in :data:`~ratchet.SCOPE_ROOTS`: **370 marks in 100
modules**. 41 name their document on the citing line, 113 more are bound by prefix, and
**213 name no document at all** — a bare mark a reader cannot attribute and a gate cannot
either. Live findings on that population: `tests/test_policy.py` cites
`gates-and-rework-design.md`, absorbed and deleted 2026-08-08, and four marks in the shipped
tracker kit read as the kit's own source document while meaning the architecture.

**A citable target is a document and a number, both nameable.** The document is a `.md` path
on the citing line, or a prefix binding in ``[tool.code_citations.bindings]``; the number has
to match a numbered heading — ``## 4. Title``, ``### 4.6 Title`` — that the document defines
today. The architecture's own section 3 makes those numbers a cited surface, which is what
a citation is entitled to rely on. A mark missing either half is **unresolved**, and unresolved is a
finding rather than a silent pass: that is the whole difference from the gate this one joins,
which counts 32 citations it cannot verify and exits zero.

**A binding is how 113 marks became checkable**, and it is ratcheted in both directions
against ``binding_count``. A prefix binding is one reviewable line that turns a directory's
bare marks into checked ones — and, added quietly, it is also the one edit that could make a
whole directory's marks resolve against a document nobody chose. So it may only appear in a
diff that says it appeared.

**A ratchet, not a ban.** 220 marks were already unresolved when this landed, across modules
no one lane should rewrite, so each module's go-live count is recorded in
``[tool.code_citations.frozen]`` and may only fall. A module the table does not name may
carry none.

This module spells the section sign as :data:`_MARK` and never as a literal, so it cannot
cite anything and cannot count itself.

Run::

    uv run python .scripts/check_code_citations.py
"""

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

# The section sign, held as data rather than typed into a pattern. Every regex below is built
# from it, so no line of this file carries the character followed by a number.
_MARK = "\N{SECTION SIGN}"

_CITATION = re.compile(rf"{_MARK}\s*(\d+(?:\.\d+)*)")
# A document reference, as this tree writes one: backticked, in a docstring, or the target of
# a markdown link. Bare basenames are admitted and resolved against `docs/`.
_DOCUMENT = re.compile(r"[A-Za-z0-9_./-]*[A-Za-z0-9_-]\.md")
# A numbered heading at any depth. The trailing dot is optional because this tree writes
# `## 4. Title` at the top level and `### 4.6 Title` below it.
_HEADING = re.compile(r"^#{1,6}\s+(\d+(?:\.\d+)*)\.?\s")

_DOC_ROOT = "docs"
# How many sites one finding spells out before it stops. A module with 10 unresolved marks
# needs the count and a way in, not ten lines of the same repair.
_MAX_SITES = 4


@dataclass(frozen=True)
class Citation:
    """One section mark, and the document it was attributed to if it was attributed."""

    path: str
    line: int
    section: str
    document: str | None

    @property
    def site(self) -> str:
        """Where a reader has to go to act on it."""
        return f"{self.path}:{self.line}"


def load_ratchet(repo: Path) -> Ratchet[int]:
    """This gate's baseline: unresolved marks per module, and the count of bindings."""
    return compose_ratchet(repo, _GATE, count_key="binding_count", entry_type=int)


def load_bindings(repo: Path) -> dict[str, str]:
    """The declared path-prefix to document map, longest prefix winning at use.

    Raises:
        RatchetError: The table is absent or malformed. Defaulting to an empty map would
            un-attribute every bound mark at once and report a whole directory as debt.
    """
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RatchetError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get(_GATE, {}).get("bindings")
    if not isinstance(table, dict) or not all(isinstance(value, str) for value in table.values()):
        raise RatchetError(f"{BINDINGS_TABLE} must map each path prefix to one document")
    return table


def bound_document(path: str, bindings: Mapping[str, str]) -> str | None:
    """The document *bindings* attributes *path*'s bare marks to, longest prefix winning.

    Longest *prefix*, not longest document name: a nested binding is how one directory inside
    a bound tree points somewhere else, and ordering on the value would decide it by the
    length of a filename.
    """
    matches = [
        (prefix, document) for prefix, document in bindings.items() if path.startswith(prefix)
    ]
    return max(matches, key=lambda match: len(match[0]))[1] if matches else None


def headings(repo: Path, document: str) -> frozenset[str] | None:
    """Every numbered heading *document* defines, or ``None`` if there is no such document."""
    path = repo / document
    if not path.is_file():
        return None
    return frozenset(
        match.group(1)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if (match := _HEADING.match(line))
    )


def resolve_document(repo: Path, named: str) -> str | None:
    """*named* as a repo-relative document path, or ``None`` when it names no single one.

    A repo-relative path is taken as written. Anything else is matched by basename under
    ``docs/``, and two matches resolve to nothing rather than to a guess — the same rule
    `check_docs_citations.resolve` applies in the other direction.
    """
    if (repo / named).is_file():
        return named
    matches = [path for path in (repo / _DOC_ROOT).rglob(Path(named).name) if path.is_file()]
    if len(matches) != 1:
        return None
    return matches[0].relative_to(repo).as_posix()


def cited(path: str, text: str, bindings: Mapping[str, str]) -> list[Citation]:
    """Every section mark in *text*, attributed to a document where one can be.

    A document named anywhere earlier on the same line binds every mark on it, which is what
    makes two marks written after one path two citations of that document rather than one.
    With no name on the line the prefix binding applies; with neither, none does.
    """
    fallback = bound_document(path, bindings)
    found: list[Citation] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in _CITATION.finditer(line):
            named = _DOCUMENT.findall(line[: match.start()])
            document = named[-1] if named else fallback
            found.append(Citation(path, number, match.group(1), document))
    return found


def scan(repo: Path, bindings: Mapping[str, str]) -> list[Citation]:
    """Every section mark in every tracked module in scope.

    Raises:
        RatchetError: git refused to list the tree.
    """
    return [
        citation for path, text in tracked_sources(repo) for citation in cited(path, text, bindings)
    ]


def unresolved(repo: Path, citations: Iterable[Citation]) -> dict[Citation, str]:
    """Each citation that does not reach a heading, against why it does not.

    Three ways, and the difference is the repair: no document was attributed, the attributed
    document is not in the tree, or the number names no heading it defines.
    """
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
    """The first few unresolved sites in *module*, as one clause."""
    listed = sorted(
        (citation.line, reason) for citation, reason in reasons.items() if citation.path == module
    )
    shown = ", ".join(f"line {line}: {reason}" for line, reason in listed[:_MAX_SITES])
    return f"{shown}, and {len(listed) - _MAX_SITES} more" if len(listed) > _MAX_SITES else shown


# One repair line for all three reasons, and the order matters: a mark that already names a
# document is repaired by correcting the number, never by binding the directory around it.
_REPAIR = (
    "name the document on the citing line, or correct the number to a heading that document "
    f"defines. A mark that names no document may instead be bound by prefix in "
    f"{BINDINGS_TABLE}, and {count_delta_remedy(_GATE, 1)}. Never delete the reference to "
    "pass: an unresolved mark is a pointer whose target moved, and finding the target is the "
    "repair"
)


def _unlisted(module: str, count: int, detail: str) -> Finding:
    """A module the closed frozen list does not name carries an unresolved mark."""
    return Finding(
        subject=module,
        detail=f"{count} unresolved citation(s); this module has no recorded debt ({detail})",
        remedy=_REPAIR,
    )


def _rose(module: str, count: int, baseline: int, detail: str) -> Finding:
    """A frozen module gained one."""
    return Finding(
        subject=module,
        detail=(
            f"{count} unresolved citation(s), up from the frozen {baseline}; a recorded "
            f"count may only fall ({detail})"
        ),
        remedy=_REPAIR,
    )


def _fell(module: str, count: int, baseline: int) -> Finding:
    """A frozen module lost one and the table still licenses the higher number."""
    banked = f'set `"{module}" = {count}` in {FROZEN_TABLE}'
    return Finding(
        subject=module,
        detail=f"{count} unresolved citation(s), down from the frozen {baseline}",
        remedy=banked if count else f'delete `"{module}"` from {FROZEN_TABLE}',
    )


def _binding_findings(repo: Path, bindings: Mapping[str, str], present: set[str]) -> list[Finding]:
    """A binding that names no document, or that prefixes no module in scope.

    Visited from the declaration rather than from the tree, because a binding whose prefix
    stopped matching is the shape that reads as satisfied: nothing is attributed to it, so
    nothing fails, and the marks it used to check quietly become debt somewhere else.
    """
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
    """The binding ratchet, which moves only in a diff that says it moved."""
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
    """Every disagreement between the tree and the recorded ratchet.

    The union of measured and recorded modules is walked, not the measured ones: a frozen
    entry whose module no longer carries an unresolved mark has to be reported, or the record
    keeps licensing the count it used to have.
    """
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
    """What the population is, so the resolved share is never read as the whole of it."""
    modules = len({citation.path for citation in citations})
    resolved = len(citations) - len(reasons)
    return (
        f"{len(citations)} citation(s) in {modules} module(s), {resolved} resolved to a "
        f"heading, {len(reasons)} unresolved ({len(ratchet.frozen)} module(s) frozen, "
        f"{ratchet.count} binding(s){rebaseline_clause(ratchet)})"
    )


def main() -> int:
    """Entry point: report every module citing a section its document does not define."""
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

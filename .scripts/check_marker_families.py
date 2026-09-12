from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

LABEL = "marker-families"
SELF = f"{SCRIPT_DIR.name}/{Path(__file__).name}"

SRC_ROOT = "src/basicly"
LOG_GLOB = ".basicly/ledger/events-*.jsonl"
ROSTER_DOC = "docs/architecture/architecture.md"

_MARKER = re.compile(r"\[harness-[a-z][a-z-]*\]")
_LEADING = re.compile(r"^\s*(\[harness-[a-z][a-z-]*\])")

_DECLARED_CLAIM = re.compile(r"\*\*([a-z]+)\*\* declared families")
_RETIRED_CLAIM = re.compile(r"\*\*([a-z]+)\*\* retired")

_NUMBER_WORDS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
)


class FamilyError(Exception):
    pass


@dataclass(frozen=True)
class Family:
    marker: str
    retired: str | None = None


FROZEN: tuple[Family, ...] = (
    Family("[harness-artifact]"),
    Family("[harness-classification]"),
    Family("[harness-cost]"),
    Family("[harness-decision]"),
    Family("[harness-info]"),
    Family(
        "[harness-overrun]",
        retired=(
            "the context-ceiling follow-up marker; OVERRUN_MARKER was deleted from src/ and "
            "12 rows remain in the log, so a derived list would lose them"
        ),
    ),
    Family("[harness-policy]"),
    Family("[harness-retro]"),
    Family("[harness-review]"),
    Family("[harness-run]"),
    Family("[harness-sizing]"),
    Family("[harness-wait]"),
)


@dataclass(frozen=True)
class Finding:
    key: str
    detail: str
    remedy: str


@dataclass(frozen=True)
class Census:
    rows: dict[str, int]
    comments: int
    stores: tuple[str, ...]


def _prose_nodes(tree: ast.Module) -> set[int]:
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def declared_families(repo: Path) -> dict[str, tuple[str, ...]]:

    root = repo / SRC_ROOT
    if not root.is_dir():
        raise FamilyError(f"no source root at {SRC_ROOT}")
    sites: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as err:
            raise FamilyError(f"could not read {path.as_posix()}: {err}") from err
        prose = _prose_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            found = _MARKER.match(node.value)
            if found and id(node) not in prose:
                sites.setdefault(found.group(0), set()).add(path.relative_to(repo).as_posix())
    return {marker: tuple(sorted(paths)) for marker, paths in sorted(sites.items())}


def _log_bodies(repo: Path) -> Iterator[tuple[str, str]]:
    for path in sorted(repo.glob(LOG_GLOB)):
        store = path.relative_to(repo).as_posix()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("kind") == "comment":
                payload = event.get("payload") or {}
                yield store, str(payload.get("text") or "")


def logged_families(repo: Path) -> Census:

    rows: dict[str, int] = {}
    stores: set[str] = set()
    comments = 0
    for store, text in _log_bodies(repo):
        stores.add(store)
        comments += 1
        found = _LEADING.match(text)
        if found:
            rows[found.group(1)] = rows.get(found.group(1), 0) + 1
    if not stores:
        raise FamilyError(f"no store to read at {LOG_GLOB}")
    if comments and not rows:
        raise FamilyError(f"read {comments} comment bodies and matched no family: bad probe")
    return Census(rows=dict(sorted(rows.items())), comments=comments, stores=tuple(sorted(stores)))


def population_findings(declared: dict[str, tuple[str, ...]], census: Census) -> Iterator[Finding]:
    frozen = {family.marker: family for family in FROZEN}
    for marker in sorted(set(declared) - set(frozen)):
        yield Finding(
            key=marker,
            detail=f"declared in {', '.join(declared[marker])} and not in the frozen list",
            remedy=f'add Family("{marker}") to FROZEN in {SELF}',
        )
    for marker in sorted(set(census.rows) - set(frozen) - set(declared)):
        yield Finding(
            key=marker,
            detail=f"{census.rows[marker]} rows in the stores and not in the frozen list",
            remedy=f'add Family("{marker}", retired="...") to FROZEN — its rows are permanent',
        )
    for marker, family in sorted(frozen.items()):
        if family.retired is None and marker not in declared:
            yield Finding(
                key=marker,
                detail="frozen as live and declared nowhere in the engine",
                remedy='give it retired="why the producer went away" rather than deleting it',
            )
        elif family.retired is not None and marker in declared:
            yield Finding(
                key=marker,
                detail=f"frozen as retired and declared in {', '.join(declared[marker])}",
                remedy="drop its retired reason: the family has a producer again",
            )


def _spelled(count: int) -> str:
    return _NUMBER_WORDS[count] if count < len(_NUMBER_WORDS) else str(count)


def _claim_findings(claim: re.Pattern[str], text: str, count: int, what: str) -> Iterator[Finding]:
    stated = claim.findall(text)
    expected = _spelled(count)
    if len(stated) != 1:
        yield Finding(
            key=f"{ROSTER_DOC}:{what}",
            detail=f"{len(stated)} statements match {claim.pattern!r}, expected exactly one",
            remedy=f'restore the sentence, or re-anchor it: "**{expected}** {what}"',
        )
    elif stated[0] != expected:
        yield Finding(
            key=f"{ROSTER_DOC}:{what}",
            detail=f"states {stated[0]!r} {what} and the tree has {expected}",
            remedy=f"correct it to **{expected}**",
        )


def document_findings(repo: Path, declared: int, retired: int) -> Iterator[Finding]:
    path = repo / ROSTER_DOC
    if not path.is_file():
        yield Finding(
            key=ROSTER_DOC,
            detail="missing, so its family claims cannot be checked",
            remedy=f"restore {ROSTER_DOC} or move the claim and re-point ROSTER_DOC",
        )
        return
    text = path.read_text(encoding="utf-8")
    yield from _claim_findings(_DECLARED_CLAIM, text, declared, "declared families")
    yield from _claim_findings(_RETIRED_CLAIM, text, retired, "retired")
    named = set(_MARKER.findall(text))
    frozen = {family.marker for family in FROZEN}
    for marker in sorted(named - frozen):
        yield Finding(
            key=f"{ROSTER_DOC}:{marker}",
            detail="named as a family and not in the frozen list",
            remedy="drop it, or freeze it here if it was really written",
        )
    for marker in sorted(frozen - named):
        yield Finding(
            key=f"{ROSTER_DOC}:{marker}",
            detail="frozen here and named nowhere in the document",
            remedy="add it to the document's roster",
        )


def collect(repo: Path) -> tuple[list[Finding], dict[str, tuple[str, ...]], Census]:

    declared = declared_families(repo)
    census = logged_families(repo)
    retired = sum(1 for family in FROZEN if family.retired is not None)
    findings = [
        *population_findings(declared, census),
        *document_findings(repo, len(declared), retired),
    ]
    return sorted(findings, key=lambda finding: finding.key), declared, census


def report(findings: Iterable[Finding]) -> None:
    for finding in findings:
        print(f"{LABEL}: {finding.key}: {finding.detail}", file=sys.stderr)
        print(f"{LABEL}:   {finding.remedy}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the harness marker families against the frozen list."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO_ROOT,
        help="the checkout to measure (default: this script's repository)",
    )
    args = parser.parse_args(argv)

    try:
        findings, declared, census = collect(args.repo)
    except FamilyError as exc:
        print(f"{LABEL}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"{LABEL}: a store holds a line that is not JSON: {exc}", file=sys.stderr)
        return 1

    if findings:
        report(findings)
        return 1
    retired = sum(1 for family in FROZEN if family.retired is not None)
    rows = sum(census.rows.values())
    print(
        f"{LABEL}: {len(declared)} declared, {retired} retired ({len(FROZEN)} frozen), "
        f"{rows} rows across {len(census.stores)} stores"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

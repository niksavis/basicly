from __future__ import annotations

import io
import sys
import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from ratchet import (  # noqa: E402 - the path above comes first
    Finding,
    RatchetError,
    report,
    tracked_sources,
)

_LABEL = "except-form"

_NOISE = frozenset({
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.COMMENT,
    tokenize.INDENT,
    tokenize.DEDENT,
})
_OPEN = frozenset("([{")
_CLOSE = frozenset(")]}")

_HOUSE_FORM = (
    "paren-free `except A, B:` is the house form (PEP 758, `python-guidelines`); "
    "parentheses stay only where the clause binds"
)


@dataclass(frozen=True)
class Clause:
    line: int
    source: str
    open_col: int
    close_col: int
    is_tuple: bool
    binds: bool
    single_line: bool

    @property
    def offends(self) -> bool:
        return self.open_col >= 0 and self.is_tuple and not self.binds and self.single_line

    @property
    def house_form(self) -> str:
        text = self.source
        kept = text[: self.open_col] + text[self.open_col + 1 : self.close_col]
        return (kept + text[self.close_col + 1 :]).strip()


def _clause(tokens: list[tokenize.TokenInfo], start: int) -> Clause:
    head = start + 1
    if tokens[head].string == "*":
        head += 1
    wrapped = tokens[head].string == "("
    element_depth = 1 if wrapped else 0
    depth = 0
    close = -1
    is_tuple = False
    binds = False
    index = head
    while index < len(tokens):
        token = tokens[index]
        if token.string in _OPEN:
            depth += 1
        elif token.string in _CLOSE:
            depth -= 1
            if depth == 0 and close < 0:
                close = index
        elif depth == 0 and token.string == ":":
            break
        elif depth == 0 and token.string == "as":
            binds = True
            break
        elif depth == element_depth and token.string == ",":
            is_tuple = True
        index += 1

    end = min(index, len(tokens) - 1)
    whole = wrapped and close == end - 1
    anchor = tokens[start]
    return Clause(
        line=anchor.start[0],
        source=anchor.line,
        open_col=tokens[head].start[1] if whole else -1,
        close_col=tokens[close].start[1] if whole else -1,
        is_tuple=is_tuple,
        binds=binds,
        single_line=anchor.start[0] == tokens[end].start[0],
    )


def clauses(text: str) -> list[Clause]:

    try:
        stream = tokenize.generate_tokens(io.StringIO(text).readline)
        tokens = [token for token in stream if token.type not in _NOISE]
    except (tokenize.TokenError, SyntaxError, ValueError) as err:
        raise RatchetError(f"cannot tokenize: {err}") from err
    return [
        _clause(tokens, index)
        for index, token in enumerate(tokens)
        if token.type == tokenize.NAME and token.string == "except"
    ]


def _finding(path: str, clause: Clause) -> Finding:
    return Finding(
        subject=f"{path}:{clause.line}",
        detail=f"parenthesised `except` tuple binding nothing: {clause.source.strip()}",
        remedy=f"write `{clause.house_form}` — {_HOUSE_FORM}",
    )


def _unreadable(path: str, err: RatchetError) -> Finding:
    return Finding(
        subject=path,
        detail=str(err),
        remedy="fix the syntax `ruff check` reports; the clause form cannot be read past it",
    )


def collect(sources: Iterable[tuple[str, str]]) -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    seen = 0
    for path, text in sources:
        try:
            found = clauses(text)
        except RatchetError as err:
            findings.append(_unreadable(path, err))
            continue
        seen += len(found)
        findings.extend(_finding(path, clause) for clause in found if clause.offends)
    return findings, seen


def main() -> int:
    try:
        sources = list(tracked_sources(REPO_ROOT))
    except RatchetError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1
    if not sources:
        print(f"{_LABEL}: no tracked Python modules found", file=sys.stderr)
        return 1

    findings, seen = collect(sources)
    if findings:
        report(_LABEL, findings)
        return 1
    print(f"{_LABEL}: {seen} `except` clauses across {len(sources)} modules use the house form")
    return 0


if __name__ == "__main__":
    sys.exit(main())

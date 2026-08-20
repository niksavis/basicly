"""Fail when a tracked module parenthesises an `except` tuple that binds nothing.

`python-guidelines` settles the house form on PEP 758: paren-free `except A, B:`, with
parentheses kept only where the clause binds — `except (ValueError, OSError) as err:`. The
floor is `requires-python = ">=3.14"`, so no compatibility argument survives. The rule was
stated only in a skill and leaked: a parenthesised clause reached
`.basicly/core/hooks/pre-push.py` in `0a0d669e`, and the `ruff-format` entry's
`fix_command` rewrote it at commit time, so the author was never told a rule existed.

**`ast` cannot see this rule.** In 3.14 `except A, B:` and `except (A, B):` produce the same
`ExceptHandler` with `type=ast.Tuple`. An AST probe reported 54 offenders on this tree and
every one was conforming. The instrument is `tokenize`, reading the tokens between `except`
and the `:` or the `as`.

**Only one of the two directions can be checked.** Parens removed from a binding clause —
`except A, B as err:` — is a SyntaxError, so no importable module holds one and the
interpreter is already that gate. This script checks the direction that compiles.

**A clause spanning more than one line is exempt.** Parentheses are the only continuation
`ruff format` accepts: measured 2026-08-20 under this repo's config, it rewrites a
backslash-continued paren-free clause *into* the paren-wrapped multi-line form, and honours a
magic trailing comma. Reporting those would leave two gates in conflict over a clause with no
satisfying form.

**`except (ValueError):` is not reported.** With no comma it is not a tuple, so the skill's
rule does not reach it, and `ruff format` strips the redundant parens anyway.

Scope, `Finding` and the output shape come from `ratchet.py`, so the tracked Python tree has
one definition across the gates that scan it. This one is not a ratchet: the tree measures
zero offenders, so there is no debt to freeze and no waiver to spend.

Run::

    uv run python .scripts/check_except_form.py
"""

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

# Layout tokens carry no syntax here, and dropping them lets one index walk a clause that
# spans physical lines without a continuation special case.
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
    """One `except` clause, reduced to the facts the house form turns on.

    `open_col`/`close_col` are the columns of the parentheses wrapping the *whole* clause, or
    -1 when nothing wraps it — `except (A, B)[0]:` wraps a subexpression, not the clause.
    """

    line: int
    source: str
    open_col: int
    close_col: int
    is_tuple: bool
    binds: bool
    single_line: bool

    @property
    def offends(self) -> bool:
        """Wrapped in parentheses, a tuple, binding nothing, and on one line."""
        return self.open_col >= 0 and self.is_tuple and not self.binds and self.single_line

    @property
    def house_form(self) -> str:
        """`source` with the wrapping parentheses taken out."""
        text = self.source
        kept = text[: self.open_col] + text[self.open_col + 1 : self.close_col]
        return (kept + text[self.close_col + 1 :]).strip()


def _clause(tokens: list[tokenize.TokenInfo], start: int) -> Clause:
    """Read the clause opened by the `except` at *start*, `except*` groups included."""
    head = start + 1
    if tokens[head].string == "*":
        head += 1
    wrapped = tokens[head].string == "("
    # Where the clause's own elements sit: inside the wrapping parens, or at the top level.
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
    """Every `except` clause in *text*, in source order.

    Raises:
        RatchetError: *text* cannot be tokenized. Raised rather than skipped, because a file
            the gate could not read reports no findings and so reads as conforming.
    """
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
    """The offending clause, with the rewrite that satisfies the rule spelled out."""
    return Finding(
        subject=f"{path}:{clause.line}",
        detail=f"parenthesised `except` tuple binding nothing: {clause.source.strip()}",
        remedy=f"write `{clause.house_form}` — {_HOUSE_FORM}",
    )


def _unreadable(path: str, err: RatchetError) -> Finding:
    """A tracked module this gate could not tokenize."""
    return Finding(
        subject=path,
        detail=str(err),
        remedy="fix the syntax `ruff check` reports; the clause form cannot be read past it",
    )


def collect(sources: Iterable[tuple[str, str]]) -> tuple[list[Finding], int]:
    """The findings, and how many `except` clauses were read to reach them."""
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
    """Entry point: report every parenthesised `except` tuple that binds nothing."""
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

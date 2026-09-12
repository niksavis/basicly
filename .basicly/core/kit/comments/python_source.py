"""Locate every comment and docstring in Python source, exactly rather than by pattern.

Python is the one language this kit does not have to guess at: `tokenize` yields a
COMMENT token for each comment with its own offsets, and `ast` yields the docstring of
each module, class and function. So this module never inspects a character, and a `#`
inside a string is never a comment here by construction.

The one thing it must decide is what to leave behind. Measured over this repository on
2026-09-12: of 9612 docstrings, 57 are their owner's only statement - almost all of them
a bare exception class. Removing those leaves a body that does not parse, so each carries
``pass`` as its replacement and every other span carries nothing.

It fails closed: source that does not parse raises, and the caller reports the file
rather than editing it.
"""

from __future__ import annotations

import ast
import importlib.util
import io
import sys
import tokenize
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(file_name: str, module_name: str):
    """Load a sibling kit module by path, under the kit's fixed ``sys.modules`` name."""
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, _HERE / file_name)
    if spec is None or spec.loader is None:
        raise ImportError("the comments kit's " + file_name + " is missing from beside it")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


lexer = _load("lexer.py", "basicly_comments_kit_lexer")
Span = lexer.Span
LexError = lexer.LexError

_DOCSTRING_OWNER = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def comment_spans(source: str) -> list:
    """Every comment and docstring in *source*, in offset order.

    Raises LexError where the source does not parse, because a file this cannot read is a
    file it must not edit.
    """
    offsets = _line_offsets(source)
    spans = _comment_tokens(source, offsets)
    spans.extend(_docstrings(source, offsets))
    spans.sort()
    return spans


def _line_offsets(source: str) -> list[int]:
    """The character offset each 1-based line starts at, with index 0 unused."""
    offsets = [0, 0]
    for line in source.split("\n")[:-1]:
        offsets.append(offsets[-1] + len(line) + 1)
    return offsets


def _comment_tokens(source: str, offsets: list[int]) -> list:
    spans = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError as err:
        raise LexError(f"python source does not tokenize: {err}") from err
    except IndentationError as err:
        raise LexError(f"python source does not tokenize: {err}") from err
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        line, column = token.start
        start = offsets[line] + column
        spans.append(Span(start, start + len(token.string), line))
    return spans


def _character_column(line: str, byte_column: int) -> int:
    """*byte_column* as a character index into *line*.

    `ast` reports `col_offset` in UTF-8 **bytes** while `tokenize` reports characters, and
    the two are the same number only for ASCII. A docstring holding one em dash - three
    bytes, one character - overshoots by two, which took the newline and the first space
    of the next line's indentation with it. Measured on this repository 2026-09-12: the
    strip corrupted two files that way and the proof refused both, which is what the proof
    is for.
    """
    return len(line.encode("utf-8")[:byte_column].decode("utf-8"))


def _docstrings(source: str, offsets: list[int]) -> list:
    try:
        tree = ast.parse(source)
    except SyntaxError as err:
        raise LexError(f"python source does not parse: {err}") from err
    lines = source.split("\n")
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_OWNER):
            continue
        if ast.get_docstring(node, clean=False) is None:
            continue
        statement = node.body[0]
        if statement.end_lineno is None or statement.end_col_offset is None:
            continue
        start = offsets[statement.lineno] + _character_column(
            lines[statement.lineno - 1], statement.col_offset
        )
        end = offsets[statement.end_lineno] + _character_column(
            lines[statement.end_lineno - 1], statement.end_col_offset
        )
        replacement = "pass" if len(node.body) == 1 and not isinstance(node, ast.Module) else ""
        spans.append(Span(start, end, statement.lineno, replacement))
    return spans


def parses(source: str) -> bool:
    """True where *source* is valid Python, for the strip to verify its own output."""
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def tree_without_docstrings(source: str) -> str:
    """A dump of *source*'s syntax tree with every docstring removed.

    This is the strip's proof for Python: the dump before and after must be identical, so
    the edit provably changed no statement, no literal and no name.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_OWNER) and ast.get_docstring(node, clean=False) is not None:
            remainder: list[ast.stmt] = list(node.body[1:])
            if not remainder and not isinstance(node, ast.Module):
                remainder.append(ast.Pass())
            node.body = remainder
    return ast.dump(ast.fix_missing_locations(tree))


def read(path: Path) -> str:
    """*path*'s text, decoded as UTF-8 with the newlines the file actually holds."""
    return path.read_text(encoding="utf-8")

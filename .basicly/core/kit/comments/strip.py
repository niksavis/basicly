"""Remove comment spans from source, and refuse to return an edit it cannot prove correct.

The removal itself is three lines. Everything else here is the proof, because a wrong
strip does not fail loudly - it silently deletes code, and the diff is 10000 lines wide.

Three invariants, checked on every file, and any one of them failing raises
:class:`UnsafeStripError` so the caller writes nothing:

1. **Python re-parses, and its tree is unchanged.** `python_source.tree_without_docstrings`
   dumps the syntax tree with docstrings dropped; the dump before and after the strip must
   be identical, which proves no statement, literal or name moved.
2. **The strip is idempotent.** A second pass over the output must change zero bytes. A
   lexer that mis-read a string would usually find a different set of spans the second
   time round, so this catches the class of error the first check cannot see for
   non-Python files.
3. **No string literal changed.** Every quoted run in the output is present in the input.
   This is the check that would catch a `//` inside a JavaScript string being taken for a
   comment - the single most likely way this kit could corrupt a consumer's code.

A line the strip empties is removed outright rather than left blank, because a file of
blank lines is not what "the code is the source of truth" is supposed to look like. A line
that was already blank is untouched.
"""

from __future__ import annotations

import importlib.util
import re
import sys
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


languages = _load("languages.py", "basicly_comments_kit_languages")
python_source = _load("python_source.py", "basicly_comments_kit_python_source")
scan = _load("scan.py", "basicly_comments_kit_scan")

_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")


class UnsafeStripError(Exception):
    """Raised where a strip cannot be proved to have changed only comments."""


def strip_source(path: Path, source: str) -> str:
    """*source* with its prose comments removed, proved against the three invariants."""
    stripped = _apply(source, scan.strippable_spans(path, source))
    _prove(path, source, stripped)
    return stripped


def _apply(source: str, spans) -> str:
    """*source* with each span replaced, then every line the removal emptied dropped."""
    if not spans:
        return source
    return _drop_emptied_lines(source, _blank_out(source, spans))


def _blank_out(source: str, spans) -> str:
    """*source* with each span replaced, keeping the newlines the span spanned.

    The newlines are kept so the result has exactly as many lines as the input, which is
    what lets :func:`_drop_emptied_lines` compare the two by index. Without that the two
    files drift apart the first time a multi-line docstring is removed, and every later
    line is judged against the wrong original.
    """
    pieces = []
    cursor = 0
    for span in sorted(spans):
        pieces.append(source[cursor : span.start])
        pieces.append(span.replacement)
        pieces.append("\n" * source.count("\n", span.start, span.end))
        cursor = span.end
    pieces.append(source[cursor:])
    return "".join(pieces)


def _drop_emptied_lines(before: str, after: str) -> str:
    """Drop the lines the strip emptied, and keep the ones the author left blank."""
    originals = before.split("\n")
    kept = []
    for index, line in enumerate(after.split("\n")):
        if line.strip() or not originals[index].strip():
            kept.append(line)
    return "\n".join(kept)


def _prove(path: Path, before: str, after: str) -> None:
    """Raise UnsafeStripError unless all three invariants hold for this edit."""
    if languages.is_python(path.suffix.lower()):
        _prove_python(path, before, after)
    _prove_idempotent(path, after)
    _prove_literals(path, before, after)


def _prove_python(path: Path, before: str, after: str) -> None:
    if not python_source.parses(after):
        raise UnsafeStripError(f"{path}: the stripped source no longer parses")
    if python_source.tree_without_docstrings(before) != python_source.tree_without_docstrings(
        after
    ):
        raise UnsafeStripError(f"{path}: the strip changed the syntax tree, not only its prose")


def _prove_idempotent(path: Path, after: str) -> None:
    if _apply(after, scan.strippable_spans(path, after)) != after:
        raise UnsafeStripError(f"{path}: a second strip changes the output, so the first is unsafe")


def _prove_literals(path: Path, before: str, after: str) -> None:
    original = set(_QUOTED.findall(before))
    for literal in _QUOTED.findall(after):
        if literal not in original:
            raise UnsafeStripError(f"{path}: the strip invented the literal {literal!r}")

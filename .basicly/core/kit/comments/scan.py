"""Answer one question for one file: where are its comments, and which of them may stay.

The dispatch lives here and nowhere else. Python goes to `python_source`, which is exact;
every other covered language goes to the `lexer` state machine; anything the kit does not
claim - config, data, prose - is reported as uncovered rather than guessed at.

A finding is a comment that is **not** a directive. That subtraction is the whole policy:
`directives` decides what a tool reads, and what a tool reads is not the author's prose.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import NamedTuple

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
lexer = _load("lexer.py", "basicly_comments_kit_lexer")
directives = _load("directives.py", "basicly_comments_kit_directives")
python_source = _load("python_source.py", "basicly_comments_kit_python_source")

LexError = lexer.LexError


class Finding(NamedTuple):
    """One comment a covered file may not keep."""

    path: Path
    line: int
    text: str
    span: object


def is_covered(path: Path) -> bool:
    """True where this kit claims *path*'s language."""
    suffix = path.suffix.lower()
    return languages.is_python(suffix) or languages.for_suffix(suffix) is not None


def all_spans(path: Path, source: str) -> list:
    """Every comment in *source*, directives included, in offset order.

    Raises LexError where the file cannot be read with certainty, and ValueError where the
    kit does not claim the language - the caller has no business editing either.
    """
    suffix = path.suffix.lower()
    if languages.is_python(suffix):
        return python_source.comment_spans(source)
    language = languages.for_suffix(suffix)
    if language is None:
        raise ValueError(f"{path}: the comments kit claims no language for {suffix!r}")
    return lexer.comment_spans(source, language)


def findings(path: Path, source: str) -> list:
    """The comments in *source* that are prose, so the ban refuses them."""
    return [
        Finding(path, span.line, span.text(source), span)
        for span in all_spans(path, source)
        if not directives.is_directive(span.text(source))
    ]


def strippable_spans(path: Path, source: str) -> list:
    """The spans a fix removes: every comment that is not a directive."""
    return [finding.span for finding in findings(path, source)]

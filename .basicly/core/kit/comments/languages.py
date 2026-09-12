"""The comment and string grammar of every language this kit strips.

One responsibility, and it is the table: what a comment looks like in a given file, and
what a string looks like so a delimiter inside one is never mistaken for a comment.
Nothing here reads a file or edits one.

Python is absent on purpose. It has an exact lexer in the standard library and
``python_source`` uses it; a hand-written grammar for Python would be a second, worse
answer to a question already answered.

Config, data and prose formats are absent for a different reason: their comments are the
only place a why can live, so the kit does not claim them.
"""

from __future__ import annotations

from typing import NamedTuple


class StringRule(NamedTuple):
    """One quoted form: its delimiters, and whether a backslash escapes inside it."""

    open: str
    close: str
    escapes: bool


class Language(NamedTuple):
    """The grammar of one family, keyed from a file extension by :func:`for_path`."""

    name: str
    extensions: tuple[str, ...]
    line_comments: tuple[str, ...]
    block_comments: tuple[tuple[str, str], ...]
    strings: tuple[StringRule, ...]
    regex_literals: bool
    word_boundary: bool = False
    heredocs: bool = False


_QUOTES = (StringRule("'", "'", True), StringRule('"', '"', True))
_QUOTES_AND_TEMPLATE = (*_QUOTES, StringRule("`", "`", True))

C_LIKE = Language(
    name="c-like",
    extensions=(
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".h",
        ".hpp",
        ".cs",
        ".go",
        ".java",
        ".kt",
        ".kts",
        ".m",
        ".mm",
        ".php",
        ".rs",
        ".scala",
        ".swift",
    ),
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    strings=_QUOTES,
    regex_literals=False,
)

JAVASCRIPT = Language(
    name="javascript",
    extensions=(".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"),
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    strings=_QUOTES_AND_TEMPLATE,
    regex_literals=True,
)

CSS = Language(
    name="css",
    extensions=(".css",),
    line_comments=(),
    block_comments=(("/*", "*/"),),
    strings=_QUOTES,
    regex_literals=False,
)

SASS = Language(
    name="sass",
    extensions=(".scss", ".sass", ".less"),
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    strings=_QUOTES,
    regex_literals=False,
)

HTML = Language(
    name="html",
    extensions=(".html", ".htm", ".xhtml", ".vue", ".svelte", ".cshtml", ".razor"),
    line_comments=(),
    block_comments=(("<!--", "-->"),),
    strings=(),
    regex_literals=False,
)

SHELL = Language(
    name="shell",
    extensions=(".sh", ".bash", ".zsh", ".ksh"),
    line_comments=("#",),
    block_comments=(),
    strings=(StringRule("'", "'", False), StringRule('"', '"', True)),
    regex_literals=False,
    word_boundary=True,
    heredocs=True,
)

POWERSHELL = Language(
    name="powershell",
    extensions=(".ps1", ".psm1", ".psd1"),
    line_comments=("#",),
    block_comments=(("<#", "#>"),),
    strings=(StringRule("'", "'", False), StringRule('"', '"', True)),
    regex_literals=False,
    word_boundary=True,
)

SQL = Language(
    name="sql",
    extensions=(".sql",),
    line_comments=("--",),
    block_comments=(("/*", "*/"),),
    strings=(StringRule("'", "'", False),),
    regex_literals=False,
)

VISUAL_BASIC = Language(
    name="visual-basic",
    extensions=(".vb", ".vbs"),
    line_comments=("'",),
    block_comments=(),
    strings=(StringRule('"', '"', False),),
    regex_literals=False,
)

PYTHON_EXTENSIONS = (".py", ".pyi")

LANGUAGES = (
    C_LIKE,
    JAVASCRIPT,
    CSS,
    SASS,
    HTML,
    SHELL,
    POWERSHELL,
    SQL,
    VISUAL_BASIC,
)

_BY_EXTENSION = {extension: language for language in LANGUAGES for extension in language.extensions}


def is_python(suffix: str) -> bool:
    """True where *suffix* names a file ``python_source`` owns rather than the lexer."""
    return suffix.lower() in PYTHON_EXTENSIONS


def for_suffix(suffix: str):
    """The language whose grammar covers *suffix*, or None where the kit claims none."""
    return _BY_EXTENSION.get(suffix.lower())


def covered_suffixes() -> tuple[str, ...]:
    """Every extension this kit strips, Python included, for the CLI to print."""
    return tuple(sorted(set(_BY_EXTENSION) | set(PYTHON_EXTENSIONS)))

"""Locate comment spans in a non-Python source file, without ever entering a string.

One responsibility: given source text and a :class:`~languages.Language`, say where the
comments are. It never edits, never reads a file, and never decides whether a comment may
stay - `directives` owns that and `strip` owns the edit.

The whole difficulty is that a comment opener is ordinary text inside a quoted string, so
this walks the source once, tracking exactly one state: in a string, in a comment, or in
code. Three families need more than that and each is handled where it is named below.

**It fails closed.** An unterminated string or block comment raises
:class:`LexError` rather than guessing where it ended, because a guess here deletes
source. The caller reports the file and leaves it alone.

Two boundaries are stated rather than hidden. A `${...}` substitution inside a JavaScript
template literal is treated as string content, so a comment written in there survives -
that leaves prose behind, which is a miss and not a corruption. A `<script>` or `<style>`
block inside HTML is likewise not descended into.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from languages import Language

_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%~^<>")

_WORD_BOUNDARY_BEFORE = frozenset(" \t\n;|&(<")


class LexError(Exception):
    """Raised where the source cannot be lexed with certainty, so it must not be edited."""


class Span(NamedTuple):
    """One comment, as an offset range into the source plus the line it opens on.

    ``replacement`` is empty for all but the 57 measured cases where the prose is its
    owner's only statement - a bare exception class, a stub - and removing it would leave
    a body that does not parse. Those carry ``pass``.
    """

    start: int
    end: int
    line: int
    replacement: str = ""

    def text(self, source: str) -> str:
        """The comment's own characters, opener included."""
        return source[self.start : self.end]


def comment_spans(source: str, language: Language) -> list[Span]:
    """Every comment in *source*, in order, as offset ranges.

    Raises LexError where a string or block comment never closes.
    """
    protected = _heredoc_ranges(source) if language.heredocs else ()
    spans: list[Span] = []
    index = 0
    size = len(source)
    line = 1
    previous = ""
    while index < size:
        char = source[index]
        if char == "\n":
            line += 1
            index += 1
            previous = ""
            continue
        if _within(index, protected):
            index += 1
            continue
        block = _block_at(source, index, language)
        if block is not None:
            opener, closer = block
            end = source.find(closer, index + len(opener))
            if end == -1:
                raise LexError(f"line {line}: block comment opened with {opener!r} never closes")
            end += len(closer)
            spans.append(Span(index, end, line))
            line += source.count("\n", index, end)
            index = end
            previous = ""
            continue
        opener = _line_comment_at(source, index, language, previous)
        if opener is not None:
            end = source.find("\n", index)
            end = size if end == -1 else end
            spans.append(Span(index, end, line))
            index = end
            continue
        rule = _string_at(source, index, language)
        if rule is not None:
            index = _skip_string(source, index, rule, line)
            previous = '"'
            continue
        if language.regex_literals and char == "/" and _regex_here(previous):
            index = _skip_regex(source, index, line)
            previous = "/"
            continue
        if not char.isspace():
            previous = char
        index += 1
    return spans


def _within(index: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= index < end for start, end in ranges)


def _block_at(source: str, index: int, language: Language):
    for opener, closer in language.block_comments:
        if source.startswith(opener, index):
            return opener, closer
    return None


def _line_comment_at(source: str, index: int, language: Language, previous: str):
    for opener in language.line_comments:
        if not source.startswith(opener, index):
            continue
        if language.word_boundary and previous and previous not in _WORD_BOUNDARY_BEFORE:
            continue
        return opener
    return None


def _string_at(source: str, index: int, language: Language):
    for rule in language.strings:
        if source.startswith(rule.open, index):
            return rule
    return None


def _skip_string(source: str, index: int, rule, line: int) -> int:
    """The offset just past the string opening at *index*; raises where it never closes."""
    cursor = index + len(rule.open)
    size = len(source)
    while cursor < size:
        char = source[cursor]
        if rule.escapes and char == "\\":
            cursor += 2
            continue
        if source.startswith(rule.close, cursor):
            return cursor + len(rule.close)
        cursor += 1
    raise LexError(f"line {line}: string opened with {rule.open!r} never closes")


def _regex_here(previous: str) -> bool:
    """True where a `/` at this point opens a regex literal rather than dividing.

    The test is the last significant character: after a value - a name, a digit, a closing
    bracket - a slash is division, and after an operator or an opening bracket it is a
    regex. That is the rule every JavaScript tokenizer uses, and it is a heuristic rather
    than a parse.
    """
    return previous == "" or previous in _REGEX_PRECEDERS


def _skip_regex(source: str, index: int, line: int) -> int:
    cursor = index + 1
    size = len(source)
    in_class = False
    while cursor < size:
        char = source[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "\n":
            raise LexError(f"line {line}: regex literal never closes")
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return cursor + 1
        cursor += 1
    raise LexError(f"line {line}: regex literal never closes")


def _heredoc_ranges(source: str) -> tuple[tuple[int, int], ...]:
    """Offset ranges of every here-document body, which is data rather than code."""
    ranges: list[tuple[int, int]] = []
    offset = 0
    terminator = None
    body_start = 0
    for raw in source.split("\n"):
        length = len(raw) + 1
        if terminator is not None:
            if raw.strip() == terminator:
                ranges.append((body_start, offset))
                terminator = None
        else:
            terminator = _heredoc_tag(raw)
            if terminator is not None:
                body_start = offset + length
        offset += length
    if terminator is not None:
        ranges.append((body_start, len(source)))
    return tuple(ranges)


def _heredoc_tag(line: str):
    marker = line.find("<<")
    if marker == -1:
        return None
    rest = line[marker + 2 :].lstrip("-")
    quote = rest[:1]
    if quote in {"'", '"'}:
        closing = rest.find(quote, 1)
        return rest[1:closing] if closing > 1 else None
    tag = ""
    for char in rest:
        if char.isalnum() or char == "_":
            tag += char
        else:
            break
    return tag or None

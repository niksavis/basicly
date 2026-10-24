from __future__ import annotations

import re
from dataclasses import dataclass

_IDENTIFIER = r"(?:[^\W\d]|[$])[\w$]*"
_NAME = _IDENTIFIER + r"(?:[.:-][\w$]+)*"
_OPEN = re.compile(r"<(" + _NAME + r")(?=[\s/<>])")
_CLOSE = re.compile(r"</(" + _NAME + r")?\s*>")
_TRIVIA = re.compile(r"(?:\s|/\*[\s\S]*?\*/|//[^\r\n]*(?:\r?\n|$))*")
_PARAMETER = re.compile(_IDENTIFIER)
_EXPRESSION_PRECEDERS = frozenset("=([{,:;!&|?<>+-*/%^~")
_EXPRESSION_KEYWORD = re.compile(
    r"\b(?:return|yield|throw|await|case|typeof|void|delete|in|instanceof|of|else|do)\s*$"
)


class JSXError(Exception):
    pass


@dataclass
class _Frame:
    kind: str
    name: str = ""
    depth: int = 0
    type_arguments: bool = True


class State:
    def __init__(self, *, typescript: bool) -> None:
        self.frames = [_Frame("code")]
        self.typescript = typescript

    def advance(self, source: str, index: int, previous: str) -> tuple[int, str] | None:
        frame = self.frames[-1]
        if frame.kind == "text":
            return self._text(source, index, previous)
        if frame.kind == "tag":
            return self._tag(source, index, previous)
        if frame.kind == "type":
            return self._type(source, index)
        return self._code(source, index, previous)

    def _code(self, source: str, index: int, previous: str) -> tuple[int, str] | None:
        frame = self.frames[-1]
        char = source[index]
        if frame.depth and char in "{}":
            frame.depth += 1 if char == "{" else -1
            if frame.depth == 0:
                self.frames.pop()
                return index + 1, "v"
        if char == "<" and self._starts_expression(source, index, previous):
            if self.typescript and _generic_arrow(source, index):
                self.frames.append(_Frame("type", depth=1))
                return index + 1, "<"
            return self._open(source, index)
        return None

    def finish(self) -> None:
        if len(self.frames) > 1:
            frame = self.frames[-1]
            raise JSXError(f"JSX {frame.kind} {frame.name!r} never closes")

    def _text(self, source: str, index: int, previous: str) -> tuple[int, str]:
        if source.startswith("</", index):
            return self._close(source, index)
        if source[index] == "<":
            opened = self._open(source, index)
            if opened is None:
                raise JSXError("JSX text contains an unreadable opening tag")
            return opened
        if source[index] == "{":
            self.frames.append(_Frame("code", depth=1))
            return index + 1, "{"
        return index + 1, previous

    def _tag(self, source: str, index: int, previous: str) -> tuple[int, str] | None:
        char = source[index]
        if char in "\"'" or source.startswith(("//", "/*"), index):
            if char in "\"'":
                self.frames[-1].type_arguments = False
            return None
        if char == "<" and self.frames[-1].type_arguments:
            self.frames[-1].type_arguments = False
            self.frames.append(_Frame("type", depth=1))
            return index + 1, "<"
        if source.startswith("/>", index):
            self.frames.pop()
            return index + 2, "v"
        if char == ">":
            self.frames[-1].kind = "text"
            return index + 1, "v"
        if char == "{":
            self.frames[-1].type_arguments = False
            self.frames.append(_Frame("code", depth=1))
            return index + 1, "{"
        if char in "</}":
            raise JSXError(f"JSX tag contains unexpected {char!r}")
        if not char.isspace():
            self.frames[-1].type_arguments = False
        return index + 1, previous

    def _type(self, source: str, index: int) -> tuple[int, str] | None:
        char = source[index]
        if char in "\"'`" or source.startswith(("//", "/*"), index):
            return None
        frame = self.frames[-1]
        if char == "<":
            frame.depth += 1
        elif char == ">" and source[index - 1] != "=":
            frame.depth -= 1
            if frame.depth == 0:
                self.frames.pop()
        return index + 1, char

    def _open(self, source: str, index: int) -> tuple[int, str] | None:
        if source.startswith("<>", index):
            self.frames.append(_Frame("text"))
            return index + 2, "v"
        match = _OPEN.match(source, index)
        if match is None:
            return None
        self.frames.append(_Frame("tag", name=match.group(1)))
        return match.end(), "v"

    def _close(self, source: str, index: int) -> tuple[int, str]:
        match = _CLOSE.match(source, index)
        name = self.frames[-1].name
        if match is None or (match.group(1) or "") != name:
            raise JSXError(f"JSX closing tag does not match {name!r}")
        self.frames.pop()
        return match.end(), "v"

    @staticmethod
    def _starts_expression(source: str, index: int, previous: str) -> bool:
        prefix = source[:index].rstrip()
        if previous == "<" and source[index - 1 : index] == "<":
            return False
        if previous in {"+", "-"} and prefix.endswith(previous * 2):
            return False
        if previous == "!":
            operand = prefix.rstrip("!").rstrip()
            if operand and (operand[-1].isalnum() or operand[-1] in "_$)]'\"`"):
                return _EXPRESSION_KEYWORD.search(operand) is not None
        return (
            not previous
            or previous in _EXPRESSION_PRECEDERS
            or _EXPRESSION_KEYWORD.search(source[:index]) is not None
        )


def _generic_arrow(source: str, index: int) -> bool:
    position = _after_trivia(source, index + 1)
    parameter = _PARAMETER.match(source, position)
    if parameter is not None and parameter.group() == "const":
        position = _after_trivia(source, parameter.end())
        parameter = _PARAMETER.match(source, position)
    if parameter is None:
        return False
    position = _after_trivia(source, parameter.end())
    if source[position : position + 1] in {",", "="}:
        return True
    keyword = _PARAMETER.match(source, position)
    if keyword is None or keyword.group() != "extends":
        return False
    position = _after_trivia(source, keyword.end())
    return position < len(source) and source[position] not in "=>/"


def _after_trivia(source: str, index: int) -> int:
    matched = _TRIVIA.match(source, index)
    if matched is None:
        raise JSXError("JSX lookahead extends beyond the source")
    return matched.end()

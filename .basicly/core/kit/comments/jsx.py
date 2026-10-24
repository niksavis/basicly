from __future__ import annotations

import re
from dataclasses import dataclass

_NAME = r"(?:[^\W\d]|[$])[\w$]*(?:[.:-][\w$]+)*"
_OPEN = re.compile(r"<(" + _NAME + r")(?=[\s/>])")
_CLOSE = re.compile(r"</(" + _NAME + r")?\s*>")
_EXPRESSION_PRECEDERS = frozenset("=([{,:;!&|?>+-*%^~")
_EXPRESSION_KEYWORD = re.compile(
    r"\b(?:return|yield|throw|await|case|typeof|void|delete|in|instanceof|of|else|do)\s*$"
)


class JSXError(Exception):
    pass


@dataclass
class _Frame:
    kind: str
    name: str = ""
    braces: int = 0


class State:
    def __init__(self) -> None:
        self.frames = [_Frame("code")]

    def advance(self, source: str, index: int, previous: str) -> tuple[int, str] | None:
        frame = self.frames[-1]
        if frame.kind == "text":
            return self._text(source, index, previous)
        if frame.kind == "tag":
            return self._tag(source, index, previous)
        char = source[index]
        if frame.braces and char in "{}":
            frame.braces += 1 if char == "{" else -1
            if frame.braces == 0:
                self.frames.pop()
                return index + 1, "v"
        if char == "<" and self._starts_expression(source, index, previous):
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
            self.frames.append(_Frame("code", braces=1))
            return index + 1, "{"
        return index + 1, previous

    def _tag(self, source: str, index: int, previous: str) -> tuple[int, str] | None:
        char = source[index]
        if char in "\"'":
            return None
        if source.startswith("/>", index):
            self.frames.pop()
            return index + 2, "v"
        if char == ">":
            self.frames[-1].kind = "text"
            return index + 1, "v"
        if char == "{":
            self.frames.append(_Frame("code", braces=1))
            return index + 1, "{"
        if char in "</}":
            raise JSXError(f"JSX tag contains unexpected {char!r}")
        return index + 1, previous

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
        return (
            not previous
            or previous in _EXPRESSION_PRECEDERS
            or _EXPRESSION_KEYWORD.search(source[:index]) is not None
        )

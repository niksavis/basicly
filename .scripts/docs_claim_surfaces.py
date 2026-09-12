from __future__ import annotations

import argparse
import re
from typing import TYPE_CHECKING

from docs_claim_sources import ClaimError, read_text, subparsers

from basicly import cli

if TYPE_CHECKING:
    from pathlib import Path

CONSUMER_SURFACES = ("README.md", "site/index.html")

_SHELL_INFO = frozenset({"", "sh", "bash", "shell", "zsh", "console", "powershell", "ps1"})
_FENCE = re.compile(
    r"^(?P<fence>```|~~~)(?P<info>[^\n`~]*)\n(?P<body>.*?)^(?P=fence)", re.MULTILINE | re.DOTALL
)
_INLINE = re.compile(r"`[^`\n]+`")
_HTML_CODE = re.compile(r"<code[^>]*>.*?</code>|<pre[^>]*>.*?</pre>", re.DOTALL)
_INVOCATION = re.compile(r"\bbasicly[ \t]+([a-z][a-z-]*)(?:[ \t]+([a-z][a-z-]*))?")


def code_spans(text: str, path: str) -> list[str]:

    if path.endswith(".html"):
        return _HTML_CODE.findall(text)
    spans = [
        match["body"]
        for match in _FENCE.finditer(text)
        if match["info"].strip().split(" ")[0].lower() in _SHELL_INFO
    ]
    spans.extend(_INLINE.findall(text))
    return spans


def cli_command_paths() -> tuple[set[tuple[str, ...]], frozenset[str]]:

    top = subparsers(cli._build_parser())
    if top is None:  # pragma: no cover - the CLI is a subcommand parser by construction
        raise ClaimError("the CLI parser declares no subcommands")

    paths: set[tuple[str, ...]] = set()
    bare: set[str] = set()
    for name, parser in top.choices.items():
        paths.add((name,))
        group = subparsers(parser)
        if group is not None:
            paths.update((name, sub) for sub in group.choices)
            continue
        if not any(
            not action.option_strings and not isinstance(action, argparse._SubParsersAction)
            for action in parser._actions
        ):
            bare.add(name)
    return paths, frozenset(bare)


def consumer_commands_exist(root: Path, surface: str) -> list[str]:

    shipped, bare = cli_command_paths()
    groups = {path[0] for path in shipped if len(path) > 1}
    unknown: set[str] = set()
    for span in code_spans(read_text(root / surface), surface):
        for command, sub in _INVOCATION.findall(span):
            if (command,) not in shipped:
                unknown.add(f"basicly {command}")
            elif sub and (command in bare or (command in groups and (command, sub) not in shipped)):
                unknown.add(f"basicly {command} {sub}")
    if unknown:
        return [f"names commands the CLI does not ship: {', '.join(sorted(unknown))}"]
    return []

"""Refuse an `rg` short-flag cluster that swallows `-r`'s replacement value.

PreToolUse, basicly-0m5hn5w. Wired into ``.claude/settings.json`` by ``basicly hooks-build``.

``-r`` is ``--replace`` and takes a value, so in a cluster the letters after it *become*
that value: ``rg -rn <pattern>`` searches with replacement ``n``, prints every match with
the pattern substituted and no line numbers, and the output reads as a real finding. The
failure is silent — it nearly produced a fabricated report of a missing config key.

Prose was the first answer and it does not bind, because ``-rn`` reads as "recursive plus
line numbers" to anyone who learned the habit from ``grep``. Ripgrep recurses by default,
so the ``r`` is never wanted.

**Measured 2026-09-12** over the 17391 recorded Bash calls for this repository: 2977 are
``rg`` invocations, **92** of them (3.1%) carry a cluster with a letter after ``r``, and
**6** use ``-r`` deliberately — every one of those as its own token, ``-r ''`` or
``-r '$1'``. So a cluster is the discriminator and its false-positive population is empty.

Fails open: a malformed payload, a non-Bash call, or anything it cannot parse exits 0.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shell_tokens import split_pipeline_segments, strip_heredocs

BLOCK_EXIT_CODE = 2

# A short cluster holding `r` with at least one letter after it. `-r` alone, `-er`, and
# `--replace` are the deliberate forms and are absent here.
_TRAP_CLUSTER = re.compile(r"^-[a-qs-zA-Z]*r[a-zA-Z]+$")

_RG_NAMES = frozenset({"rg", "ripgrep"})


def command_text(payload: object) -> str:
    """The shell command a Bash tool call is about to run, or '' when there is none."""
    if not isinstance(payload, dict):
        return ""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if str(tool).lower() not in {"bash", "shell"}:
        return ""
    args = payload.get("tool_input") or payload.get("toolArgs") or {}
    return (args.get("command") or "") if isinstance(args, dict) else ""


def swallowed_replacements(command: str) -> tuple[str, ...]:
    """The `rg` flag clusters in *command* whose trailing letters become a replacement."""
    found: list[str] = []
    for segment in split_pipeline_segments(strip_heredocs(command)):
        tokens = segment.split()
        index = 0
        while index < len(tokens) and "=" in tokens[index] and not tokens[index].startswith("-"):
            index += 1
        if index >= len(tokens) or tokens[index].rsplit("/", maxsplit=1)[-1] not in _RG_NAMES:
            continue
        found.extend(token for token in tokens[index + 1 :] if _TRAP_CLUSTER.match(token))
    return tuple(dict.fromkeys(found))


_ADVICE = (
    "`-r` is `--replace` and takes a value, so the letters after it BECOME the "
    "replacement: the pattern is substituted in every printed line, the line numbers "
    "vanish, and the output still reads as a real finding. Measured here: 92 of 2977 "
    "rg calls did this and only 6 wanted --replace, which is why this is a hook.\n"
    "Ripgrep recurses by default, so drop the r:\n"
    "  rg -n <pattern> <path>        # line numbers, recursive already\n"
    "  rg -l <pattern> <path>        # names only\n"
    "  rg --replace '' <pattern> .   # when you really do want a replacement"
)


def main() -> int:
    """Exit 2 to refuse the call when a cluster would turn a search into a substitution."""
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError, ValueError:
        return 0
    command = command_text(payload)
    if not command:
        return 0
    clusters = swallowed_replacements(command)
    if not clusters:
        return 0
    subject = ", ".join(f"`{cluster}`" for cluster in clusters)
    print(f"rg-replace-guard: refusing {subject}: {_ADVICE}", file=sys.stderr)
    return BLOCK_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())

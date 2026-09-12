"""Refuse a command whose head is a bare `$VAR` that same command assigned a phrase.

PreToolUse, basicly-gmdvdwo. Wired into ``.claude/settings.json`` by ``basicly hooks-build``.

zsh does not word-split an unquoted expansion, so ``W="uv run x --"; $W a b`` looks for one
command literally named ``uv run x --``. It exits 127 and the chain runs on, so the failure
reads as one odd line in a green transcript. The rule is already always-on in the
``non-interactive-shell`` fragment and a consumer who had read it still hit this eight times
in a row, which is the same conclusion ``pipe-status-guard`` reached.

**The discriminator is the assignment, not the variable.** Measured 2026-09-12 over 17358
recorded Bash calls: a bare ``$VAR`` head occurs in 4 (three recorded failing with 127, one
never ran), while a quoted ``"$VAR"`` head occurs in 54 and is always an executable path
holding a space. ``$EDITOR``, ``$SHELL`` and ``$PAGER`` occur 0 times, so the population that
would have forced a warning rather than a refusal is empty.

So the fire set needs the same string to assign that variable a value holding whitespace.
Shell state does not persist between Bash calls here, so the assignment is in the one string
or this guard has nothing to judge — and an unset environment variable can never be reached.

Fails open: a malformed payload, a non-Bash call, or anything it cannot parse exits 0.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# The sibling parser, imported the way `pipe-status-guard.py` imports it: a hook is run
# by path under whatever interpreter the host provides, and a test loads it through
# `spec_from_file_location`, so neither puts this directory on `sys.path`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shell_tokens import split_pipeline_segments, strip_heredocs

BLOCK_EXIT_CODE = 2

# A head token that is nothing but an expansion: `$VAR` or `${VAR}`. A quoted `"$VAR"`
# does not match, which is the whole discriminator.
_BARE_HEAD = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")

# A `VAR=value` prefix, which stands before the command rather than being it.
_ASSIGN_PREFIX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def command_text(payload: object) -> str:
    """The shell command a Bash tool call is about to run, or '' when there is none."""
    if not isinstance(payload, dict):
        return ""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if str(tool).lower() not in {"bash", "shell"}:
        return ""
    args = payload.get("tool_input") or payload.get("toolArgs") or {}
    return (args.get("command") or "") if isinstance(args, dict) else ""


def _assigned_phrases(command: str) -> set[str]:
    """Names this command assigns a value that holds whitespace once expanded."""
    names: set[str] = set()
    for match in re.finditer(
        r"(?:^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S*)", command
    ):
        value = match.group(2)
        if value[:1] in {'"', "'"}:
            value = value[1:-1]
        if " " in value or "\t" in value:
            names.add(match.group(1))
    return names


def _head_token(segment: str) -> str:
    """The raw command-position token of a segment, assignment prefixes skipped."""
    for token in segment.strip().split():
        if not _ASSIGN_PREFIX.match(token):
            return token
    return ""


def unsplit_command_vars(command: str) -> tuple[str, ...]:
    """Names used bare in command position after this command assigned them a phrase.

    Empty for all but 4 of the 17353 recorded calls, which is what keeps the refusal
    from becoming noise the operator switches off.
    """
    phrases = _assigned_phrases(command)
    if not phrases:
        return ()
    found: list[str] = []
    for segment in split_pipeline_segments(strip_heredocs(command)):
        match = _BARE_HEAD.match(_head_token(segment))
        if match and match.group(1) in phrases:
            found.append(match.group(1))
    return tuple(dict.fromkeys(found))


_ADVICE = (
    "zsh does not word-split an unquoted expansion, so the whole value becomes ONE "
    "command name and the call exits 127 while the rest of the chain runs on — a "
    "consumer hit this eight times in a row after reading the rule, which is why this "
    "is a hook and not a note.\n"
    "Use one of:\n"
    "  uv run basicly tracker write -- dep add a b       # write the prefix out\n"
    '  args=(uv run basicly tracker write --); "${args[@]}" dep add a b\n'
    "  alias-free: put the repeated prefix in a shell function, then call the function\n"
    "Then check the command actually ran, not just that the chain returned."
)


def main() -> int:
    """Exit 2 to refuse the call when a variable holding a phrase heads a command."""
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError, ValueError:
        return 0
    command = command_text(payload)
    if not command:
        return 0
    names = unsplit_command_vars(command)
    if not names:
        return 0
    subject = ", ".join(f"`${name}`" for name in names)
    print(f"bare-var-guard: refusing {subject} in command position: {_ADVICE}", file=sys.stderr)
    return BLOCK_EXIT_CODE


if __name__ == "__main__":
    sys.exit(main())

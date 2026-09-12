from __future__ import annotations

import re
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING, Protocol

from . import plan_record, tracker

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_SPAN = re.compile(r"`([^`\n]+)`")

_PYTEST = "pytest"
_COLLECT_ONLY = ("--collect-only", "-q", "-p", "no:cacheprovider")

_SELECTORS = ("-k", "-m")
_REPORT_ONLY = ("-q", "--quiet", "-v", "-s", "-x", "--exitfirst", "--tb")

_NOTHING_COLLECTED = 5
_COLLECT_TIMEOUT = 60.0


class Demonstrated(Protocol):
    @property
    def title(self) -> str: ...

    @property
    def demonstration(self) -> str | None: ...


def collects_nothing(repo_root: Path, demonstration: str) -> bool:

    argv = _collect_argv(demonstration)
    if argv is None:
        return False
    try:
        collected = subprocess.run(  # noqa: S603 — argv rebuilt from an allow-list, no shell
            [sys.executable, "-m", _PYTEST, *_COLLECT_ONLY, *argv],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=_COLLECT_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return False
    return collected.returncode == _NOTHING_COLLECTED


def _collect_argv(demonstration: str) -> list[str] | None:
    for span in _SPAN.findall(demonstration):
        try:
            words = shlex.split(span)
        except ValueError:
            continue
        if _PYTEST not in words:
            continue
        argv: list[str] = []
        rest = iter(words[words.index(_PYTEST) + 1 :])
        for word in rest:
            if word in _SELECTORS:
                argv += [word, next(rest, "")]
            elif word.startswith(_REPORT_ONLY):
                continue
            elif word.startswith("-"):
                return None
            else:
                argv.append(word)
        return argv
    return None


def plan_notice(repo_root: Path, children: Sequence[Demonstrated]) -> str:

    unrun = [
        child.title for child in children if collects_nothing(repo_root, child.demonstration or "")
    ]
    if not unrun:
        return ""
    return (
        " — demonstration collects nothing today (fine for a test this plan will write, a "
        "typo otherwise): " + ", ".join(repr(title) for title in unrun)
    )


def unrun_reason(repo_root: Path, issue_id: str) -> str:

    record = tracker.read_record(repo_root, issue_id)
    description = record.get("description") if isinstance(record, dict) else None
    if not isinstance(description, str):
        return ""
    demonstration = plan_record.parse_plan_section(description).demonstration
    if not demonstration or not collects_nothing(repo_root, demonstration):
        return ""
    return (
        f"{issue_id} would close against a demonstration that collects no test "
        f"({demonstration!r}); the work claims to be done, so the test it names was "
        "supposed to exist by now — write it under that name, or correct the "
        "demonstration line on the bead to the check that does exercise it"
    )

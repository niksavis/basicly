"""What refused, read off the output a gated command left behind.

Three code paths reported a hook refusal on 2026-08-21 and none named a check: the salvage
commit quoted ``protect-generated-commit...Passed`` as its rejection reason, and two lane
closes printed only the argv and the exit code. One shape in three places — the output
holding the answer was summarised by position (its last line) or by stream (``stderr or
stdout``) instead of by structure (basicly-fi1i7z).

Both shapes it reads were observed, not composed: pre-commit's per-hook verdict line and
block (the constants below), and a repo-local ratchet gate prefixing every line with its own
name, ``release-notes: <subject>: <detail>`` then an indented remedy
(``.scripts/ratchet.py`` ``report``), which is what :func:`check_remedy` keys on.

**The reason is chosen by what a line claims, not by where it sits.** A tail was the first
design and real output refuted it: this repo's ``pre-commit-script`` hook wraps the whole
verify suite, so its block ends on a list of check names while the answer, ``checks failed:
28/32 passed ... (failed: ...)``, sits six lines earlier.

Nothing here runs a command or decides a verdict, which is what lets it sit below
:mod:`basicly.checkout` in the import contract.
"""

from __future__ import annotations

# comment-density-waiver: cohesion: 51.7% of a 2,208-token module, and the prose
# is the two output formats this parses. Both were read off their producers —
# `pre_commit/commands/run.py` for the verdict line and block, `.scripts/ratchet.py` for the
# labelled one — and a parser whose fixtures are composed rather than observed is how the defect
# it replaces was written. Also load-bearing: the tail design that real output refuted, without
# which the next reader re-derives it, and the reason a chainless failure keeps its old wording.
import os
from dataclasses import dataclass
from pathlib import Path

# Read off `pre_commit.commands.run`, which builds each line as
# `f"{name}{dots}{postfix}{verdict}"`, rather than recalled.
_VERDICTS = ("Failed", "Passed", "Skipped")
_NO_FILES = "(no files to check)"

# Annotations that restate what the message already carries.
_ANNOTATIONS = ("- hook id:", "- duration:", "- exit code:")

# A formatter that rewrote a file fails with this line and often no output at all, so
# dropping it would leave nothing to report.
_MODIFIED = "- files were modified by this hook"

# A check stating its own verdict, in the vocabulary this repo's gates use.
_STATED = ("FAILED:", "checks failed:", "BROKEN")

# Weaker evidence, for a third-party tool that prints no summary line.
_FAILURE_WORDS = ("error", "fail", "broken")

# The reason goes in a one-line block detail, so it stays a sentence; the transcript is
# what the dump file is for.
_REASON_LINES = 3
_REASON_CHARS = 400

# Under `.basicly/usage/` for the reason `verify_artifact.RUN_ARTIFACT` is: a landing
# refuses to merge while the checkout carries dirt outside the tracker, so a file a
# failing command rewrites must not be tracked.
OUTPUT_DUMP = Path(".basicly/usage/gate-output.txt")


@dataclass(frozen=True)
class Refusal:
    """One check that refused, and the reason it reported."""

    check: str
    reason: str

    def __str__(self) -> str:
        """The refusal as an operator reads it."""
        return f"`{self.check}` refused: {self.reason}"


def _verdict(line: str) -> tuple[str, str] | None:
    """``(name, verdict)`` when *line* is one of pre-commit's verdict lines, else None.

    The dots discriminate: a line merely *ending* in "Failed" is something a hook printed.
    A name long enough to leave no dots is unmatched, and lands in :func:`summarise`'s
    "names no failing check" branch rather than being guessed at.
    """
    for verdict in _VERDICTS:
        head = line.removesuffix(verdict)
        if head == line:
            continue
        padded = head.removesuffix(_NO_FILES)
        name = padded.rstrip(".")
        if name and name != padded:
            return name, verdict
    return None


def _stated_lines(body: list[str]) -> list[str]:
    """The lines of *body* that claim a failure, by the strongest available evidence."""
    if stated := [line for line in body if any(mark in line for mark in _STATED)]:
        return stated
    return [line for line in body if any(word in line.lower() for word in _FAILURE_WORDS)] or body


def _reason(block: list[str]) -> str:
    """The reason a failing hook's *block* reports, capped to one sentence's worth.

    The surviving annotation loses its bullet so it reads as a sentence, not a transcript.
    """
    body = [
        _MODIFIED.removeprefix("- ") if line.strip() == _MODIFIED else line
        for line in block
        if line.strip() and not line.startswith(_ANNOTATIONS)
    ]
    if not body:
        return "the hook reported no output"
    reason = " · ".join(line.strip() for line in _stated_lines(body)[-_REASON_LINES:])
    return reason if len(reason) <= _REASON_CHARS else reason[:_REASON_CHARS] + "…"


def ran_hooks(output: str) -> bool:
    """Whether *output* holds a pre-commit chain at all.

    The caller's discriminator between "a hook refused" and "git itself refused": a failing
    ``git rev-parse`` has no check to name, and naming one would be the invention this
    module exists to stop.
    """
    return any(_verdict(line) is not None for line in output.splitlines())


def refusals(output: str) -> tuple[Refusal, ...]:
    """Every check in *output* that refused, in the order the chain ran them.

    A verdict other than ``Failed`` closes its block and contributes nothing, which is the
    whole guarantee that no passing verdict reaches a message.
    """
    found: list[Refusal] = []
    name = ""
    block: list[str] = []
    for line in output.splitlines():
        seen = _verdict(line)
        if seen is None:
            if name:
                block.append(line)
            continue
        if name:
            found.append(Refusal(name, _reason(block)))
        name, block = (seen[0] if seen[1] == "Failed" else ""), []
    if name:
        found.append(Refusal(name, _reason(block)))
    return tuple(found)


def write_output(repo_root: Path, output: str) -> Path | None:
    """Persist *output* to :data:`OUTPUT_DUMP`; its path, or None when unwritable.

    Never raises: an exception here would discard the refusal the caller is reporting, where
    a message naming no path is merely degraded. Pid-scoped temporary for the reason
    :func:`basicly.verify_artifact.write_run_artifact` gives.
    """
    path = Path(repo_root) / OUTPUT_DUMP
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(output, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        return None
    return path


def summarise(output: str, *, repo_root: Path | None = None) -> str | None:
    """Name what refused in *output*, or None when no hook chain ran in it.

    None is "not my question" and is distinct from the string returned when a chain *did*
    run and named nothing: the first leaves the caller's wording alone, the second replaces
    it with an admission.
    """
    if not ran_hooks(output):
        return None
    if refused := refusals(output):
        return "; ".join(str(refusal) for refusal in refused)
    written = write_output(repo_root, output) if repo_root is not None else None
    where = f"the full output is in {OUTPUT_DUMP.as_posix()}" if written else "it was not captured"
    return f"a hook refused but its output names no failing check; {where}"


def check_remedy(output: str, check: str) -> str | None:
    """The lines *check* prefixed with its own name in *output*, or None when it printed none.

    Carrying the remedy is the point: it is the half that tells an operator what to do, and
    the gate has already written it by the time anything reads this.
    """
    label = f"{check}:"
    lines = [line.strip() for line in output.splitlines() if line.strip().startswith(label)]
    if not lines:
        return None
    joined = " · ".join(line.removeprefix(label).strip() for line in lines)
    return joined if len(joined) <= _REASON_CHARS else joined[:_REASON_CHARS] + "…"

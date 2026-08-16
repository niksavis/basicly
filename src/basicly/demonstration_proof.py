"""What a unit's declared demonstration selects when it is actually run.

The boundary is *running the claim* against *judging the field*:
:mod:`basicly.plan_gate` decides whether a proposed child named a demonstration at all
and never runs one, and nothing here reads a plan document. No form rule can tell a
command that selects nothing from one that selects everything, which is why the
collector exists — and it is execution, not judgement, which is why it is not in the
gate.

**The same answer means different things at the two moments it is asked** and that is
the whole design (basicly-u2hl.58). At plan time a demonstration is a *promise*: ``pytest
tests/test_handoff.py -k unwired`` collecting nothing is the ordinary honest case,
because the test is what the child will write — so :func:`plan_notice` reports and
admits, the call D19 already makes for an over-large diff. At close it is a *claim of
completion*, so :func:`unrun_reason` refuses. The measured failure was five beads closed
in one session against a selector matching nothing, every one of whose real regressions
existed under another name; not one was a bad plan, so a gate placed at plan time refuses
the honest majority and gets waived away.
"""

from __future__ import annotations

# comment-density-waiver: 57.4% of a 1,940-token module, and the prose is the finding. The
# same zero-selection result is a report at one rung and a refusal at the next; delete the
# paragraph saying why and the next reader "simplifies" them into one rule, which is the
# first cut of this gate (it refused two honest plans out of three probed) and the defect
# it replaced (five beads closed against a selector nobody ran). Also load-bearing: the
# exit codes that count as an answer, and the flags that reach the collector.
import re
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING, Protocol

from . import br, plan_record

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# A backticked span inside one line. A second spelling of the shape
# `plan_record._BACKTICKED` matches, deliberately: that one asks whether a whole entry is
# a single backticked value, this one pulls the runnable things out of a sentence.
_SPAN = re.compile(r"`([^`\n]+)`")

# The one command this module runs rather than reads, in any spelling (`pytest`,
# `python -m pytest`, `uv run pytest`). `--collect-only` is a bounded read; shelling a
# free-form demonstration string is not, so the argv is rebuilt from the allow-list below
# rather than passed through. `no:cacheprovider` because a gate must not leave a
# `.pytest_cache` behind in the tree it is judging.
_PYTEST = "pytest"
_COLLECT_ONLY = ("--collect-only", "-q", "-p", "no:cacheprovider")

# `-q`/`-v`/`-s`/`-x`/`--tb` change only the report and are dropped. Any other flag stops
# the run: `-p` loads an arbitrary module, `--ignore`/`--deselect` change what is
# collected, and a flag nobody vetted must not reach the collector.
_SELECTORS = ("-k", "-m")
_REPORT_ONLY = ("-q", "--quiet", "-v", "-s", "-x", "--exitfirst", "--tb")

# pytest's `EXIT_NOTESTSCOLLECTED`, and the only code that counts as an answer of zero: 4
# (usage error, what a target nobody has written yet gives) and 2/3 (collection error)
# are the collector failing to answer, and an unanswered question is not a finding. The
# timeout is 30x the 2.0s a whole-suite `--collect-only` measured here.
_NOTHING_COLLECTED = 5
_COLLECT_TIMEOUT = 60.0


class Demonstrated(Protocol):
    """A unit carrying a title and the demonstration it declared.

    Structural rather than an import of ``plan_gate.PlannedUnit``: the module that runs
    a claim must not depend on the one that judges the plan carrying it, which is the
    same direction :mod:`basicly.plan_record` satisfies that protocol in.
    """

    @property
    def title(self) -> str:
        """The proposed child's title, as the plan names it."""
        ...

    @property
    def demonstration(self) -> str | None:
        """How this unit is exercised end to end, through the consumer surface."""
        ...


def collects_nothing(repo_root: Path, demonstration: str) -> bool:
    """Whether *demonstration* names a pytest run *repo_root* collects no test for.

    False for everything this module does not run, which is most demonstrations: D18
    admits a ``basicly`` transcript and an HTTP request, so treating what cannot be run
    as a finding would report against the forms the rule was written to allow.

    Run in *repo_root* rather than the process cwd, which a lane's advance may be sitting
    anywhere under: a relative target in the demonstration is written against the tree
    that declared it.
    """
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
    """The targets and selectors of the first backticked pytest run, else ``None``."""
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
    """A one-line suffix naming the children whose demonstration collects nothing, else ``""``.

    A report, never a refusal: the remedy is the author's, because either the named test
    is what this child will write or the selector is a typo, and only the author knows
    which.
    """
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
    """Why *issue_id* may not be closed against its recorded demonstration, else ``""``.

    Empty for the populations that are not findings: a bead recorded before D18 carries
    no demonstration line, and one this module does not run is no evidence either way.
    Empty too when the record cannot be read, which is no hole — the close is the next
    thing the caller does through that same tracker, so it fails there instead.
    """
    record = br.read_record(repo_root, issue_id)
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

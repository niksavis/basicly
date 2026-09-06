# comment-density-waiver: cost(basicly-kya2os.1): what left at the module-size split was
# the code - 125 lines moved and 31 of them were prose - leaving a table of six argv
# declarations and the reason for each. Three compression passes did not move it off 54.1%,
# and one that did was reverted for truncating rationale mid-sentence.
"""The board's action table: six `basicly` invocations, and no authority of its own.

**The board is a renderer with a keyboard.** Every action is an argv list handed to the
installed `basicly` CLI as a subprocess, taken from :data:`ACTIONS` - a closed table. Nothing
here writes a file, opens a tracker, or imports an engine module that can;
`.importlinter`'s `consumer-reads-only-the-snapshot` contract is the structural half of that
claim and the absence of a single read in this file is the other. The engine disposes, so an
action's outcome is whatever exit code the CLI gave it.

How one of these is run, replied to and audited is `board_action_surface`, and the reasoning
about the confirm code, redaction and the HTTP trust boundary moved there with the code it
describes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

# The one route that takes a POST. A board with actions disabled never registers it, which is
# what makes a read-only board a structural refusal rather than a check someone can miss.
ROUTE = "/action"

# Long enough for a kill reason or a decision answer, short enough that a form post is not a
# way to hand the CLI an argument no terminal would ever have typed.
MAX_TEXT = 2000

# `loop kill` tears a worktree down and `policy checkpoint` writes through the tracker, so this
# is minutes-scale work; unbounded, it would hold a server thread for the life of the process.
TIMEOUT_S = 300.0

# An identifier as the tracker spells one: issue ids, checkpoint names, decision ids (which
# carry a `#`) and confirm codes. The leading class is the security-relevant half - it admits
# no `-`, so no field can reach argparse as a flag.
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._#-]{0,63}\Z")

# What stands in for a spent confirm code. Not asterisks: a secret's length is a fact about it.
REDACTED = "<redacted>"

_CONFIRM = "confirm"


@dataclass(frozen=True)
class Field:
    """One input on an action's form: its name, its label, and where a value comes from.

    ``from_ask`` is the key of the ask this answers *about*, which the board prefills. A field
        without one is what only a person holds, and is never prefilled.
    """

    name: str
    label: str
    free: bool = False
    from_ask: str = ""
    # A record with no parent has no grant root; refusing would make an orphan unstartable.
    optional: bool = False


@dataclass(frozen=True)
class Action:
    """One entry of the closed table: what it is called, what it asks for, what it runs."""

    label: str
    fields: tuple[Field, ...]
    build: Callable[[Mapping[str, str]], tuple[str, ...]]
    confirmed: bool = False


@dataclass(frozen=True)
class Outcome:
    """The POST's reply: a status, and the plain text the result frame shows.

    A refusal is one of these rather than a type of its own: a refusal is not a different kind
    of thing from a reply, it is the reply, with a status that says no.
    """

    status: HTTPStatus
    text: str


def _refused(status: HTTPStatus, reason: str) -> Outcome:
    """The reply for a submission that will not be run."""
    return Outcome(status, f"refused: {reason}\n")


def asked(action: Action) -> list[Field]:
    """*action*'s fields, with the confirm code appended where the action needs one.

    Public, with :class:`Action` and :class:`Field`, because :mod:`basicly.board_asks` builds
    the prefilled form and must ask this module what an action wants. The alternative was that
    module spelling the field list a second time, which is how a form and the argv behind it
    drift apart.
    """
    asked = [*action.fields]
    if action.confirmed:
        asked.append(Field(_CONFIRM, "confirm code"))
    return asked


def _answer(form: Mapping[str, str]) -> tuple[str, ...]:
    """`loop answer`. `--` first: an answer may open with a dash and is not a flag."""
    return ("loop", "answer", "--", form["decision_id"], form["text"])


def _approve(form: Mapping[str, str]) -> tuple[str, ...]:
    """`policy checkpoint --approve`, carrying the code the operator typed and nothing else."""
    return (
        "policy",
        "checkpoint",
        form["issue"],
        form["name"],
        "--approve",
        "--confirm",
        form[_CONFIRM],
    )


def _park(form: Mapping[str, str]) -> tuple[str, ...]:
    """`update --status deferred`, and no reason travels with it.

    `tracker_argv`'s update table maps no flag to a reason - only `close --reason` does - so
    a board asking for one would drop what a person typed. `--notes` is the nearest field and
    it *replaces*, which loses whatever the record already holds. So the ledger records who
    parked it and when, and the why goes in a comment from a terminal (basicly-arxhshr).
    """
    return ("tracker", "write", "--", "update", form["issue"], "--status", "deferred")


def _start(form: Mapping[str, str]) -> tuple[str, ...]:
    """`loop run <id> --detach` - the one verb that starts a ready leaf and returns.

    Not `supervise`, which fans out over children a leaf has none of, nor a bare `run`, which is
        synchronous against a 300s :data:`TIMEOUT_S`. Type and root are carried because without
        them the child stopped at intake (basicly-fiow1sr, basicly-zq9i2m.6).
    """
    argv = ("loop", "run", form["issue"], "--detach")
    if form.get("work_type"):
        argv += ("--work-type", form["work_type"])
    if form.get("root"):
        argv += ("--root", form["root"])
    return argv


def _resume(form: Mapping[str, str]) -> tuple[str, ...]:
    """`in_progress`, never `open`.

    A write stating what the record's newest events already say is skipped as a replay, and
    a record parked from `open` still holds that `open` - so `--status open` would append
    nothing and exit 1. `in_progress` is in the ready set anyway (`differential.is_ready`),
    so the shorter path is also the correct one.
    """
    return ("tracker", "write", "--", "update", form["issue"], "--status", "in_progress")


def _kill(form: Mapping[str, str]) -> tuple[str, ...]:
    """`loop kill`. `--reason=` rather than two argv elements: the text may open with a dash."""
    return (
        "loop",
        "kill",
        form["issue"],
        f"--reason={form['reason']}",
        "--confirm",
        form[_CONFIRM],
    )


# The complete action table. Nothing else is clickable, and a producer cannot add an entry: a
# consumer has no mechanism to execute an action it does not already know. `test_board_actions`
# asserts the length as well as the contents - a sixth verb reaching the wall is the failure
# this table exists to make loud.
START_ACTION = "record-start"


def start_command(form: Mapping[str, str]) -> str:
    """The command a startable record's page prints, built by the action that runs it.

    One function, not one per caller: a second spelling is a second answer (basicly-fiow1sr).
    """
    return "basicly " + " ".join(ACTIONS[START_ACTION].build(form))


ACTIONS: dict[str, Action] = {
    "loop-answer": Action(
        label="Answer a queued decision",
        fields=(
            Field("decision_id", "decision id", from_ask="wait_id"),
            Field("text", "the answer", free=True),
        ),
        build=_answer,
    ),
    "checkpoint-approve": Action(
        label="Approve a checkpoint",
        fields=(
            Field("issue", "issue", from_ask="issue"),
            Field("name", "checkpoint", from_ask="subject"),
        ),
        build=_approve,
        confirmed=True,
    ),
    # No confirm code: parking spends nothing and is undone by its own opposite.
    "record-start": Action(
        label="start it",
        fields=(
            Field("issue", "record", from_ask="issue"),
            Field("work_type", "work type", from_ask="type", optional=True),
            Field("root", "grant root", from_ask="root", optional=True),
        ),
        build=_start,
    ),
    "record-park": Action(
        label="park it",
        fields=(Field("issue", "record", from_ask="issue"),),
        build=_park,
    ),
    "record-resume": Action(
        label="resume it",
        fields=(Field("issue", "record", from_ask="issue"),),
        build=_resume,
    ),
    "lane-kill": Action(
        label="Kill a lane",
        fields=(
            Field("issue", "lane", from_ask="issue"),
            Field("reason", "why", free=True),
        ),
        build=_kill,
        confirmed=True,
    ),
}

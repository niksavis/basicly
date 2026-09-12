from __future__ import annotations

import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

ROUTE = "/action"

MAX_TEXT = 2000

TIMEOUT_S = 300.0

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._#-]{0,63}\Z")

REDACTED = "<redacted>"

_CONFIRM = "confirm"


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    free: bool = False
    from_ask: str = ""
    optional: bool = False


@dataclass(frozen=True)
class Action:
    label: str
    fields: tuple[Field, ...]
    build: Callable[[Mapping[str, str]], tuple[str, ...]]
    confirmed: bool = False


@dataclass(frozen=True)
class Outcome:
    status: HTTPStatus
    text: str


def _refused(status: HTTPStatus, reason: str) -> Outcome:
    return Outcome(status, f"refused: {reason}\n")


def asked(action: Action) -> list[Field]:

    asked = [*action.fields]
    if action.confirmed:
        asked.append(Field(_CONFIRM, "confirm code"))
    return asked


def _answer(form: Mapping[str, str]) -> tuple[str, ...]:
    return ("loop", "answer", "--", form["decision_id"], form["text"])


def _approve(form: Mapping[str, str]) -> tuple[str, ...]:
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

    return ("tracker", "write", "--", "update", form["issue"], "--status", "deferred")


def _start(form: Mapping[str, str]) -> tuple[str, ...]:

    argv = ("loop", "run", form["issue"], "--detach")
    if form.get("work_type"):
        argv += ("--work-type", form["work_type"])
    if form.get("root"):
        argv += ("--root", form["root"])
    return argv


def _resume(form: Mapping[str, str]) -> tuple[str, ...]:

    return ("tracker", "write", "--", "update", form["issue"], "--status", "in_progress")


def _kill(form: Mapping[str, str]) -> tuple[str, ...]:
    return (
        "loop",
        "kill",
        form["issue"],
        f"--reason={form['reason']}",
        "--confirm",
        form[_CONFIRM],
    )


START_ACTION = "record-start"


def start_command(form: Mapping[str, str]) -> str:

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

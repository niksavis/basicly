"""The board's action surface: three `basicly` invocations, and no authority of its own.

**The board is a renderer with a keyboard.** Every action is an argv list handed to the
installed `basicly` CLI as a subprocess, taken from :data:`ACTIONS` - a closed table of three
entries. Nothing here writes a file, opens a tracker, or imports an engine module that can;
`.importlinter`'s `consumer-reads-only-the-snapshot` contract is the structural half of that
claim and the absence of a single read in this file is the other. The engine disposes, so an
action's outcome is whatever exit code the CLI gave it.

**Why the confirm code is a field a human fills, and never a value this module holds.**
`policy` mints a one-time code into `.basicly/usage/checkpoint-confirms.json` and hands a
non-interactive caller a challenge to relay to a human. The board *is* a non-interactive
caller. A board that read that file and offered a one-click approve would be relaying the code
to itself - satisfying the letter of the anti-autopilot gate and defeating the whole of its
purpose. So :meth:`ActionSurface.panel` draws an input with no value attribute, a human types
the code they got from a terminal, and this module never learns where it came from. That is
deliberately more friction than a button, and the same friction a terminal operator pays.

**The code is then redacted out of the audit line and the reply**, which is a second property
and the one a passing test would not notice: the challenge `basicly` prints carries the code,
so echoing an invocation verbatim would leave a live credential on the wall's own screen and in
the server's stdout for whoever walks past next.

**Untrusted input crosses one boundary and it is here**: every field arrives over HTTP from a
screen anyone in the room can touch. Each guard is stated beside the code that applies it.
"""

from __future__ import annotations

import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from html import escape
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from . import ui

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from http.server import BaseHTTPRequestHandler

# The one route that takes a POST. A board with actions disabled never registers it, which is
# what makes a read-only board a structural refusal rather than a check someone can miss.
ROUTE = "/action"

# The frame a reply lands in, so a submission never navigates the wall away from the board. A
# display with a back button is a display someone has left on the wrong page.
RESULT_FRAME = "board-action-result"

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
class _Field:
    """One input on an action's form: its form name, its label, whether it is free text."""

    name: str
    label: str
    free: bool = False


@dataclass(frozen=True)
class _Action:
    """One entry of the closed table: what it is called, what it asks for, what it runs."""

    label: str
    fields: tuple[_Field, ...]
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


def _asked(action: _Action) -> list[_Field]:
    """*action*'s fields, with the confirm code appended where the action needs one."""
    asked = [*action.fields]
    if action.confirmed:
        asked.append(_Field(_CONFIRM, "confirm code"))
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
# asserts the length as well as the contents - a fourth verb reaching the wall is the failure
# this table exists to make loud.
ACTIONS: dict[str, _Action] = {
    "loop-answer": _Action(
        label="Answer a queued decision",
        fields=(_Field("decision_id", "decision id"), _Field("text", "the answer", free=True)),
        build=_answer,
    ),
    "checkpoint-approve": _Action(
        label="Approve a checkpoint",
        fields=(_Field("issue", "issue"), _Field("name", "checkpoint")),
        build=_approve,
        confirmed=True,
    ),
    "lane-kill": _Action(
        label="Kill a lane",
        fields=(_Field("issue", "lane"), _Field("reason", "why", free=True)),
        build=_kill,
        confirmed=True,
    ),
}


def executable() -> str | None:
    """The `basicly` console script to invoke, or None where this machine has none.

    This process's own venv first, so a board served by `uv run basicly` drives the checkout it
    is displaying. `shutil.which` applies `PATHEXT`, so the Windows shim resolves here too.
    """
    return shutil.which("basicly", path=str(Path(sys.executable).parent)) or shutil.which("basicly")


def redacted(argv: tuple[str, ...]) -> tuple[str, ...]:
    """*argv* with the value after `--confirm` replaced, for the audit line and the reply."""
    out = list(argv)
    for index, element in enumerate(out[:-1]):
        if element == "--confirm":
            out[index + 1] = REDACTED
    return tuple(out)


def _spawn(argv: tuple[str, ...], cwd: Path) -> tuple[int, str]:
    """Run *argv* in *cwd*; its exit code and its merged output.

    Injected rather than reached for, so a spy needs no module-attribute patch - that is
    global, and would let one test's spy answer another's real call.
    """
    # The alternative rejected: calling the engine function in-process, which would give the
    # board the authority C8 denies it and need the import `.importlinter` forbids.
    completed = subprocess.run(  # noqa: S603 - which()-resolved head, validated list argv, no shell
        argv, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def _validated(action: _Action, form: Mapping[str, list[str]]) -> dict[str, str] | Outcome:
    """*action*'s fields read out of a posted form, or the first reason to refuse them."""
    values: dict[str, str] = {}
    for field in _asked(action):
        raw = (form.get(field.name) or [""])[0].strip()
        if not raw:
            return _refused(HTTPStatus.BAD_REQUEST, f"{field.label} is empty; nothing was run")
        if field.free and len(raw) > MAX_TEXT:
            return _refused(HTTPStatus.BAD_REQUEST, f"{field.label} is over {MAX_TEXT} long")
        if not field.free and not _ID.fullmatch(raw):
            return _refused(HTTPStatus.BAD_REQUEST, f"{field.label} is not an identifier")
        values[field.name] = raw
    return values


def origins(port: int) -> frozenset[str]:
    """This server's own origins, both loopback spellings - off the port, not off `Host`."""
    return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})


class ActionSurface:
    """The registered action endpoint: its per-process token, its panel, its invocations.

    Nothing counts them: the two echoes are the record, so a counter beside them would be a
    second source of truth for the same fact and no consumer reads it.

    The token lives only here and in the page this process served, so a page a browser kept
    from an earlier run cannot drive this one. It authorises nothing and substitutes for no
    confirm code - it says only that the submission came from the page this server drew.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        run: Callable[[tuple[str, ...], Path], tuple[int, str]] = _spawn,
        echo: Callable[[str], None] = ui.say,
    ) -> None:
        """Hold *repo_root* and mint this process's token; no I/O and no spawn until a POST."""
        self.repo_root = repo_root
        self.token = secrets.token_urlsafe(16)
        self._run = run
        self._echo = echo

    def panel(self) -> str:
        """One form per table entry, every field empty, and a frame for the replies.

        Every interpolated string is an :data:`ACTIONS` literal or this process's token, so
        nothing untrusted reaches the markup; the escaping guards a later non-literal label.
        """
        forms = "".join(self._form(name, action) for name, action in ACTIONS.items())
        return (
            '<section class="board-actions" id="board-actions">'
            "<h2>Act</h2>"
            "<p>Every button here runs the <code>basicly</code> CLI and nothing else. "
            "A checkpoint or a kill needs a one-time code a human types: the board never "
            "reads it, so get it from a terminal and fill the empty field.</p>"
            f"{forms}"
            f'<iframe name="{RESULT_FRAME}" title="what the last action printed"></iframe>'
            "</section>"
        )

    def _form(self, name: str, action: _Action) -> str:
        """*action*'s form: hidden token, hidden name, an empty input per field, one button."""
        inputs = "".join(
            f"<label>{escape(field.label)} "
            f'<input name="{escape(field.name)}" autocomplete="off" required></label>'
            for field in _asked(action)
        )
        return (
            f'<form method="post" action="{ROUTE}" target="{RESULT_FRAME}">'
            f'<input type="hidden" name="token" value="{escape(self.token)}">'
            f'<input type="hidden" name="action" value="{escape(name)}">'
            f"{inputs}"
            f'<button type="submit">{escape(action.label)}</button>'
            "</form>"
        )

    def plan(self, origin: str | None, port: int, body: bytes) -> tuple[str, ...] | Outcome:
        """The argv this submission would run, or the reason it will not run at all.

        Origin and token first, and both refuse having read nothing else: a submission from a
        page this server did not draw is not a malformed action, it is not this server's action.
        """
        if origin not in origins(port):
            return _refused(HTTPStatus.FORBIDDEN, "that submission is not from this board")
        form = parse_qs(body.decode("utf-8", errors="replace"))
        if not secrets.compare_digest((form.get("token") or [""])[0], self.token):
            return _refused(HTTPStatus.FORBIDDEN, "stale board page; reload it and retry")
        name = (form.get("action") or [""])[0]
        action = ACTIONS.get(name)
        if action is None:
            return _refused(HTTPStatus.BAD_REQUEST, f"no action named {name!r}")
        values = _validated(action, form)
        if isinstance(values, Outcome):
            return values
        head = executable()
        if head is None:
            return _refused(HTTPStatus.SERVICE_UNAVAILABLE, "no `basicly` executable here")
        return (head, *action.build(values))

    def respond(self, *, origin: str | None, port: int, body: bytes) -> Outcome:
        """Refuse the submission, or run it and report what the CLI said.

        The two echoes are the point: the terminal that started the board is then a complete
        audit log of what the wall did, which is the only record a display leaves behind.
        """
        planned = self.plan(origin, port, body)
        if isinstance(planned, Outcome):
            return planned
        shown = " ".join(redacted(planned))
        self._echo(f"board: action   running {shown}")
        code, output = self._run(planned, self.repo_root)
        self._echo(f"board: action   exit {code} from {shown}")
        return Outcome(HTTPStatus.OK, f"$ {shown}\nexit {code}\n\n{output}\n")


def inject(page: str, surface: ActionSurface | None) -> str:
    """*page* with *surface*'s panel before `</body>`, or *page* untouched where none is given.

    At this tier rather than in the template: Mode A's page is a file with no server behind it,
    so a form drawn into it would post into nothing.
    """
    if surface is None:
        return page
    return page.replace("</body>", surface.panel() + "</body>", 1)


def transcript(surface: ActionSurface | None) -> str:
    """The start-up line naming which actions this board will run, if any."""
    if surface is None:
        return "board: actions   none - this board answers GET only (--no-actions)"
    return f"board: actions   {', '.join(ACTIONS)} - a confirm code is typed, never read"


def handle_post(handler: BaseHTTPRequestHandler, surface: ActionSurface | None) -> None:
    """Answer a POST: the action route where a surface is registered, 405 everywhere else.

    405 rather than 501 or 404: 501 reads as "not implemented yet", and a 404 would invite a
    client to keep looking. `Allow: GET` is the true answer both when actions were never
    registered and when a POST went to the page. The reply is `text/plain`, so a CLI's stdout
    can never be read back as markup.
    """
    if surface is None or urlsplit(handler.path).path != ROUTE:
        handler.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        handler.send_header("Allow", "GET")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    length = int(handler.headers.get("Content-Length") or 0)
    outcome = surface.respond(
        origin=handler.headers.get("Origin"),
        port=int(handler.server.server_address[1]),  # type: ignore[index]
        body=handler.rfile.read(length),
    )
    encoded = outcome.text.encode("utf-8")
    handler.send_response(outcome.status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)

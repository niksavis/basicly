"""The endpoint that runs a board action: its token, its spawn, its POST, its audit line.

Split from `board_actions` when that module crossed the size cap. The seam is the one it
already had: that module answers *what verbs exist and what argv each builds*, this one
answers *how one is run and replied to*. Three modules read the table and never serve
anything, and none of them wants a subprocess or an HTTP handler on the import path.

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

import secrets
import shutil
import subprocess
import sys
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from . import ui
from .board_actions import (
    _ID,
    ACTIONS,
    MAX_TEXT,
    REDACTED,
    ROUTE,
    TIMEOUT_S,
    Action,
    Outcome,
    _refused,
    asked,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from http.server import BaseHTTPRequestHandler


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


def _validated(action: Action, form: Mapping[str, list[str]]) -> dict[str, str] | Outcome:
    """*action*'s fields read out of a posted form, or the first reason to refuse them."""
    values: dict[str, str] = {}
    for field in asked(action):
        raw = (form.get(field.name) or [""])[0].strip()
        if not raw and field.optional:
            continue
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

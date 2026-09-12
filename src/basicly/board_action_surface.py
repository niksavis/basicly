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

    return shutil.which("basicly", path=str(Path(sys.executable).parent)) or shutil.which("basicly")


def redacted(argv: tuple[str, ...]) -> tuple[str, ...]:
    out = list(argv)
    for index, element in enumerate(out[:-1]):
        if element == "--confirm":
            out[index + 1] = REDACTED
    return tuple(out)


def _spawn(argv: tuple[str, ...], cwd: Path) -> tuple[int, str]:

    completed = subprocess.run(  # noqa: S603 - which()-resolved head, validated list argv, no shell
        argv, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def _validated(action: Action, form: Mapping[str, list[str]]) -> dict[str, str] | Outcome:
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
    return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})


class ActionSurface:
    def __init__(
        self,
        repo_root: Path,
        *,
        run: Callable[[tuple[str, ...], Path], tuple[int, str]] = _spawn,
        echo: Callable[[str], None] = ui.say,
    ) -> None:
        self.repo_root = repo_root
        self.token = secrets.token_urlsafe(16)
        self._run = run
        self._echo = echo

    def plan(self, origin: str | None, port: int, body: bytes) -> tuple[str, ...] | Outcome:

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

        planned = self.plan(origin, port, body)
        if isinstance(planned, Outcome):
            return planned
        shown = " ".join(redacted(planned))
        self._echo(f"board: action   running {shown}")
        code, output = self._run(planned, self.repo_root)
        self._echo(f"board: action   exit {code} from {shown}")
        return Outcome(HTTPStatus.OK, f"$ {shown}\nexit {code}\n\n{output}\n")


def transcript(surface: ActionSurface | None) -> str:
    if surface is None:
        return "board: actions   none - this board answers GET only (--no-actions)"
    return f"board: actions   {', '.join(ACTIONS)} - a confirm code is typed, never read"


def handle_post(handler: BaseHTTPRequestHandler, surface: ActionSurface | None) -> None:

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

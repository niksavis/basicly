# module-size-waiver: cohesion: 3985 -> 4629 of 4000, headroom was already 15.

from __future__ import annotations

import contextlib
import ipaddress
import json
import socket
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote, urlsplit

from . import (
    board_action_surface,
    board_actions,
    board_asks,
    board_assets,
    board_backlog,
    board_bodies,
    board_kanban,
    board_record,
    board_render,
    board_schema,
    board_snapshot,
    board_wall,
    catalog,
    supervise,
    ui,
)

if TYPE_CHECKING:
    from collections.abc import Callable
from pathlib import Path

HOST = "127.0.0.1"


def admitted_host(value: str) -> str:

    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError(f"{value!r} is not an IP literal; a resolvable name is refused") from None
    if parsed.version != 4:
        raise ValueError(f"{value!r} is IPv6; this listener binds IPv4 literals only")
    if parsed.is_unspecified:
        raise ValueError(f"{value!r} binds every interface; name the one you mean")
    return str(parsed)


DEFAULT_PORT = 8787

DEFAULT_REFRESH_S = supervise.HEARTBEAT_INTERVAL_S

SNAPSHOT_ROUTE = "/snapshot.json"
PAGE_ROUTES = ("/", "/index.html")

KANBAN_ROUTES = ("/loop", "/loop.html")

BACKLOG_ROUTES = ("/backlog", "/backlog.html")

RECORD_ROUTE = f"/{board_record.HREF_DIR}/"

NO_RECORD = "no such record in the snapshot this board holds"

STOPPED = "board: stopped. {refreshes} refreshes, {failures} failed. No state was written."

SELF_AGE = "producer age {age:.0f}s - this process loaded its code at {loaded}"
STALE_TEMPLATE_FAULT = (
    "fault: the template changed {age:.0f}s after this process loaded its code - a blank "
    "region below may be the stale answerer, not an absence. Restart the board."
)
DROPPED_ROWS_FAULT = (
    "fault: the document holds ready rows this process computed, but none reached this "
    "page. Restart the board."
)

NOTES_SLOT = '<div class="notes"></div>'


def newest_template_mtime(templates_dir: Path) -> float | None:

    try:
        stamps = [path.stat().st_mtime for path in templates_dir.glob("*.j2")]
    except OSError:
        return None
    return max(stamps, default=None)


def _template_mtime() -> float | None:
    return newest_template_mtime(catalog.bundled_catalog_root() / board_render.TEMPLATE_DIR)


def _rows_dropped(ready: object, drawn: str) -> bool:
    idents = [
        ident
        for row in getattr(ready, "rows", ())
        if isinstance(ident := getattr(row, "ident", None), str)
    ]
    return bool(idents) and not any(ident in drawn for ident in idents)


def _banner(notes: list[tuple[str, str]]) -> str:

    lines = "".join(
        f'<p class="note{f" state-{state}" if state else ""}">{note}</p>' for note, state in notes
    )
    faulted = " fault" if any(state for _, state in notes) else ""
    return f'<div class="notes{faulted}">{lines}</div>'


def session_facts(repo_root: Path) -> board_snapshot.SessionFacts | None:

    held = supervise.read_holder(repo_root)
    if held is None or not held.root_issue:
        return None
    stale = held.age_s > supervise.STALE_AFTER_S
    return board_snapshot.SessionFacts(
        root_issue=held.root_issue,
        supervised=not stale,
        session_id=held.session_id or "",
        age_s=held.age_s,
        stale=stale,
    )


def live_holder(repo_root: Path) -> supervise.LockInfo | None:

    held = supervise.read_holder(repo_root)
    if held is None or held.age_s >= supervise.STALE_AFTER_S:
        return None
    return held


class Board:
    def __init__(
        self,
        repo_root: Path,
        *,
        refresh_s: float = DEFAULT_REFRESH_S,
        build: Callable[[], dict[str, object]] | None = None,
        actions: board_action_surface.ActionSurface | None = None,
        template_mtime: Callable[[], float | None] = _template_mtime,
    ) -> None:

        self.repo_root = repo_root
        self.refresh_s = refresh_s
        self._build = build
        self.actions = actions
        self.refreshes = 0
        self.failures = 0
        self._served: bytes | None = None
        self._folding = threading.Lock()
        self._started_at = datetime.now(UTC)
        self._template_mtime = template_mtime

    @property
    def snapshot_path(self) -> Path:
        return self.repo_root / board_snapshot.SNAPSHOT_FILE

    def _fold(self) -> dict[str, object]:

        freshness = board_snapshot.Freshness(
            source=board_snapshot.SELF_REFRESH,
            cadence_s=self.refresh_s,
            stale_after_s=supervise.STALE_AFTER_S,
        )
        if self._build is None:
            facts = board_snapshot.Facts(session=session_facts(self.repo_root))
            return board_snapshot.build_document(self.repo_root, facts=facts, freshness=freshness)
        document = self._build()
        document["freshness"] = {
            "source": freshness.source,
            "cadence_s": freshness.cadence_s,
            "stale_after_s": freshness.stale_after_s,
        }
        return document

    def refresh(self) -> bool:

        if live_holder(self.repo_root) is not None:
            return False
        if not self._folding.acquire(blocking=False):
            return False
        try:
            document = self._fold()
        except Exception:  # noqa: BLE001 — a display must outlive one bad fold
            self.failures += 1
            return False
        else:
            self._served = board_snapshot.serialize(document).encode("utf-8")
            self.refreshes += 1
            return True
        finally:
            self._folding.release()

    def payload(self) -> bytes | None:

        if live_holder(self.repo_root) is None:
            return self._served
        try:
            return self.snapshot_path.read_bytes()
        except OSError:
            return None

    def _readable(self) -> tuple[dict, board_schema.SnapshotVerdict] | None:

        payload = self.payload()
        if payload is None:
            return None
        try:
            document = json.loads(payload)
        except json.JSONDecodeError:
            return None
        verdict = board_schema.verdict(self.repo_root, document)
        return (document, verdict) if verdict.readable else None

    def record(self, record_id: str, now: datetime) -> bytes | None:

        held = self._readable()
        if held is None:
            return None
        filled = board_record.context(
            held[0],
            held[1],
            record_id,
            now,
            page=board_record.PageFacts(
                back="/",
                start_command=board_actions.start_command(
                    board_record.start_form(held[0], record_id)
                ),
                body=board_bodies.bodies(self.repo_root).get(record_id, ""),
            ),
        )
        return None if filled is None else board_render.render_record(filled).encode("utf-8")

    def backlog(self, now: datetime) -> bytes | None:
        held = self._readable()
        if held is None:
            return None
        filled = board_backlog.context(held[0], held[1], now, back="/")
        return board_render.render_backlog(filled).encode("utf-8")

    def kanban(self, now: datetime) -> bytes | None:

        held = self._readable()
        if held is None:
            return None
        filled = board_kanban.context(held[0], held[1], now, back="/")
        return board_render.render_kanban(filled).encode("utf-8")

    def page(self, now: datetime) -> bytes | None:
        held = self._readable()
        if held is None:
            return None
        document, verdict = held
        token = self.actions.token if self.actions is not None else None
        rows, dropped = board_asks.pending(document.get("asks"), token)
        filled = board_render.context(
            document,
            verdict,
            now,
            acts=(
                rows,
                dropped,
                board_asks.killable(document.get("lanes"), token),
                board_asks.parking(document.get("units"), token),
                board_asks.starting(document, token),
            ),
        )
        drawn = board_render.render(filled)
        return self._name_self_faults(drawn, filled.get("ready"), now).encode("utf-8")

    def _name_self_faults(self, drawn: str, ready: object, now: datetime) -> str:

        started = self._started_at.timestamp()
        age_s = now.timestamp() - started
        notes: list[tuple[str, str]] = []
        mtime = self._template_mtime()
        if mtime is not None and mtime > started:
            notes.append((
                STALE_TEMPLATE_FAULT.format(age=now.timestamp() - started),
                board_wall.STALE,
            ))
        if notes:
            notes.append((
                SELF_AGE.format(age=age_s, loaded=self._started_at.isoformat(timespec="seconds")),
                "",
            ))
        if _rows_dropped(ready, drawn):
            notes.append((DROPPED_ROWS_FAULT, board_wall.FAIL))
        return (
            drawn.replace(NOTES_SLOT, _banner(notes), 1)
            if NOTES_SLOT in drawn
            else drawn.replace("</body>", _banner(notes) + "</body>", 1)
        )

    def producer(self) -> str:
        held = live_holder(self.repo_root)
        if held is None:
            return (
                f"board: producer  self-refresh every {self.refresh_s:.0f}s "
                "(no supervisor lock held on this repo)"
            )
        return (
            f"board: producer  supervisor {held.session_id or 'unnamed'} (pid {held.pid}), "
            f"heartbeat {held.age_s:.0f}s old, "
            f"writing every {supervise.HEARTBEAT_INTERVAL_S:.0f}s"
        )


class _Handler(BaseHTTPRequestHandler):
    @property
    def board(self) -> Board:
        return cast("_Server", self.server).board

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — the base's name
        pass

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == SNAPSHOT_ROUTE:
            self._send(self.board.payload(), "application/json")
        elif route in PAGE_ROUTES:
            self._send(self.board.page(datetime.now(UTC)), "text/html; charset=utf-8", reload=True)
        elif route in BACKLOG_ROUTES:
            self._send(
                self.board.backlog(datetime.now(UTC)), "text/html; charset=utf-8", reload=True
            )
        elif route in KANBAN_ROUTES:
            self._send(
                self.board.kanban(datetime.now(UTC)), "text/html; charset=utf-8", reload=True
            )
        elif route.startswith(RECORD_ROUTE):
            self._record(route)
        elif route.startswith(board_assets.ROUTE):
            self._asset(route)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _record(self, route: str) -> None:

        ident = unquote(route[len(RECORD_ROUTE) :]).removesuffix(board_record.HREF_SUFFIX)
        body = self.board.record(ident, datetime.now(UTC))
        if body is None:
            self.send_error(HTTPStatus.NOT_FOUND, NO_RECORD)
            return
        self._send(body, "text/html; charset=utf-8", reload=True)

    def _asset(self, route: str) -> None:

        held = board_assets.read(board_render.root(), unquote(route[len(board_assets.ROUTE) :]))
        if held is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body, content_type = held
        self._send(body, content_type)

    def do_POST(self) -> None:
        board_action_surface.handle_post(self, self.board.actions)

    def _send(self, body: bytes | None, content_type: str, *, reload: bool = False) -> None:

        if body is None:
            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "no board snapshot yet")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if reload:
            self.send_header("Refresh", f"{self.board.refresh_s:.0f}")
        self.end_headers()
        self.wfile.write(body)


EXCLUSIVE_BIND_OPTION: int | None = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)


class _Server(ThreadingHTTPServer):
    allow_reuse_address = EXCLUSIVE_BIND_OPTION is None

    def __init__(self, address: tuple[str, int], board: Board) -> None:
        self.board = board
        super().__init__(address, _Handler)

    def server_bind(self) -> None:
        if EXCLUSIVE_BIND_OPTION is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, EXCLUSIVE_BIND_OPTION, 1)
        super().server_bind()


@dataclass(frozen=True)
class Listener:
    _httpd: _Server
    board: Board

    @property
    def host(self) -> str:
        return str(self._httpd.server_address[0])

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def run(self) -> None:
        self._httpd.serve_forever()

    def stop(self) -> None:
        self._httpd.shutdown()

    def close(self) -> None:
        self._httpd.server_close()


def bind(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    *,
    port: int = DEFAULT_PORT,
    refresh_s: float = DEFAULT_REFRESH_S,
    build: Callable[[], dict[str, object]] | None = None,
    actions: bool = True,
    host: str = HOST,
) -> Listener:

    surface = board_action_surface.ActionSurface(repo_root) if actions else None
    board = Board(repo_root, refresh_s=refresh_s, build=build, actions=surface)
    board.refresh()
    return Listener(_httpd=_Server((admitted_host(host), port), board), board=board)


def _tick(board: Board, stop: threading.Event) -> None:
    while not stop.wait(board.refresh_s):
        board.refresh()


def serve(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    *,
    port: int = DEFAULT_PORT,
    refresh_s: float = DEFAULT_REFRESH_S,
    build: Callable[[], dict[str, object]] | None = None,
    actions: bool = True,
    host: str = HOST,
) -> int:

    try:
        host = admitted_host(host)
    except ValueError as exc:
        ui.warn(f"board: refusing to bind - {exc}")
        return 2
    try:
        listener = bind(
            repo_root, port=port, refresh_s=refresh_s, build=build, actions=actions, host=host
        )
    except OSError as exc:
        ui.warn(f"board: cannot listen on {host}:{port} - {exc}")
        return 1
    ui.say(
        f"board: serving {board_schema.VERSION} on {listener.url}  ({host} only; Ctrl-C to stop)"
    )
    if not ipaddress.ip_address(host).is_loopback:
        reach = "code-gated actions" if actions else "no action route"
        ui.say(f"board: {host} is reachable beyond this machine - {reach}")
    ui.say(listener.board.producer())
    ui.say(board_action_surface.transcript(listener.board.actions))
    ui.say("board: press Ctrl-C to stop. This process holds no lock and blocks no gate.")
    stop = threading.Event()
    threading.Thread(target=_tick, args=(listener.board, stop), daemon=True).start()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            listener.run()
    finally:
        stop.set()
        listener.close()
    ui.say(STOPPED.format(refreshes=listener.board.refreshes, failures=listener.board.failures))
    return 0

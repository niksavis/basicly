"""Serve the board on the loopback for a wall display: Mode B (basicly-rn0o.5, basicly-rn0o.6).

Loopback-only, writes nothing, and one POST route that `--no-actions` removes. Each is
asserted in `tests/test_board_serve.py`, not described here. Who produces is decided per tick:
a fresh supervisor lock means serve its bytes, no lock means fold in memory. The lock is read
at this tier and passed down, which `.importlinter` enforces (C11).
"""

# module-size-waiver: cohesion: 3985 -> 4629 of 4000, headroom was already 15.
# The self-staleness check (`_template_mtime`, `_rows_dropped`, `_name_self_faults`) reads one
# `Board` instance's own `_started_at` and the document, verdict and drawn page `page()`
# already holds this tick - a free function taking those as arguments is the same code behind
# an import, not less coupling, so it fails the gate's own "not into `_part1`/`_part2`" rule
# rather than satisfying it. Docstrings were cut first: three paragraphs to one sentence each.

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
from urllib.parse import urlsplit

from . import board_actions, board_render, board_schema, board_snapshot, catalog, supervise, ui

if TYPE_CHECKING:
    from collections.abc import Callable
from pathlib import Path

# C10, and the security boundary here: the loopback by default, and only ever a literal
# IPv4 address the operator chose (basicly-bxk5g8, for touch walls) — never `0.0.0.0`,
# never a name a resolver is free to point off this box. `admitted_host` is the rule.
HOST = "127.0.0.1"


def admitted_host(value: str) -> str:
    """The bind address, admitted; raises ValueError naming the refused rule.

    A wildcard exposes interfaces the operator never saw, a name resolves wherever a
    resolver says, and IPv6 would need its own address family here.
    """
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError(f"{value!r} is not an IP literal; a resolvable name is refused") from None
    if parsed.version != 4:
        raise ValueError(f"{value!r} is IPv6; this listener binds IPv4 literals only")
    if parsed.is_unspecified:
        raise ValueError(f"{value!r} binds every interface; name the one you mean")
    return str(parsed)


# The transcript's port, fixed so a wall display's bookmark survives a restart. `--port 0`
# takes an ephemeral one, which is what a test and a second board on one machine use.
DEFAULT_PORT = 8787

# `supervise.HEARTBEAT_INTERVAL_S`, so an unsupervised board ticks at the cadence a supervised
# one would: a viewer cannot be fresher than the producer it is standing in for.
DEFAULT_REFRESH_S = supervise.HEARTBEAT_INTERVAL_S

SNAPSHOT_ROUTE = "/snapshot.json"
PAGE_ROUTES = ("/", "/index.html")

STOPPED = "board: stopped. {refreshes} refreshes, {failures} failed. No state was written."

# basicly-mcf2uh: a long-lived process re-reads its template every render but keeps whatever
# model it imported at start, so the two drift apart in silence (`f7788bb7`).
SELF_AGE = "producer age {age:.0f}s - this process loaded its code at {loaded}"
STALE_TEMPLATE_FAULT = (
    "fault: the template changed {age:.0f}s after this process loaded its code - a blank "
    "region below may be the stale answerer, not an absence. Restart the board."
)
DROPPED_ROWS_FAULT = (
    "fault: the document holds ready rows this process computed, but none reached this "
    "page. Restart the board."
)


def _template_mtime() -> float | None:
    """The board page template's mtime, or None where it is unreadable (neither is staleness)."""
    try:
        path = catalog.bundled_catalog_root() / board_render.TEMPLATE_DIR / board_render.TEMPLATE
        return path.stat().st_mtime
    except OSError:
        return None


def _rows_dropped(ready: object, drawn: str) -> bool:
    """True where *ready* holds rows by `ident`, but none of them reached *drawn*."""
    idents = [
        ident
        for row in getattr(ready, "rows", ())
        if isinstance(ident := getattr(row, "ident", None), str)
    ]
    return bool(idents) and not any(ident in drawn for ident in idents)


def session_facts(repo_root: Path) -> board_snapshot.SessionFacts | None:
    """The supervisor lock's facts, or None where no lock names a root.

    None rather than a guessed root: the `session` section is then omitted and its panel says
    the producer did not emit it, which is true. A root invented here would be a claim about
    which pass is running, drawn on a wall.
    """
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
    """The lock holder whose heartbeat is younger than `supervise.STALE_AFTER_S`, if any.

    The one question that decides who produces, asked per tick and per request. A stale lock is
    a crashed supervisor, which is precisely when a viewer has to fold for itself instead of
    serving a file nobody is rewriting.
    """
    held = supervise.read_holder(repo_root)
    if held is None or held.age_s >= supervise.STALE_AFTER_S:
        return None
    return held


class Board:
    """What the routes answer from, and the only mutable state this process keeps.

    None of it outlives the process: the served document is a field, not a file. A fold is
    guarded by a non-blocking lock, so a tick that arrives while the previous one is still
    running is dropped rather than queued - one refresh in flight, and a slow fold on a large
    ledger cannot stack up behind itself.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        refresh_s: float = DEFAULT_REFRESH_S,
        build: Callable[[], dict[str, object]] | None = None,
        actions: board_actions.ActionSurface | None = None,
        template_mtime: Callable[[], float | None] = _template_mtime,
    ) -> None:
        """Hold *repo_root*, the cadence, how to build and what may be acted on.

        *actions* is None for a read-only board: no surface, no POST route, no panel.
        `build` comes from above because `board_facts` outranks this tier; folding from
        the lock facts alone served 0 phases of 232 (basicly-sp8lce). *template_mtime* is
        injectable so a test can name staleness without touching the real shared file.
        """
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
        """Where a supervisor lands its snapshot: the file this process reads and never writes."""
        return self.repo_root / board_snapshot.SNAPSHOT_FILE

    def _fold(self) -> dict[str, object]:
        """One document, through the caller's builder where it gave one.

        The freshness is this module's either way: a self-refresh is what *this* process
        does, and a builder written for Mode A cannot know the cadence it is served at.
        """
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
        """Fold one document into memory unless a live supervisor owns the tick; did it fold.

        A failure is counted and swallowed on purpose: a stale screen with a growing age
        beats the dark one that exiting would leave, which is what the STALE band is for.
        """
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
        """The bytes ``GET /snapshot.json`` answers with, or None while there is no document.

        The supervisor's file byte for byte when one is live, because the contract says the
        served bytes *are* the file's; this process's own fold otherwise, serialised by the
        same function that would have written it.
        """
        if live_holder(self.repo_root) is None:
            return self._served
        try:
            return self.snapshot_path.read_bytes()
        except OSError:
            return None

    def page(self, now: datetime) -> bytes | None:
        """The board as one HTML page, or None where no readable document is available.

        A document the contract refuses is not drawn, for Mode A's reason.
        """
        payload = self.payload()
        if payload is None:
            return None
        try:
            document = json.loads(payload)
        except json.JSONDecodeError:
            return None
        verdict = board_schema.verdict(self.repo_root, document)
        if not verdict.readable:
            return None
        drawn = board_render.page(document, verdict, now=now)
        ready = board_render.context(document, verdict, now).get("ready")
        drawn = self._name_self_faults(drawn, ready, now)
        return board_actions.inject(drawn, self.actions).encode("utf-8")

    def _name_self_faults(self, drawn: str, ready: object, now: datetime) -> str:
        """*drawn* with this process's own age, and any self-staleness fault, before `</body>`.

        Neither `board_schema.verdict` nor `board_render` can see whether this process is
        the code the on-disk template was built for; that is this tier's own to report.
        """
        started = self._started_at.timestamp()
        age_s = now.timestamp() - started
        notes = [SELF_AGE.format(age=age_s, loaded=self._started_at.isoformat(timespec="seconds"))]
        mtime = self._template_mtime()
        if mtime is not None and mtime > started:
            notes.append(STALE_TEMPLATE_FAULT.format(age=now.timestamp() - started))
        if _rows_dropped(ready, drawn):
            notes.append(DROPPED_ROWS_FAULT)
        banner = "".join(f'<p class="note">{note}</p>' for note in notes)
        return drawn.replace("</body>", banner + "</body>", 1)

    def producer(self) -> str:
        """The transcript's `producer` line: which process writes the document being served."""
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
    """Two GET routes, and the action surface's POST where a board registered one."""

    @property
    def board(self) -> Board:
        """The state this handler answers from; the server it belongs to carries it."""
        return cast("_Server", self.server).board

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — the base's name
        """Swallow the per-request access log; a wall console is not a log sink."""

    def do_GET(self) -> None:
        """The page at the roots, the contract at :data:`SNAPSHOT_ROUTE`, 404 anywhere else."""
        route = urlsplit(self.path).path
        if route == SNAPSHOT_ROUTE:
            self._send(self.board.payload(), "application/json")
        elif route in PAGE_ROUTES:
            self._send(self.board.page(datetime.now(UTC)), "text/html; charset=utf-8", reload=True)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """Whatever the action surface answers, or 405 where this board registered none."""
        board_actions.handle_post(self, self.board.actions)

    def _send(self, body: bytes | None, content_type: str, *, reload: bool = False) -> None:
        """One response, or 503 while the producer has not landed a document yet.

        503 rather than 404 because the route exists and will answer; it is the document that
        does not exist. *reload* sets the `Refresh` header, which is how the page re-fetches on
        the cadence with no script in it, and which Mode A's artifact on disk cannot claim.
        """
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


# Windows only. `SO_REUSEADDR`, which `http.server` sets, "allows a socket to forcibly bind to
# a port in use by another socket" there, so a taken port never errors [S learn.microsoft.com,
# Using SO_REUSEADDR and SO_EXCLUSIVEADDRUSE]. Named, not `sys.platform`, so a test injects it.
EXCLUSIVE_BIND_OPTION: int | None = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)


class _Server(ThreadingHTTPServer):
    """A threaded loopback listener carrying the :class:`Board` its handlers read.

    Threaded because one browser holding a page open must not make the snapshot route wait.
    """

    allow_reuse_address = EXCLUSIVE_BIND_OPTION is None

    def __init__(self, address: tuple[str, int], board: Board) -> None:
        self.board = board
        super().__init__(address, _Handler)

    def server_bind(self) -> None:
        """Bind, claiming the port exclusively wherever the platform offers that."""
        if EXCLUSIVE_BIND_OPTION is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, EXCLUSIVE_BIND_OPTION, 1)
        super().server_bind()


@dataclass(frozen=True)
class Listener:
    """A bound but not-yet-serving board, so a caller can read the port it actually got.

    The socket is private: `stop` belongs to another thread than `run`, and `close` is
    the only call safe after an interrupt, so the order lives here and not in callers.
    """

    _httpd: _Server
    board: Board

    @property
    def host(self) -> str:
        """The address the socket is bound to. A test asserts this, because C10 is one line."""
        return str(self._httpd.server_address[0])

    @property
    def port(self) -> int:
        """The bound port, which is the assigned one wherever `--port 0` was asked for."""
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        """The URL the start line prints, and the only origin the board is reachable at."""
        return f"http://{self.host}:{self.port}"

    def run(self) -> None:
        """Answer requests until :meth:`stop`, or until the caller is interrupted."""
        self._httpd.serve_forever()

    def stop(self) -> None:
        """Ask a running :meth:`run` to return. Must be called from another thread."""
        self._httpd.shutdown()

    def close(self) -> None:
        """Release the socket. Safe whether or not :meth:`run` was ever entered."""
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
    """Bind *host* and fold the first document; a listener that is not yet serving.

    Separate from :func:`serve` so a caller can read the assigned port before any request could
    arrive: `--port 0` is the documented way to run a second board or a test, and polling a
    fixed port until it opens is exactly the flake that avoids.
    """
    surface = board_actions.ActionSurface(repo_root) if actions else None
    board = Board(repo_root, refresh_s=refresh_s, build=build, actions=surface)
    board.refresh()
    return Listener(_httpd=_Server((admitted_host(host), port), board), board=board)


def _tick(board: Board, stop: threading.Event) -> None:
    """Refresh on the cadence until *stop*. The wait *is* the sleep, so a stop is immediate."""
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
    """Run the board until SIGINT, then report what it did; the exit code.

    The counters are printed on the way out rather than logged as they happen, because the
    number a wall operator wants is "did this screen keep up", which is one line at the end and
    not a stream nobody reads.
    """
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
    ui.say(board_actions.transcript(listener.board.actions))
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

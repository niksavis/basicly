"""Serve the board on the loopback for a wall display: Mode B (basicly-rn0o.5, basicly-rn0o.6).

Three properties are this module, and each is a refusal rather than a feature. The listener
binds :data:`HOST` and nothing else, so a screen in an engineering room is driven by a browser
on the same machine and never by anything on the network. The only POST route is the one
:mod:`basicly.board_actions` owns, absent under `--no-actions`, because a display anyone in the
room can touch must not be able to kill a lane; any write an action causes is the engine's own
command making it. And this process writes nothing at all - no lock, no snapshot file, no path
under `.basicly/ledger/` - so a board can never fail a landing, and the stop line's "No state
was written" is a fact rather than a hope.

**Who produces is decided per tick, not per process.** While the supervisor lock is fresh, the
supervisor is already folding a snapshot on its own beat, so this process serves that file's
bytes and computes nothing: a second fold would be a second producer racing the first over one
path, and the transcript's `producer` line says which one is speaking. With no fresh holder it
folds for itself at ``--refresh`` and keeps the result in memory, which is why nothing it
serves outlives it.

**This is the layer that may read the lock**, and `board_cli` reads it through here rather than
spelling the shape twice. :mod:`basicly.board_snapshot` may not read it at all - the import
would close ``supervise -> board_snapshot -> supervise``, since the supervisor emits a snapshot
of its own - so the facts are read at this tier and passed down (C11).
"""

from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

from . import board_actions, board_render, board_schema, board_snapshot, supervise, ui

if TYPE_CHECKING:
    from collections.abc import Callable
from pathlib import Path

# C10, and the only line here that is a security boundary: the loopback literally, never
# `0.0.0.0` and never a name a resolver is free to point off this box.
HOST = "127.0.0.1"

# The transcript's port, fixed so a wall display's bookmark survives a restart. `--port 0`
# takes an ephemeral one, which is what a test and a second board on one machine use.
DEFAULT_PORT = 8787

# `supervise.HEARTBEAT_INTERVAL_S`, so an unsupervised board ticks at the cadence a supervised
# one would: a viewer cannot be fresher than the producer it is standing in for.
DEFAULT_REFRESH_S = supervise.HEARTBEAT_INTERVAL_S

SNAPSHOT_ROUTE = "/snapshot.json"
PAGE_ROUTES = ("/", "/index.html")

STOPPED = "board: stopped. {refreshes} refreshes, {failures} failed. No state was written."


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
    ) -> None:
        """Hold *repo_root*, the cadence, how to build and what may be acted on.

        *actions* is None for a read-only board: no surface, no POST route, and no panel.

        **`build` comes from above, and its absence was a real defect**: folding with the
        lock facts alone served 0 phases of 232 where `board --out` served 232.
        `board_facts` sits above this tier, so the caller passes a builder rather than this
        module reaching upward for one (basicly-sp8lce).
        """
        self.repo_root = repo_root
        self.refresh_s = refresh_s
        self._build = build
        self.actions = actions
        self.refreshes = 0
        self.failures = 0
        self._served: bytes | None = None
        self._folding = threading.Lock()

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

        A failure is counted and swallowed on purpose. A display whose fold hit a corrupt
        source must keep showing its last document with a growing age - that is what the STALE
        band is for - because exiting replaces a stale screen with a dark one, and a dark
        screen is the failure this whole design is against.
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

        A document the contract refuses is not drawn, for Mode A's reason: a page rendered over
        a document with no valid age on it is the one output this design has no honest use for.
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
        return board_actions.inject(drawn, self.actions).encode("utf-8")

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


class _Server(ThreadingHTTPServer):
    """A threaded loopback listener carrying the :class:`Board` its handlers read.

    Threaded because one browser holding a page open must not make the snapshot route wait;
    `daemon_threads` and `allow_reuse_address` are the base class's own defaults and are
    deliberately not respelled here.
    """

    def __init__(self, address: tuple[str, int], board: Board) -> None:
        self.board = board
        super().__init__(address, _Handler)


@dataclass(frozen=True)
class Listener:
    """A bound but not-yet-serving board, so a caller can read the port it actually got.

    The socket is held privately and reached through :meth:`run`, :meth:`stop` and
    :meth:`close`, because the three have an order a caller must not have to know: `stop`
    belongs to another thread than `run`, and `close` is the only one safe after an interrupt
    took `run` out from under the base class's own shutdown event.
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


def bind(
    repo_root: Path,
    *,
    port: int = DEFAULT_PORT,
    refresh_s: float = DEFAULT_REFRESH_S,
    build: Callable[[], dict[str, object]] | None = None,
    actions: bool = True,
) -> Listener:
    """Bind :data:`HOST` and fold the first document; a listener that is not yet serving.

    Separate from :func:`serve` so a caller can read the assigned port before any request could
    arrive: `--port 0` is the documented way to run a second board or a test, and polling a
    fixed port until it opens is exactly the flake that avoids.
    """
    surface = board_actions.ActionSurface(repo_root) if actions else None
    board = Board(repo_root, refresh_s=refresh_s, build=build, actions=surface)
    board.refresh()
    return Listener(_httpd=_Server((HOST, port), board), board=board)


def _tick(board: Board, stop: threading.Event) -> None:
    """Refresh on the cadence until *stop*. The wait *is* the sleep, so a stop is immediate."""
    while not stop.wait(board.refresh_s):
        board.refresh()


def serve(
    repo_root: Path,
    *,
    port: int = DEFAULT_PORT,
    refresh_s: float = DEFAULT_REFRESH_S,
    build: Callable[[], dict[str, object]] | None = None,
    actions: bool = True,
) -> int:
    """Run the board until SIGINT, then report what it did; the exit code.

    The counters are printed on the way out rather than logged as they happen, because the
    number a wall operator wants is "did this screen keep up", which is one line at the end and
    not a stream nobody reads.
    """
    try:
        listener = bind(repo_root, port=port, refresh_s=refresh_s, build=build, actions=actions)
    except OSError as exc:
        ui.warn(f"board: cannot listen on {HOST}:{port} - {exc}")
        return 1
    ui.say(
        f"board: serving {board_schema.VERSION} on {listener.url}  ({HOST} only; Ctrl-C to stop)"
    )
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

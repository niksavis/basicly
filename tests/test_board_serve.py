"""Mode B: the loopback board, and the four refusals that are the unit (basicly-rn0o.5).

Two rules for anything added here. Instrument the property off the live socket, never off a
constant. Never sleep: bind before the thread starts, gate on an Event, always port 0.
"""

# module-size-waiver: cohesion: 3877 -> 4469 of 4000, headroom was already 123.
# `board_serve.py`'s own docstring states its behaviour is "asserted in
# tests/test_board_serve.py, not described here" - the three self-staleness tests are that
# assertion, sharing `board_repo` and `_ready_document` with the rest of this module; a second
# suite would either duplicate the fixture or import it across files nothing else here does.

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from basicly import (
    board_cli,
    board_facts,
    board_render,
    board_schema,
    board_serve,
    board_snapshot,
    cli,
    owned_store,
    projection,
    supervise,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_LEDGER = REPO_ROOT / "tests" / "fixtures" / "board" / "ledger" / "events-0001.jsonl"
MINIMAL = REPO_ROOT / "tests" / "fixtures" / "board" / "minimal-v1.json"

# Every wait in this module is a condition with a bound, never a duration to elapse.
TIMEOUT_S = 20.0


@pytest.fixture
def board_repo(work_repo: Path) -> Path:
    """A work repo whose ledger is the frozen board corpus, so a fold has something to read."""
    ledger = owned_store.ledger_dir(work_repo)
    ledger.mkdir(parents=True, exist_ok=True)
    for stale in ledger.glob("events-*.jsonl"):
        stale.unlink()
    shutil.copy2(FIXTURE_LEDGER, ledger / "events-0001.jsonl")
    return work_repo


def _lock(repo_root: Path, *, age_s: float = 0.0) -> Path:
    """A supervisor lock whose heartbeat is *age_s* old, by mtime - staleness is mtime-only."""
    path = repo_root / supervise.LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": 4321, "session_id": "x-1:beef", "root_issue": "x-1"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    stamp = path.stat().st_mtime - age_s
    os.utime(path, (stamp, stamp))
    return path


@contextmanager
def _running(listener: board_serve.Listener) -> Iterator[board_serve.Listener]:
    """*listener* serving on a background thread, torn down however the body exits."""
    thread = threading.Thread(target=listener.run, daemon=True)
    thread.start()
    try:
        yield listener
    finally:
        listener.stop()
        listener.close()
        thread.join(timeout=TIMEOUT_S)


def _get(url: str) -> tuple[int, bytes, dict[str, str]]:
    """One GET: status, body and headers."""
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
        return response.status, response.read(), dict(response.headers)


def test_the_listener_binds_the_loopback_and_never_a_wildcard_or_a_name(board_repo: Path) -> None:
    """AC 1 and C10, read off the live socket rather than off the constant."""
    with _running(board_serve.bind(board_repo, port=0)) as listener:
        bound = ipaddress.ip_address(listener.host)
        assert bound.is_loopback
        assert not bound.is_unspecified
        assert listener.host == "127.0.0.1"
        assert listener.port > 0
        assert listener.url == f"http://127.0.0.1:{listener.port}"


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("0.0.0.0", "every interface"),
        ("board.example.com", "not an IP literal"),
        ("::1", "IPv6"),
    ],
)
def test_a_wildcard_a_name_and_ipv6_are_refused_by_the_admission_rule(
    value: str, reason: str
) -> None:
    """C10 survives the LAN bind (basicly-bxk5g8): only a chosen IPv4 literal is admitted."""
    with pytest.raises(ValueError, match=reason):
        board_serve.admitted_host(value)


def test_an_explicit_interface_literal_is_admitted_and_bound(board_repo: Path) -> None:
    """The touch-wall case, driven on the one non-loopback-free address every box has."""
    assert board_serve.admitted_host("127.0.0.1") == "127.0.0.1"
    with _running(board_serve.bind(board_repo, port=0, host="127.0.0.1")) as listener:
        assert listener.host == "127.0.0.1"


def test_the_two_get_routes_answer_and_a_post_is_405(board_repo: Path) -> None:
    """AC 6 and AC 7: the read-only board, spelled `actions=False` rather than defaulted.

    405 rather than the base class's 501: this resource is never going to take a POST.
    """
    with _running(board_serve.bind(board_repo, port=0, actions=False)) as listener:
        status, body, headers = _get(f"{listener.url}/snapshot.json")
        assert status == 200
        assert json.loads(body)["schema"] == board_schema.VERSION
        assert headers["Content-Type"] == "application/json"

        status, page, headers = _get(listener.url + "/")
        assert status == 200
        assert board_schema.VERSION in page.decode("utf-8")
        assert headers["Refresh"] == "15"

        post = urllib.request.Request(f"{listener.url}/action", data=b"", method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(post, timeout=TIMEOUT_S)
        assert refused.value.code == 405
        assert refused.value.headers["Allow"] == "GET"

        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{listener.url}/elsewhere", timeout=TIMEOUT_S)
        assert missing.value.code == 404


def test_the_served_snapshot_validates_and_is_fresher_than_the_cadence_it_declares(
    board_repo: Path,
) -> None:
    """The acceptance criterion over the wire: a document must not outlive its own cadence."""
    with _running(board_serve.bind(board_repo, port=0, refresh_s=15.0)) as listener:
        _status, body, _headers = _get(f"{listener.url}/snapshot.json")
    document: dict[str, Any] = json.loads(body)

    assert board_schema.verdict(board_repo, document).readable
    assert document["freshness"]["source"] == board_snapshot.SELF_REFRESH
    assert document["freshness"]["cadence_s"] == 15.0
    assert document["freshness"]["stale_after_s"] == supervise.STALE_AFTER_S
    assert board_serve.DEFAULT_REFRESH_S == supervise.HEARTBEAT_INTERVAL_S == 15.0


def test_a_fresh_lock_makes_the_route_the_supervisors_file_byte_for_byte(
    board_repo: Path,
) -> None:
    """AC 2: the file differs from a fold, so folding anyway would not compare equal."""
    _lock(board_repo)
    minimal = json.loads(MINIMAL.read_text(encoding="utf-8"))
    landed = board_snapshot.write_document(board_repo, minimal)

    board = board_serve.Board(board_repo)
    assert board.refresh() is False
    assert board.refreshes == 0
    assert board.payload() == landed.read_bytes()
    assert board.producer().startswith("board: producer  supervisor x-1:beef (pid 4321)")


def test_a_stale_lock_hands_the_fold_back_to_the_viewer(board_repo: Path) -> None:
    """AC 3: a stale lock is a crashed supervisor, which is when a viewer must fold for itself."""
    _lock(board_repo, age_s=supervise.STALE_AFTER_S + 1)
    board = board_serve.Board(board_repo, refresh_s=1.0)

    assert board.refresh() is True
    assert board.refreshes == 1
    served = board.payload()
    assert served is not None
    assert json.loads(served)["session"]["holder"]["stale"] is True
    assert "self-refresh every 1s" in board.producer()


def test_the_server_takes_no_lock_and_writes_nothing_at_all(
    board_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 5, twice: the write seams refuse and the tree is listed, as either is fail-open."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the board server wrote state")

    monkeypatch.setattr(projection, "atomic_write_text", refuse)
    monkeypatch.setattr(board_snapshot, "write_document", refuse)
    monkeypatch.setattr(supervise, "acquire", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    before = sorted(path.relative_to(board_repo) for path in board_repo.rglob("*"))
    with _running(board_serve.bind(board_repo, port=0)) as listener:
        assert _get(f"{listener.url}/snapshot.json")[0] == 200
        assert _get(listener.url + "/")[0] == 200
        listener.board.refresh()

    assert sorted(path.relative_to(board_repo) for path in board_repo.rglob("*")) == before
    assert not (board_repo / supervise.LOCK_FILE).exists()
    assert not (board_repo / board_snapshot.SNAPSHOT_FILE).exists()


def test_a_tick_arriving_mid_fold_is_dropped_rather_than_queued(
    board_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 3's "never more than one refresh in flight", as a concurrency measurement.

    The fold is gated on an event so the second caller is guaranteed to arrive while the first
    is still inside it - the ordering is made deterministic rather than raced for.
    """
    inside = threading.Event()
    release = threading.Event()
    depth: list[int] = []
    real = board_snapshot.build_document

    def gated(*args: Any, **kwargs: Any) -> Any:
        depth.append(1)
        inside.set()
        assert release.wait(TIMEOUT_S)
        return real(*args, **kwargs)

    monkeypatch.setattr(board_snapshot, "build_document", gated)
    board = board_serve.Board(board_repo)
    first = threading.Thread(target=board.refresh)
    first.start()
    assert inside.wait(TIMEOUT_S)

    assert board.refresh() is False
    release.set()
    first.join(timeout=TIMEOUT_S)

    assert depth == [1]
    assert board.refreshes == 1


def test_a_fold_that_raises_is_counted_and_leaves_the_last_document_standing(
    board_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dark screen is the failure this design is against, so a bad fold costs a counter."""
    board = board_serve.Board(board_repo)
    assert board.refresh() is True
    good = board.payload()

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("corrupt source")

    monkeypatch.setattr(board_snapshot, "build_document", explode)
    assert board.refresh() is False
    assert board.failures == 1
    assert board.refreshes == 1
    assert board.payload() == good


def test_ctrl_c_reports_the_counts_and_that_no_state_was_written(
    board_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC 5's clean exit, by entering the real recovery path rather than reading it.

    `serve_forever` is replaced with the interrupt it would raise, which is the only thing a
    Ctrl-C does to this process; everything after it - the ticker stop, the socket close and the
    report - is the code under test.
    """
    monkeypatch.chdir(board_repo)

    def interrupt(_self: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(board_serve._Server, "serve_forever", interrupt)
    assert cli.main(["board", "serve", "--port", "0"]) == 0

    printed = capsys.readouterr().out
    assert f"serving {board_schema.VERSION} on http://127.0.0.1:" in printed
    assert "(127.0.0.1 only; Ctrl-C to stop)" in printed
    assert "holds no lock and blocks no gate" in printed
    assert board_serve.STOPPED.format(refreshes=1, failures=0) in printed


def test_a_port_already_taken_is_reported_rather_than_raised(board_repo: Path) -> None:
    """A consumer gets a line and an exit code, never a traceback out of a socket."""
    with _running(board_serve.bind(board_repo, port=0)) as listener:
        assert board_serve.serve(board_repo, port=listener.port) == 1


def test_the_bind_claims_the_port_exclusively_wherever_the_platform_offers_that(
    board_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal above, on the platform that does not give it free.

    Injected, never raced: the option is Windows-only and cost one run 5h57m in `serve_forever`.
    """
    assert board_serve._Server.allow_reuse_address is (board_serve.EXCLUSIVE_BIND_OPTION is None)

    asked: list[int] = []
    real = socket.socket.setsockopt
    monkeypatch.setattr(board_serve, "EXCLUSIVE_BIND_OPTION", socket.SO_REUSEADDR)
    monkeypatch.setattr(
        socket.socket,
        "setsockopt",
        lambda self, level, option, value: asked.append(option) or real(self, level, option, value),
    )
    board_serve.bind(board_repo, port=0).close()

    assert socket.SO_REUSEADDR in asked


def test_the_serve_help_carries_both_frozen_claims_and_never_the_word_it_refuses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """C1, on the surface that would be tempted: a live-attached mode is where the word appears.

    Asserted in both directions, as `test_board_cli.py` does for the group: a surface that
    spends the word in order to deny it has still spent it.
    """
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["board", "serve", "--help"])
    assert exit_info.value.code == 0
    printed = " ".join(capsys.readouterr().out.split())
    assert board_cli.FRESHNESS in printed
    assert board_cli.NO_WRITES in printed
    assert "real-time" not in printed
    assert "real time" not in printed


def test_the_served_document_carries_what_the_emitted_one_carries(tmp_path: Path) -> None:
    """Two producers, one contract: the server must not be the poorer of them.

    It was. Measured on a live tree before the fix: the served document carried a phase on
    0 of 232 units against the emitted document's 232, no `ready` at all, and a `repo`
    section holding only a name. `board_facts` sits above this tier and cannot be imported
    here, so the caller passes a builder rather than this module reaching for one - and
    this test is what keeps a third producer from diverging in silence.
    """
    marker = {"schema": "harness-board/v1", "units": [{"id": "demo-1", "phase": "build"}]}
    board = board_serve.Board(tmp_path, build=lambda: dict(marker))

    assert board.refresh() is True
    served = json.loads(board.payload() or b"{}")

    assert served["units"] == marker["units"], "the builder's facts must reach the wire"


def test_a_live_supervisor_serves_no_less_than_the_viewer_folded_for_itself(
    board_repo: Path,
) -> None:
    """basicly-bd4epr: handing production to the supervisor must not cost the wall a section.

    A live lock displaces the viewer's fold entirely - `refresh` returns False and the route
    answers the supervisor's file - so these are two producers under one contract, and the only
    honest check is the relation between them. Both are folded here rather than one being
    remembered: a count copied from a previous run cannot fail when the corpus moves under it.
    """
    viewer = board_serve.Board(board_repo, build=lambda: board_facts.document(board_repo))
    assert viewer.refresh() is True
    unsupervised: dict[str, Any] = json.loads(viewer.payload() or b"{}")

    _lock(board_repo)
    board_facts.emit_tick(board_repo, supervise.HEARTBEAT_INTERVAL_S)
    supervised = board_serve.Board(board_repo, build=lambda: board_facts.document(board_repo))
    assert supervised.refresh() is False, "a live holder owns the tick"
    served: dict[str, Any] = json.loads(supervised.payload() or b"{}")

    # `lanes` carved out for the reason test_supervise_board records: this fixture's root
    # is not a record id, so the tick withholds the section while the viewer earns `[]`
    # from the absent lock (basicly-u6eeag).
    assert set(unsupervised) - {"lanes"} <= set(served)
    assert "lanes" not in served, "the fixture's root is not a record id, so no session derives"
    phased = [unit for unit in served["units"] if unit.get("phase")]
    assert phased, "the corpus must carry a phase for this comparison to discriminate"
    assert len(phased) == len([unit for unit in unsupervised["units"] if unit.get("phase")])
    assert served["backlog"]["ready"] == unsupervised["backlog"]["ready"]
    assert served["freshness"]["source"] == board_snapshot.SUPERVISOR_TICK
    assert board_schema.verdict(board_repo, served).readable


def test_the_served_freshness_is_the_servers_own_cadence(tmp_path: Path) -> None:
    """A builder written for Mode A cannot know the cadence it is served at."""
    board = board_serve.Board(tmp_path, refresh_s=7.0, build=lambda: {"schema": "harness-board/v1"})

    assert board.refresh() is True
    freshness = json.loads(board.payload() or b"{}")["freshness"]

    assert freshness["source"] == board_snapshot.SELF_REFRESH
    assert freshness["cadence_s"] == 7.0


def _ready_document() -> dict[str, Any]:
    """One readable document naming exactly one ready unit, for the fault tests below."""
    return {
        "schema": "harness-board/v1",
        "generated_at": "2026-08-14T16:42:52Z",
        "units": [{"id": "demo-1", "title": "Demo One", "priority": "P1", "ready": True}],
    }


def test_the_page_always_names_this_producers_own_age(board_repo: Path) -> None:
    """AC 3: the page already reports the document's freshness; it must report its own too."""
    board = board_serve.Board(board_repo, build=_ready_document)

    assert board.refresh() is True
    page = board.page(datetime.now(UTC))

    assert page is not None
    assert "producer age" in page.decode("utf-8")


def test_a_template_newer_than_this_process_is_named_a_fault_not_a_blank(board_repo: Path) -> None:
    """AC 1 and AC 4, reproducing `f7788bb7` without touching the real template on disk.

    A sibling lane may be rendering through that same file, so staleness is injected via
    *template_mtime* rather than by writing to it.
    """
    board = board_serve.Board(
        board_repo, build=_ready_document, template_mtime=lambda: time.time() + 3600
    )
    assert board.refresh() is True

    page = board.page(datetime.now(UTC))

    assert page is not None
    text = page.decode("utf-8")
    assert "the template changed" in text
    assert "Restart the board." in text


def test_rows_the_model_computed_that_never_reached_the_page_are_named_a_fault(
    board_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 2: `ready.rows` non-empty but `ready.groups` drew nothing is exactly `f7788bb7`.

    `board_render.page` is replaced with a version that renders as an old template would
    have - dropping the one row's id - while the unpatched `board_render.context` still
    reports the row this process actually computed, which is the mismatch this guards.
    """
    real_page = board_render.page

    def outdated(*args: Any, **kwargs: Any) -> str:
        return real_page(*args, **kwargs).replace("demo-1", "")

    monkeypatch.setattr(board_render, "page", outdated)
    board = board_serve.Board(board_repo, build=_ready_document)
    assert board.refresh() is True

    page = board.page(datetime.now(UTC))

    assert page is not None
    assert board_serve.DROPPED_ROWS_FAULT in page.decode("utf-8")

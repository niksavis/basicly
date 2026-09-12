# module-size-waiver: cohesion: 3877 -> 4469 of 4000, headroom was already 123.

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
    board_assets,
    board_cli,
    board_facts,
    board_render,
    board_schema,
    board_serve,
    board_snapshot,
    board_wall,
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

TIMEOUT_S = 20.0


@pytest.fixture
def board_repo(work_repo: Path) -> Path:
    ledger = owned_store.ledger_dir(work_repo)
    ledger.mkdir(parents=True, exist_ok=True)
    for stale in ledger.glob("events-*.jsonl"):
        stale.unlink()
    shutil.copy2(FIXTURE_LEDGER, ledger / "events-0001.jsonl")
    return work_repo


def _lock(repo_root: Path, *, age_s: float = 0.0) -> Path:
    path = repo_root / supervise.LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": 4321, "session_id": "x-1:beef", "root_issue": "x-1"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    stamp = path.stat().st_mtime - age_s
    os.utime(path, (stamp, stamp))
    return path


@contextmanager
def _running(listener: board_serve.Listener) -> Iterator[board_serve.Listener]:
    thread = threading.Thread(target=listener.run, daemon=True)
    thread.start()
    try:
        yield listener
    finally:
        listener.stop()
        listener.close()
        thread.join(timeout=TIMEOUT_S)


def _get(url: str) -> tuple[int, bytes, dict[str, str]]:
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
        return response.status, response.read(), dict(response.headers)


def test_the_listener_binds_the_loopback_and_never_a_wildcard_or_a_name(board_repo: Path) -> None:
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
    with pytest.raises(ValueError, match=reason):
        board_serve.admitted_host(value)


def test_an_explicit_interface_literal_is_admitted_and_bound(board_repo: Path) -> None:
    assert board_serve.admitted_host("127.0.0.1") == "127.0.0.1"
    with _running(board_serve.bind(board_repo, port=0, host="127.0.0.1")) as listener:
        assert listener.host == "127.0.0.1"


def test_the_two_get_routes_answer_and_a_post_is_405(board_repo: Path) -> None:

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


def test_the_vendored_stylesheet_is_served_by_name_and_nothing_else_beside_it(
    board_repo: Path,
) -> None:

    with _running(board_serve.bind(board_repo, port=0, actions=False)) as listener:
        status, body, headers = _get(f"{listener.url}/{board_assets.href(board_assets.STYLESHEET)}")
        assert status == 200
        assert headers["Content-Type"] == board_assets.ASSETS[board_assets.STYLESHEET]
        vendored = board_assets.read(board_render.root(), board_assets.STYLESHEET)
        assert vendored is not None
        assert body == vendored[0]
        assert "Refresh" not in headers

        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(f"{listener.url}{board_assets.ROUTE}LICENSE", timeout=TIMEOUT_S)
        assert refused.value.code == 404


def test_the_record_route_answers_with_and_without_the_suffix(board_repo: Path) -> None:

    with _running(board_serve.bind(board_repo, port=0)) as listener:
        document = json.loads(_get(f"{listener.url}{board_serve.SNAPSHOT_ROUTE}")[1])
        ident = document["units"][0]["id"]

        plain = _get(f"{listener.url}/record/{ident}")
        suffixed = _get(f"{listener.url}/record/{ident}.html")
        assert plain[0] == 200
        assert ident in plain[1].decode("utf-8")
        assert plain[1] == suffixed[1], "the two spellings answer with different pages"

        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{listener.url}/record/no-such-record", timeout=TIMEOUT_S)
        assert missing.value.code == 404


def test_the_served_snapshot_validates_and_is_fresher_than_the_cadence_it_declares(
    board_repo: Path,
) -> None:
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
    _lock(board_repo)
    minimal = json.loads(MINIMAL.read_text(encoding="utf-8"))
    landed = board_snapshot.write_document(board_repo, minimal)

    board = board_serve.Board(board_repo)
    assert board.refresh() is False
    assert board.refreshes == 0
    assert board.payload() == landed.read_bytes()
    assert board.producer().startswith("board: producer  supervisor x-1:beef (pid 4321)")


def test_a_stale_lock_hands_the_fold_back_to_the_viewer(board_repo: Path) -> None:
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
    with _running(board_serve.bind(board_repo, port=0)) as listener:
        assert board_serve.serve(board_repo, port=listener.port) == 1


def test_the_bind_claims_the_port_exclusively_wherever_the_platform_offers_that(
    board_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

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

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["board", "serve", "--help"])
    assert exit_info.value.code == 0
    printed = " ".join(capsys.readouterr().out.split())
    assert board_cli.FRESHNESS in printed
    assert board_cli.NO_WRITES in printed
    assert "real-time" not in printed
    assert "real time" not in printed


def test_the_served_document_carries_what_the_emitted_one_carries(tmp_path: Path) -> None:

    marker = {"schema": "harness-board/v1", "units": [{"id": "demo-1", "phase": "build"}]}
    board = board_serve.Board(tmp_path, build=lambda: dict(marker))

    assert board.refresh() is True
    served = json.loads(board.payload() or b"{}")

    assert served["units"] == marker["units"], "the builder's facts must reach the wire"


def test_a_live_supervisor_serves_no_less_than_the_viewer_folded_for_itself(
    board_repo: Path,
) -> None:

    viewer = board_serve.Board(board_repo, build=lambda: board_facts.document(board_repo))
    assert viewer.refresh() is True
    unsupervised: dict[str, Any] = json.loads(viewer.payload() or b"{}")

    _lock(board_repo)
    board_facts.emit_tick(board_repo, supervise.HEARTBEAT_INTERVAL_S)
    supervised = board_serve.Board(board_repo, build=lambda: board_facts.document(board_repo))
    assert supervised.refresh() is False, "a live holder owns the tick"
    served: dict[str, Any] = json.loads(supervised.payload() or b"{}")

    assert set(unsupervised) - {"lanes"} <= set(served)
    assert "lanes" not in served, "the fixture's root is not a record id, so no session derives"
    phased = [unit for unit in served["units"] if unit.get("phase")]
    assert phased, "the corpus must carry a phase for this comparison to discriminate"
    assert len(phased) == len([unit for unit in unsupervised["units"] if unit.get("phase")])
    assert served["backlog"]["ready"] == unsupervised["backlog"]["ready"]
    assert served["freshness"]["source"] == board_snapshot.SUPERVISOR_TICK
    assert board_schema.verdict(board_repo, served).readable


def test_the_served_freshness_is_the_servers_own_cadence(tmp_path: Path) -> None:
    board = board_serve.Board(tmp_path, refresh_s=7.0, build=lambda: {"schema": "harness-board/v1"})

    assert board.refresh() is True
    freshness = json.loads(board.payload() or b"{}")["freshness"]

    assert freshness["source"] == board_snapshot.SELF_REFRESH
    assert freshness["cadence_s"] == 7.0


def _ready_document() -> dict[str, Any]:
    return {
        "schema": "harness-board/v1",
        "generated_at": "2026-08-14T16:42:52Z",
        "units": [{"id": "demo-1", "title": "Demo One", "priority": "P1", "ready": True}],
    }


def test_a_healthy_page_does_not_spend_a_line_on_this_producers_own_age(
    board_repo: Path,
) -> None:

    board = board_serve.Board(board_repo, build=_ready_document)

    assert board.refresh() is True
    page = board.page(datetime.now(UTC))

    assert page is not None
    assert "producer age" not in page.decode("utf-8")


def test_a_template_newer_than_this_process_is_named_a_fault_not_a_blank(board_repo: Path) -> None:

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

    real_render = board_render.render

    def outdated(*args: Any, **kwargs: Any) -> str:
        return real_render(*args, **kwargs).replace("demo-1", "")

    monkeypatch.setattr(board_render, "render", outdated)
    board = board_serve.Board(board_repo, build=_ready_document)
    assert board.refresh() is True

    page = board.page(datetime.now(UTC))

    assert page is not None
    assert board_serve.DROPPED_ROWS_FAULT in page.decode("utf-8")


def _drawn(board: board_serve.Board) -> str:
    assert board.refresh() is True
    page = board.page(datetime.now(UTC))
    assert page is not None
    return page.decode("utf-8")


def test_the_producers_own_note_lands_inside_the_grid_and_never_after_it(
    board_repo: Path,
) -> None:

    text = _drawn(
        board_serve.Board(
            board_repo, build=_ready_document, template_mtime=lambda: time.time() + 3600
        )
    )
    tick = text.index('<section class="region tick">')

    assert board_serve.NOTES_SLOT not in text, "the slot was left empty, so no note was filled in"
    assert tick < text.index("producer age") < text.index("</section>", tick)
    closed = [line.strip() for line in text[text.rindex("</main>") :].strip().splitlines()]
    assert closed == ["</main>", "</body>", "</html>"], "the grid is no longer the last thing drawn"


def test_a_fault_takes_the_line_above_the_events_and_the_state_colour(board_repo: Path) -> None:
    board = board_serve.Board(
        board_repo, build=_ready_document, template_mtime=lambda: time.time() + 3600
    )
    text = _drawn(board)

    assert '<div class="notes fault">' in text
    assert f"state-{board_wall.STALE}" in text
    assert text.index("the template changed") < text.index('<span class="label">events')


def test_a_page_whose_template_left_no_slot_still_carries_the_note(
    board_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_page = board_render.page
    monkeypatch.setattr(
        board_render,
        "page",
        lambda *a, **k: real_page(*a, **k).replace(board_serve.NOTES_SLOT, ""),
    )

    faulted = board_serve.Board(
        board_repo, build=_ready_document, template_mtime=lambda: time.time() + 3600
    )
    assert "producer age" in _drawn(faulted)

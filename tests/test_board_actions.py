from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import pytest

from basicly import board_action_surface, board_actions, board_asks, board_serve, cli, policy

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).parent.parent
MINIMAL = REPO_ROOT / "tests" / "fixtures" / "board" / "minimal-v1.json"
TIMEOUT_S = 20.0

PLANTED = "planted-code-3f9c2ae1"

SUBMISSIONS = {
    "loop-answer": {"decision_id": "x-1#abc", "text": "-do it"},
    "checkpoint-approve": {"issue": "x-1", "name": "ship", "confirm": "abc123"},
    "lane-kill": {"issue": "x-1", "reason": "-wrong shape", "confirm": "abc123"},
    "record-park": {"issue": "x-1"},
    "record-resume": {"issue": "x-1"},
    "record-start": {"issue": "x-1", "work_type": "bug", "root": "x-2"},
}
VERBS = {
    "loop-answer": ("loop", "answer"),
    "checkpoint-approve": ("policy", "checkpoint"),
    "lane-kill": ("loop", "kill"),
    "record-park": ("tracker", "write"),
    "record-resume": ("tracker", "write"),
    "record-start": ("loop", "run"),
}
ORIGIN = "http://127.0.0.1:1"


REPLY = (0, "checkpoint ship: APPROVED (x-1)")


class _Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.cwds: list[Path] = []
        self.said: list[str] = []

    def __call__(self, argv: tuple[str, ...], cwd: Path) -> tuple[int, str]:
        self.calls.append(argv)
        self.cwds.append(cwd)
        return REPLY

    def echo(self, line: str) -> None:
        self.said.append(line)


class _Surface(board_action_surface.ActionSurface):
    def __init__(self, repo_root: Path) -> None:
        self.spy = _Spy()
        super().__init__(repo_root, run=self.spy, echo=self.spy.echo)


@pytest.fixture
def surface(tmp_path: Path) -> _Surface:
    return _Surface(tmp_path)


def _form(surface: board_action_surface.ActionSurface, action: str, **fields: str) -> bytes:
    return urlencode({"token": surface.token, "action": action, **fields}).encode("utf-8")


ASK = {
    "wait_id": "x-1#wait-ship",
    "issue": "x-1",
    "kind": "checkpoint",
    "subject": "ship",
    "question": "ship this?",
    "waiting_s": 90,
    "actions": [{"offer": "Approve the ship checkpoint", "basicly": "checkpoint-approve"}],
}


def _document() -> dict[str, Any]:
    document = json.loads(MINIMAL.read_text(encoding="utf-8"))
    document["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    document["asks"] = [ASK]
    return document


def _running(listener: board_serve.Listener) -> Iterator[board_serve.Listener]:
    thread = threading.Thread(target=listener.run, daemon=True)
    thread.start()
    try:
        yield listener
    finally:
        listener.stop()
        listener.close()
        thread.join(timeout=TIMEOUT_S)


@pytest.fixture
def served(work_repo: Path) -> Iterator[board_serve.Listener]:
    yield from _running(board_serve.bind(work_repo, port=0, build=_document, actions=True))


def _post(url: str, body: bytes, *, origin: str | None) -> tuple[int, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read().decode("utf-8")


def test_the_action_table_holds_exactly_six_entries_and_names_them() -> None:

    assert len(board_actions.ACTIONS) == 6
    assert set(board_actions.ACTIONS) == set(SUBMISSIONS) == set(VERBS)


@pytest.mark.parametrize("name", list(SUBMISSIONS))
def test_every_action_is_an_argv_list_headed_by_the_basicly_executable(
    surface: _Surface, name: str
) -> None:
    planned = surface.plan(ORIGIN, 1, _form(surface, name, **SUBMISSIONS[name]))
    assert isinstance(planned, tuple)
    assert planned[0] == board_action_surface.executable()
    assert planned[1 : 1 + len(VERBS[name])] == VERBS[name]


@pytest.mark.parametrize("name", list(SUBMISSIONS))
def test_the_real_parser_accepts_every_argv_the_table_builds(surface: _Surface, name: str) -> None:

    planned = surface.plan(ORIGIN, 1, _form(surface, name, **SUBMISSIONS[name]))
    assert isinstance(planned, tuple)
    parsed = cli._build_parser().parse_args(list(planned[1:]))
    assert parsed.command == VERBS[name][0]


def test_no_action_path_reads_any_file_let_alone_the_confirm_codes(
    surface: _Surface, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    minted = tmp_path / policy._CONFIRM_FILE
    minted.parent.mkdir(parents=True, exist_ok=True)
    minted.write_text(json.dumps({"x-1:ship": {"code": PLANTED, "expires": 9e9}}), encoding="utf-8")
    opened: list[str] = []
    real = Path.open

    def spy(self: Path, *args: object, **kwargs: object) -> Any:
        opened.append(str(self))
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", spy)
    board_asks.pending([ASK], surface.token)
    surface.respond(
        origin=ORIGIN,
        port=1,
        body=_form(surface, "checkpoint-approve", issue="x-1", name="ship", confirm=PLANTED),
    )
    assert opened == []

    assert policy._read_confirms(minted)
    assert [path for path in opened if "checkpoint-confirms" in path]


def test_no_confirm_code_reaches_the_row_the_reply_or_the_audit_line(surface: _Surface) -> None:

    rows = board_asks.pending([ASK], surface.token)[0]
    confirms = [
        field for row in rows for field in row["fields"] if field["name"] == board_actions._CONFIRM
    ]
    assert confirms, "no row asked for a confirm code, so this probe proves nothing"
    assert all(field["value"] == "" and field["typed"] for field in confirms)
    assert PLANTED not in json.dumps(rows)

    body = _form(surface, "checkpoint-approve", issue="x-1", name="ship", confirm=PLANTED)
    reply = surface.respond(origin=ORIGIN, port=1, body=body)
    assert PLANTED not in reply.text
    assert board_actions.REDACTED in reply.text
    assert not [line for line in surface.spy.said if PLANTED in line]
    assert surface.spy.calls[0][-1] == PLANTED


@pytest.mark.parametrize(
    ("name", "fields"),
    [
        ("checkpoint-approve", {"issue": "x-1", "name": "ship", "confirm": ""}),
        ("lane-kill", {"issue": "x-1", "reason": "wrong shape", "confirm": "  "}),
    ],
)
def test_an_empty_confirm_code_refuses_and_invokes_nothing(
    surface: _Surface, name: str, fields: dict[str, str]
) -> None:

    reply = surface.respond(origin=ORIGIN, port=1, body=_form(surface, name, **fields))
    assert reply.status == HTTPStatus.BAD_REQUEST
    assert "confirm code is empty" in reply.text
    assert surface.spy.calls == []


def test_a_field_that_could_arrive_as_a_flag_is_refused(surface: _Surface) -> None:
    body = _form(surface, "lane-kill", issue="--discard", reason="x", confirm="abc123")
    reply = surface.respond(origin=ORIGIN, port=1, body=body)
    assert reply.status == HTTPStatus.BAD_REQUEST
    assert "not an identifier" in reply.text
    assert surface.spy.calls == []


@pytest.mark.parametrize("origin", [None, "http://evil.example", "http://127.0.0.1:2"])
def test_a_submission_from_another_origin_is_forbidden_and_invokes_nothing(
    surface: _Surface, origin: str | None
) -> None:
    body = _form(surface, "loop-answer", decision_id="x-1#abc", text="yes")
    reply = surface.respond(origin=origin, port=1, body=body)
    assert reply.status == HTTPStatus.FORBIDDEN
    assert surface.spy.calls == []


def test_a_submission_carrying_another_processs_token_is_forbidden(surface: _Surface) -> None:
    stale = _Surface(surface.repo_root)
    reply = surface.respond(
        origin=ORIGIN, port=1, body=_form(stale, "loop-answer", decision_id="x-1#abc", text="yes")
    )
    assert reply.status == HTTPStatus.FORBIDDEN
    assert surface.spy.calls == []
    assert stale.token != surface.token


def test_every_invocation_is_echoed_before_it_runs_and_after_it_returns(surface: _Surface) -> None:
    body = _form(surface, "loop-answer", decision_id="x-1#abc", text="yes")
    reply = surface.respond(origin=ORIGIN, port=1, body=body)

    assert len(surface.spy.said) == 2
    assert surface.spy.said[0].startswith("board: action   running ")
    assert surface.spy.said[1].startswith("board: action   exit 0 from ")
    assert "loop answer" in surface.spy.said[0]
    assert "exit 0" in reply.text
    assert surface.spy.cwds == [surface.repo_root]


def test_a_read_only_board_answers_405_and_draws_no_panel(work_repo: Path) -> None:
    for board in _running(board_serve.bind(work_repo, port=0, build=_document, actions=False)):
        assert board.board.actions is None
        status, _text = _post(f"{board.url}{board_actions.ROUTE}", b"", origin=None)
        assert status == HTTPStatus.METHOD_NOT_ALLOWED
        page = urllib.request.urlopen(board.url + "/", timeout=TIMEOUT_S).read()
        assert b"<form" not in page
        assert b'name="token"' not in page


def test_the_no_actions_flag_exists_and_the_default_is_actions_on() -> None:
    parser = cli._build_parser()
    assert parser.parse_args(["board", "serve"]).no_actions is False
    assert parser.parse_args(["board", "serve", "--no-actions"]).no_actions is True

"""The action surface, and the authority boundary that is the whole of it (basicly-rn0o.6).

The property this suite exists for cannot be read off the source: a board that read
`.basicly/usage/checkpoint-confirms.json` and pre-filled the field would pass every functional
test here, because the approve would still land. So the boundary is instrumented twice - a spy
over every read the surface could make, and a live code planted in the embargoed file and then
searched for in the page and in every reply. Either alone is fail-open: the spy misses a read
by a path it does not name, the search misses a read whose value is never rendered, and the spy
carries a positive control because a recording of nothing is ambiguous.

Nothing spawns a subprocess. The runner is injected through
:class:`basicly.board_actions.ActionSurface`, so "invoked nothing" is a counter rather than an
inference, and no test's spy can answer for another's real call.
"""

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

from basicly import board_actions, board_serve, cli, policy

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).parent.parent
MINIMAL = REPO_ROOT / "tests" / "fixtures" / "board" / "minimal-v1.json"
TIMEOUT_S = 20.0

# A code planted in the file the board may never read. Long and distinctive so its absence from
# a page is a real search rather than a match on two hex characters.
PLANTED = "planted-code-3f9c2ae1"

# One submission per table entry, shared by the argv-shape test and the parser test. The free
# text opens with a dash on purpose: that is the value an argv seam gets wrong, by handing
# argparse something it reads as a flag.
SUBMISSIONS = {
    "loop-answer": {"decision_id": "x-1#abc", "text": "-do it"},
    "checkpoint-approve": {"issue": "x-1", "name": "ship", "confirm": "abc123"},
    "lane-kill": {"issue": "x-1", "reason": "-wrong shape", "confirm": "abc123"},
}
VERBS = {
    "loop-answer": ("loop", "answer"),
    "checkpoint-approve": ("policy", "checkpoint"),
    "lane-kill": ("loop", "kill"),
}
ORIGIN = "http://127.0.0.1:1"


# What the spy answers with, standing in for what the CLI would have printed.
REPLY = (0, "checkpoint ship: APPROVED (x-1)")


class _Spy:
    """A runner and an echo in one: it records argv and lines, and spawns nothing."""

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


class _Surface(board_actions.ActionSurface):
    """An action surface carrying the spy that stands in for both its runner and its echo."""

    def __init__(self, repo_root: Path) -> None:
        self.spy = _Spy()
        super().__init__(repo_root, run=self.spy, echo=self.spy.echo)


@pytest.fixture
def surface(tmp_path: Path) -> _Surface:
    """A surface that will refuse or record, but never spawn."""
    return _Surface(tmp_path)


def _form(surface: board_actions.ActionSurface, action: str, **fields: str) -> bytes:
    """A urlencoded submission carrying *surface*'s token, as its own panel would post one."""
    return urlencode({"token": surface.token, "action": action, **fields}).encode("utf-8")


def _document() -> dict[str, Any]:
    """The minimal conformant snapshot, dated now so its age is valid and a page renders."""
    document = json.loads(MINIMAL.read_text(encoding="utf-8"))
    document["generated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return document


def _running(listener: board_serve.Listener) -> Iterator[board_serve.Listener]:
    """*listener* serving on a background thread, torn down however the caller exits."""
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
    """A serving board with actions registered, over a document a page can be drawn from."""
    yield from _running(board_serve.bind(work_repo, port=0, build=_document, actions=True))


def _post(url: str, body: bytes, *, origin: str | None) -> tuple[int, str]:
    """One POST, its status and its text, with `HTTPError` unwrapped into the same shape."""
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read().decode("utf-8")


# --- AC 1: the closed table ------------------------------------------------


def test_the_action_table_holds_exactly_three_entries_and_names_them() -> None:
    """AC 1, asserted as a length as well as a membership.

    A fourth verb reaching the wall is the failure this table exists to make loud, and a
    membership check alone waves it through.
    """
    assert len(board_actions.ACTIONS) == 3
    assert set(board_actions.ACTIONS) == set(SUBMISSIONS) == set(VERBS)


@pytest.mark.parametrize("name", list(SUBMISSIONS))
def test_every_action_is_an_argv_list_headed_by_the_basicly_executable(
    surface: _Surface, name: str
) -> None:
    """AC 1: the head is the resolved executable and the tail is the CLI verb, never a shell."""
    planned = surface.plan(ORIGIN, 1, _form(surface, name, **SUBMISSIONS[name]))
    assert isinstance(planned, tuple)
    assert planned[0] == board_actions.executable()
    assert planned[1 : 1 + len(VERBS[name])] == VERBS[name]


@pytest.mark.parametrize("name", list(SUBMISSIONS))
def test_the_real_parser_accepts_every_argv_the_table_builds(surface: _Surface, name: str) -> None:
    """The seam a table of literals cannot check itself: the shipped parser accepts the argv.

    Parsed rather than run, so nothing is written. Without this the table could name a flag the
    CLI dropped two releases ago and every assertion above it would still pass.
    """
    planned = surface.plan(ORIGIN, 1, _form(surface, name, **SUBMISSIONS[name]))
    assert isinstance(planned, tuple)
    parsed = cli._build_parser().parse_args(list(planned[1:]))
    assert parsed.command == VERBS[name][0]


# --- AC 3: the confirm-code boundary ---------------------------------------
#
# AC 2's contract lives with the gate that owns it: `test_import_contracts.py` injects
# `board_actions -> policy` into a staged package and asserts `lint-imports` refuses it, which
# is a negative control rather than a re-reading of the config file.


def test_no_action_path_reads_any_file_let_alone_the_confirm_codes(
    surface: _Surface, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 3, first instrument: every filesystem read the surface could make is recorded.

    The whole approve path runs - panel, then respond - with a real code in the file, and
    nothing is opened at all, which is stronger than the embargo needs. The control then reads
    that same file through `policy`, because an empty recording is otherwise ambiguous between
    "read nothing" and "watched the wrong call".
    """
    minted = tmp_path / policy._CONFIRM_FILE
    minted.parent.mkdir(parents=True, exist_ok=True)
    minted.write_text(json.dumps({"x-1:ship": {"code": PLANTED, "expires": 9e9}}), encoding="utf-8")
    opened: list[str] = []
    real = Path.open

    def spy(self: Path, *args: object, **kwargs: object) -> Any:
        opened.append(str(self))
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", spy)
    surface.panel()
    surface.respond(
        origin=ORIGIN,
        port=1,
        body=_form(surface, "checkpoint-approve", issue="x-1", name="ship", confirm=PLANTED),
    )
    assert opened == []

    assert policy._read_confirms(minted)
    assert [path for path in opened if "checkpoint-confirms" in path]


def test_no_confirm_code_reaches_the_page_the_reply_or_the_audit_line(surface: _Surface) -> None:
    """AC 3, second instrument, and the record's own criterion: the field is drawn empty.

    Asserted as *no* `value` attribute rather than as an empty one: `value=""` and a missing
    attribute render identically, and only the second says no code path could have filled it.
    """
    panel = surface.panel()
    assert PLANTED not in panel
    assert 'name="confirm" autocomplete="off" required' in panel
    assert "value=" not in panel.split('name="confirm"')[1].split(">")[0]

    body = _form(surface, "checkpoint-approve", issue="x-1", name="ship", confirm=PLANTED)
    reply = surface.respond(origin=ORIGIN, port=1, body=body)
    assert PLANTED not in reply.text
    assert board_actions.REDACTED in reply.text
    assert not [line for line in surface.spy.said if PLANTED in line]
    assert surface.spy.calls[0][-1] == PLANTED


# --- AC 4: an empty confirm code refuses without invoking anything ----------


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
    """AC 4, and the record's own criterion: an empty field is a refusal, not a challenge.

    Whitespace counts as empty on the kill, because a field a finger brushed is what a bare
    `if not value` on the raw string lets through.
    """
    reply = surface.respond(origin=ORIGIN, port=1, body=_form(surface, name, **fields))
    assert reply.status == HTTPStatus.BAD_REQUEST
    assert "confirm code is empty" in reply.text
    assert surface.spy.calls == []


def test_a_field_that_could_arrive_as_a_flag_is_refused(surface: _Surface) -> None:
    """The trust boundary: an issue id is not free text, so it cannot smuggle `--discard`."""
    body = _form(surface, "lane-kill", issue="--discard", reason="x", confirm="abc123")
    reply = surface.respond(origin=ORIGIN, port=1, body=body)
    assert reply.status == HTTPStatus.BAD_REQUEST
    assert "not an identifier" in reply.text
    assert surface.spy.calls == []


# --- AC 5: origin and token -------------------------------------------------


@pytest.mark.parametrize("origin", [None, "http://evil.example", "http://127.0.0.1:2"])
def test_a_submission_from_another_origin_is_forbidden_and_invokes_nothing(
    surface: _Surface, origin: str | None
) -> None:
    """AC 5: a page in another tab, and a page on this same host at another port."""
    body = _form(surface, "loop-answer", decision_id="x-1#abc", text="yes")
    reply = surface.respond(origin=origin, port=1, body=body)
    assert reply.status == HTTPStatus.FORBIDDEN
    assert surface.spy.calls == []


def test_a_submission_carrying_another_processs_token_is_forbidden(surface: _Surface) -> None:
    """AC 5: the token is per process, so a page kept from an earlier run cannot drive this one."""
    stale = _Surface(surface.repo_root)
    reply = surface.respond(
        origin=ORIGIN, port=1, body=_form(stale, "loop-answer", decision_id="x-1#abc", text="yes")
    )
    assert reply.status == HTTPStatus.FORBIDDEN
    assert surface.spy.calls == []
    assert stale.token != surface.token


# --- AC 6: the terminal is the audit log ------------------------------------


def test_every_invocation_is_echoed_before_it_runs_and_after_it_returns(surface: _Surface) -> None:
    """AC 6: two lines per action, the second carrying the exit code the CLI gave it."""
    body = _form(surface, "loop-answer", decision_id="x-1#abc", text="yes")
    reply = surface.respond(origin=ORIGIN, port=1, body=body)

    assert len(surface.spy.said) == 2
    assert surface.spy.said[0].startswith("board: action   running ")
    assert surface.spy.said[1].startswith("board: action   exit 0 from ")
    assert "loop answer" in surface.spy.said[0]
    assert "exit 0" in reply.text
    assert surface.spy.cwds == [surface.repo_root]


# --- AC 7: a read-only board registers no route at all ----------------------


def test_a_read_only_board_answers_405_and_draws_no_panel(work_repo: Path) -> None:
    """AC 7: with no surface there is no route and no affordance, which is `--no-actions`."""
    for board in _running(board_serve.bind(work_repo, port=0, build=_document, actions=False)):
        assert board.board.actions is None
        status, _text = _post(f"{board.url}{board_actions.ROUTE}", b"", origin=None)
        assert status == HTTPStatus.METHOD_NOT_ALLOWED
        page = urllib.request.urlopen(board.url + "/", timeout=TIMEOUT_S).read()
        assert b"board-actions" not in page


def test_the_no_actions_flag_exists_and_the_default_is_actions_on() -> None:
    """AC 7 at the argv seam: the flag reaches `board serve`, and its absence leaves actions on."""
    parser = cli._build_parser()
    assert parser.parse_args(["board", "serve"]).no_actions is False
    assert parser.parse_args(["board", "serve", "--no-actions"]).no_actions is True


# --- The wire: a served board, end to end -----------------------------------


def test_the_served_page_carries_one_panel_whose_confirm_field_is_empty(
    served: board_serve.Listener,
) -> None:
    """The record's acceptance criterion over the wire: the field a human fills is drawn empty."""
    surface = served.board.actions
    assert surface is not None
    page = urllib.request.urlopen(served.url + "/", timeout=TIMEOUT_S).read().decode("utf-8")

    assert page.count('id="board-actions"') == 1
    assert page.count(f'value="{surface.token}"') == len(board_actions.ACTIONS)
    assert 'name="confirm" autocomplete="off" required>' in page
    assert page.index("board-actions") < page.index("</body>")


def test_the_wire_refuses_an_empty_code_and_accepts_a_typed_one(
    served: board_serve.Listener,
) -> None:
    """AC 4 and AC 6 through the socket, with the runner replaced so nothing is written.

    The accepted case asserts the code reached the argv and did not reach the reply, which is
    the pair the whole boundary rests on.
    """
    surface = served.board.actions
    assert surface is not None
    spy = _Spy()
    surface._run = spy
    route = f"{served.url}{board_actions.ROUTE}"

    empty = _form(surface, "checkpoint-approve", issue="x-1", name="ship", confirm="")
    status, text = _post(route, empty, origin=served.url)
    assert status == HTTPStatus.BAD_REQUEST
    assert "confirm code is empty" in text
    assert spy.calls == []

    typed = _form(surface, "checkpoint-approve", issue="x-1", name="ship", confirm=PLANTED)
    status, text = _post(route, typed, origin=served.url)
    assert status == HTTPStatus.OK
    assert "APPROVED" in text
    assert PLANTED not in text
    assert spy.calls[0][-1] == PLANTED


def test_a_post_to_the_page_route_is_still_405_on_an_acting_board(
    served: board_serve.Listener,
) -> None:
    """Only the action route takes a POST; the page route says so, with an `Allow`."""
    request = urllib.request.Request(served.url + "/", data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=TIMEOUT_S)
    assert refused.value.code == HTTPStatus.METHOD_NOT_ALLOWED
    assert refused.value.headers["Allow"] == "GET"

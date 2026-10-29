from __future__ import annotations

import re
import subprocess  # nosec B404
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlencode

from basicly import board_action_surface, board_asks, tracker
from tests.flipped_tracker import flipped_repo, seed
from tests.test_board_asks import _page

ORIGIN = "http://127.0.0.1:1"


def _body(surface: board_action_surface.ActionSurface, **fields: str) -> bytes:
    form = {"token": surface.token, "action": "record-priority", **fields}
    return urlencode(form).encode("utf-8")


def _priority(repo: Path, record: str) -> object:
    kit = tracker.kit(repo)
    found = kit.read_ledger(tracker.ledger_dir(repo))
    return kit.events.fold(found).records[record].fields.get("priority")


def test_the_board_changes_a_record_priority_through_the_installed_cli(tmp_path: Path) -> None:
    repo = flipped_repo(tmp_path)
    for argv in (["init", "-q"], ["config", "user.name", "Op"], ["config", "user.email", "o@e.x"]):
        subprocess.run(["git", *argv], cwd=repo, check=True)  # nosec B603 B607
    seed(repo, "x-1", title="ranked wrong")
    surface = board_action_surface.ActionSurface(repo, echo=lambda _line: None)

    outcome = surface.respond(origin=ORIGIN, port=1, body=_body(surface, issue="x-1", priority="0"))

    assert outcome.status == HTTPStatus.OK
    assert "exit 0" in outcome.text, outcome.text
    assert _priority(repo, "x-1") == 0


def test_a_priority_the_tracker_refuses_is_refused_before_anything_runs(tmp_path: Path) -> None:
    ran: list[tuple[str, ...]] = []
    surface = board_action_surface.ActionSurface(
        tmp_path, run=lambda argv, _cwd: (ran.append(argv), (0, ""))[1], echo=lambda _line: None
    )

    outcome = surface.respond(origin=ORIGIN, port=1, body=_body(surface, issue="x-1", priority="7"))

    assert outcome.status == HTTPStatus.BAD_REQUEST
    assert "priority must be one of 0, 1, 2, 3, 4" in outcome.text
    assert ran == []


def test_every_ready_row_offers_its_own_priority_with_the_command_it_runs() -> None:
    page = _page([])
    body = page[page.index('class="region ready') :]
    rows = re.findall(r'<td class="id clip">(?:<a[^>]*>)?(basicly-[\w.]+)(?:</a>)?</td>', body)
    armed = re.findall(
        r'value="record-priority">\s*<input type="hidden" name="issue" value="(basicly-[\w.]+)"',
        body,
    )
    commands = re.findall(r'title="basicly tracker write -- update (basicly-[\w.]+) -p ', body)

    assert rows, "the fixture draws no ready rows, so this proves nothing"
    assert armed == rows
    assert commands == rows
    assert board_asks.PRIORITIES == ("0", "1", "2", "3", "4")

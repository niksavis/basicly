from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import test_tracker_process_evidence

cli = test_tracker_process_evidence.cli
_run = test_tracker_process_evidence._run

CRITERION = "When a task is saved, show shall return its title."
PAGE = Path(__file__).resolve().parents[1] / ".basicly/core/kit/board/web/index.html"


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    return tmp_path / "ledger"


def _record(
    ledger: Path, capsys: pytest.CaptureFixture[str], title: str, kind: str = "task"
) -> str:
    args = ["create", str(ledger), "--prefix", "demo", "--title", title]
    args += ["--description", "## Context\n\nA record.", "--acceptance", CRITERION]
    args += ["--field", f"issue_type={kind}", "--field", "size=small"]
    code, saved = _run(capsys, *args)
    assert code == cli.EXIT_OK, saved
    return saved["record"]


def _link(
    ledger: Path, capsys: pytest.CaptureFixture[str], record: str, target: str, kind: str = "blocks"
) -> None:
    code, saved = _run(capsys, "dep", str(ledger), record, target, "--type", kind)
    assert code == cli.EXIT_OK, saved


def _complete(ledger: Path, capsys: pytest.CaptureFixture[str], record: str) -> None:
    check = {"criterion": CRITERION, "command": ["uv", "run", "pytest", "tests/test_save.py"]}
    evidence = json.dumps({"checks": [{**check, "result": "1 passed", "exit_code": 0}]})
    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_OK
    assert _run(capsys, "confirm", str(ledger), record, "--evidence", evidence)[0] == cli.EXIT_OK
    assert _run(capsys, "close", str(ledger), record, "--reason", "Done.")[0] == cli.EXIT_OK


def _cancel(ledger: Path, capsys: pytest.CaptureFixture[str], record: str) -> None:
    args = ["close", str(ledger), record, "--resolution", "cancelled", "--reason", "Dropped."]
    code, saved = _run(capsys, *args)
    assert code == cli.EXIT_OK, saved


def _shown(ledger: Path, record: str) -> dict:
    shown = cli.read_record(ledger, record)
    assert shown is not None
    return shown


def _ids(rows: list[dict]) -> list[str]:
    return [row["id"] for row in rows]


def test_the_epic_path_runs_from_the_root_down_to_the_parent(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    epic = _record(ledger, capsys, "ship the app", "epic")
    feature = _record(ledger, capsys, "save tasks", "feature")
    task = _record(ledger, capsys, "save a title")
    _link(ledger, capsys, feature, epic, "parent-child")
    _link(ledger, capsys, task, feature, "parent-child")

    path = _shown(ledger, task)["ancestors"]

    assert _ids(path) == [epic, feature]
    assert [row["issue_type"] for row in path] == ["epic", "feature"]
    assert path[0]["title"] == "ship the app"


def test_the_wait_chain_reaches_the_blocker_that_can_start_now(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _record(ledger, capsys, "first")
    second = _record(ledger, capsys, "second")
    third = _record(ledger, capsys, "third")
    _link(ledger, capsys, first, second)
    _link(ledger, capsys, second, third)

    waits = _shown(ledger, first)["waits"]

    assert _ids(waits) == [second]
    assert waits[0]["startable"] is False
    assert _ids(waits[0]["waits"]) == [third]
    assert waits[0]["waits"][0]["startable"] is True


def test_a_cancelled_blocker_still_holds_and_a_completed_one_does_not(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _record(ledger, capsys, "record")
    done = _record(ledger, capsys, "done")
    dropped = _record(ledger, capsys, "dropped")
    _link(ledger, capsys, record, done)
    _link(ledger, capsys, record, dropped)
    _complete(ledger, capsys, done)
    _cancel(ledger, capsys, dropped)

    shown = _shown(ledger, record)

    assert _ids(shown["waits"]) == [dropped]
    assert shown["waits"][0]["close_resolution"] == "cancelled"


def test_an_epic_waits_on_its_open_children(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    epic = _record(ledger, capsys, "epic", "epic")
    open_child = _record(ledger, capsys, "open child")
    done_child = _record(ledger, capsys, "done child")
    _link(ledger, capsys, open_child, epic, "parent-child")
    _link(ledger, capsys, done_child, epic, "parent-child")
    _complete(ledger, capsys, done_child)

    waits = _shown(ledger, epic)["waits"]

    assert _ids(waits) == [open_child]
    assert waits[0]["via"] == "child"


def test_a_record_names_what_its_epic_waits_on_apart_from_its_own_waits(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    epic = _record(ledger, capsys, "epic", "epic")
    story = _record(ledger, capsys, "story")
    outside = _record(ledger, capsys, "outside")
    _link(ledger, capsys, story, epic, "parent-child")
    _link(ledger, capsys, epic, outside)

    shown = _shown(ledger, story)

    assert shown["waits"] == []
    assert shown["parent_waits"]["id"] == epic
    assert _ids(shown["parent_waits"]["waits"]) == [outside]


def test_a_shared_blocker_is_expanded_once(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    top = _record(ledger, capsys, "top")
    left = _record(ledger, capsys, "left")
    right = _record(ledger, capsys, "right")
    shared = _record(ledger, capsys, "shared")
    deeper = _record(ledger, capsys, "deeper")
    for record, target in ((top, left), (top, right), (left, shared), (right, shared)):
        _link(ledger, capsys, record, target)
    _link(ledger, capsys, shared, deeper)

    waits = {row["id"]: row for row in _shown(ledger, top)["waits"]}
    reached = [waits[left]["waits"][0], waits[right]["waits"][0]]

    assert [row["id"] for row in reached] == [shared, shared]
    assert sorted(row["repeat"] for row in reached) == [False, True]
    expanded = next(row for row in reached if not row["repeat"])
    assert _ids(expanded["waits"]) == [deeper]
    assert next(row for row in reached if row["repeat"])["waits"] == []


def test_the_page_reads_the_fields_the_record_view_writes(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    page = PAGE.read_text(encoding="utf-8")
    shown = _shown(ledger, _record(ledger, capsys, "record"))

    for field in ("ancestors", "waits", "parent_waits"):
        assert field in shown
        assert f"shown.{field}" in page
    for field in ("startable", "repeat", "via", "close_resolution"):
        assert f"row.{field}" in page


def test_the_list_marks_a_cancelled_or_missing_blocker_as_still_holding(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = _record(ledger, capsys, "record")
    done = _record(ledger, capsys, "done")
    dropped = _record(ledger, capsys, "dropped")
    _link(ledger, capsys, record, done)
    _link(ledger, capsys, record, dropped)
    edge = {
        cli.migrate.EDGE_FROM: record,
        cli.migrate.EDGE_TO: "demo-ghost",
        cli.migrate.EDGE_TYPE: "blocks",
    }
    cli.events.append(ledger, [cli.events.Draft(record, cli.migrate.KIND_EDGE, edge)])
    _complete(ledger, capsys, done)
    _cancel(ledger, capsys, dropped)

    rows = cli.queries.query_records(ledger, summary=True)
    edges = {
        edge["id"]: edge for edge in next(r for r in rows if r["record"] == record)["dependencies"]
    }

    assert {name: edge["holds"] for name, edge in edges.items()} == {
        done: False,
        dropped: True,
        "demo-ghost": True,
    }
    assert "edge.holds" in PAGE.read_text(encoding="utf-8")

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from basicly import cli, tracker, tracker_query
from tests import flipped_tracker

ROOT = "tq-1"


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"a tracker read spawned a process: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.fixture
def backlog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:

    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, ROOT, title="the root")
    first = tracker.create_record(
        repo, ["create", "parse it", "-t", "task", "-p", "1", "--parent", ROOT, "--json"]
    )
    second = tracker.create_record(
        repo, ["create", "render it", "-t", "task", "-p", "2", "--parent", ROOT, "--json"]
    )
    tracker.write(repo, ["dep", "add", second, first, "-t", "blocks"])
    monkeypatch.chdir(repo)
    return repo


def _json_out(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _subcommands_of(parser: argparse.ArgumentParser, group: str) -> set[str]:

    choices: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            found = action.choices.get(group)
            if found is None:
                continue
            for nested in found._actions:
                if isinstance(nested, argparse._SubParsersAction):
                    choices |= set(nested.choices)
    return choices


@pytest.mark.usefixtures("backlog")
def test_ready_lists_only_the_record_that_can_be_worked_now(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["tracker", "ready", "--json"]) == 0

    report = _json_out(capsys)

    assert [row["record"] for row in report["records"]] == [f"{ROOT}.1"]
    assert report["sort"]
    assert report["schema"]


@pytest.mark.usefixtures("backlog")
def test_ready_honours_the_limit_it_was_given(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["tracker", "ready", "--json", "--limit", "0"]) == 0

    assert _json_out(capsys)["records"] == []


@pytest.mark.usefixtures("backlog")
def test_blocked_names_the_open_blocker_and_the_decomposition_apart(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["tracker", "blocked", "--json"]) == 0

    rows = {row["record"]: row for row in _json_out(capsys)["records"]}

    assert rows[ROOT]["children"] == [f"{ROOT}.1", f"{ROOT}.2"]
    assert rows[ROOT]["blocked_by"] == []
    assert rows[f"{ROOT}.2"]["blocked_by"] == [{"record": f"{ROOT}.1", "status": "open"}]
    assert rows[f"{ROOT}.2"]["children"] == []


def test_closing_the_blocker_moves_its_dependent_into_the_ready_set(
    backlog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tracker.write(backlog, ["close", f"{ROOT}.1", "--reason", "landed"])

    assert cli.main(["tracker", "ready", "--json"]) == 0

    assert [row["record"] for row in _json_out(capsys)["records"]] == [f"{ROOT}.2"]


@pytest.mark.usefixtures("backlog")
def test_stats_totals_the_graph_by_status(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["tracker", "stats", "--json"]) == 0

    report = _json_out(capsys)

    assert report["records"] == 3
    assert report["by_status"] == {"open": 3}
    assert (report["ready"], report["blocked"]) == (1, 2)


@pytest.mark.usefixtures("backlog")
def test_show_prints_the_record_and_refuses_an_absent_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["tracker", "show", f"{ROOT}.1"]) == 0
    assert _json_out(capsys)["fields"]["title"] == "parse it"

    assert cli.main(["tracker", "show", "tq-nope"]) == 1
    assert _json_out(capsys)["found"] is False


def test_list_narrows_by_status(backlog: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["tracker", "list"]) == 0
    assert _json_out(capsys)["count"] == 3

    tracker.write(backlog, ["close", f"{ROOT}.1", "--reason", "landed"])

    assert cli.main(["tracker", "list", "--status", "closed"]) == 0
    assert [row["record"] for row in _json_out(capsys)["records"]] == [f"{ROOT}.1"]


@pytest.mark.usefixtures("backlog")
def test_a_table_is_printed_when_json_was_not_asked_for(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["tracker", "ready"]) == 0

    out = capsys.readouterr().out
    assert "Ready" in out
    assert f"{ROOT}.1" in out
    assert not out.lstrip().startswith("{")


def test_a_repository_with_no_tracker_is_refused_rather_than_answered_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert cli.main(["tracker", "stats"]) != 0
    assert "not installed" in capsys.readouterr().err


@pytest.mark.usefixtures("backlog")
def test_every_declared_verb_has_a_handler() -> None:

    declared = _subcommands_of(cli._build_parser(), "tracker")

    assert set(tracker_query.HANDLERS) <= declared


@pytest.mark.usefixtures("backlog")
def test_show_carries_both_directions_of_the_dependency_graph(
    capsys: pytest.CaptureFixture[str],
) -> None:

    assert cli.main(["tracker", "show", ROOT]) == 0

    shown = _json_out(capsys)

    assert shown["dependencies"] == []
    assert shown["dependents"] == [
        {
            "id": f"{ROOT}.1",
            "dependency_type": "parent-child",
            "status": "open",
            "title": "parse it",
        },
        {
            "id": f"{ROOT}.2",
            "dependency_type": "parent-child",
            "status": "open",
            "title": "render it",
        },
    ]

    assert cli.main(["tracker", "show", f"{ROOT}.2"]) == 0

    held = _json_out(capsys)

    assert held["dependents"] == []
    assert {(row["id"], row["dependency_type"], row["status"]) for row in held["dependencies"]} == {
        (ROOT, "parent-child", "open"),
        (f"{ROOT}.1", "blocks", "open"),
    }


def test_a_record_with_no_edges_prints_both_keys_empty(
    backlog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    flipped_tracker.seed(backlog, "tq-2", title="alone")

    assert cli.main(["tracker", "show", "tq-2"]) == 0

    shown = _json_out(capsys)

    assert (shown["dependencies"], shown["dependents"]) == ([], [])


def test_the_kit_and_the_engine_render_one_edge_shape(backlog: Path) -> None:

    ledger = tracker.ledger_dir(backlog)
    kit = tracker.kit(backlog)
    kit.events.append(
        ledger,
        [
            kit.events.Draft(
                f"{ROOT}.1",
                kit.migrate.KIND_EDGE,
                {
                    kit.migrate.EDGE_FROM: f"{ROOT}.1",
                    kit.migrate.EDGE_TO: "tq-ghost",
                    kit.migrate.EDGE_TYPE: "blocks",
                },
            )
        ],
    )
    kit_cli = tracker.kit(backlog, "cli")

    for record in (ROOT, f"{ROOT}.1", f"{ROOT}.2"):
        kit_shown = kit_cli.read_record(ledger, record)
        engine = tracker.owned_record(backlog, record)
        assert engine is not None
        assert kit_shown["dependencies"] == engine["dependencies"], record
        assert kit_shown["dependents"] == engine["dependents"], record
        if record == f"{ROOT}.1":
            assert {"id": "tq-ghost", "dependency_type": "blocks", "status": "unknown"} in engine[
                "dependencies"
            ]


def test_one_relation_stated_by_two_events_shows_as_one_row(
    backlog: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    ledger = tracker.ledger_dir(backlog)
    kit = tracker.kit(backlog)
    child = f"{ROOT}.1"

    landed = kit.events.append(
        ledger,
        [
            kit.events.Draft(
                child,
                kit.migrate.KIND_EDGE,
                {
                    kit.migrate.EDGE_FROM: child,
                    kit.migrate.EDGE_TO: ROOT,
                    kit.migrate.EDGE_TYPE: "parent-child",
                    kit.migrate.ASSERTED_AT_KEY: "2026-08-16T15:27:37.836780869Z",
                    kit.migrate.ASSERTED_BY_KEY: "an-importer",
                },
            )
        ],
    )
    assert len(landed) == 1

    assert cli.main(["tracker", "show", ROOT]) == 0
    shown = [row for row in _json_out(capsys)["dependents"] if row["id"] == child]
    assert cli.main(["tracker", "show", child]) == 0
    held = _json_out(capsys)["dependencies"]

    assert len(shown) == 1
    assert held == [{"id": ROOT, "dependency_type": "parent-child", "status": "open"}]

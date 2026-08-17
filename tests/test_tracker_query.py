"""``basicly tracker`` read verbs, over a real ledger (basicly-vkh0.42.7).

Every test drives the verb through :func:`basicly.cli.main`, which is the surface a human
and an agent both use, and reads what it printed. Nothing is stubbed: the whole point of
these verbs is that the engine resolves the ledger's location itself, so a test that
handed one in would exercise the argument it exists to remove.

A spawn fails the test. "The store could not answer and the verb printed an empty
backlog" would satisfy a weaker assertion and is exactly what a read seam can do wrong.
"""

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
    """Any process start is the test's failure rather than a fallback."""

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"a tracker read spawned a process: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.fixture
def backlog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A decomposed root with two children, the second blocked on the first.

    The smallest graph in which every verb says something different: the root is an
    anchor, one child is ready, the other is held.
    """
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
    """The subcommand names *group* declares, read off the built parser.

    Reached through argparse's private action list because it is the only place the
    parser records what it accepts; asserting against a second hand-written list is
    what this test exists to avoid.
    """
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
    """The ordered backlog: a decomposed parent is an anchor and a blocked child waits."""
    assert cli.main(["tracker", "ready", "--json"]) == 0

    report = _json_out(capsys)

    assert [row["record"] for row in report["records"]] == [f"{ROOT}.1"]
    # The policy travels with the answer: a rank recorded without it cannot be read back.
    assert report["sort"]
    assert report["schema"]


@pytest.mark.usefixtures("backlog")
def test_ready_honours_the_limit_it_was_given(capsys: pytest.CaptureFixture[str]) -> None:
    """The control on the query above: the filter is the limit, not an empty ready set."""
    assert cli.main(["tracker", "ready", "--json", "--limit", "0"]) == 0

    assert _json_out(capsys)["records"] == []


@pytest.mark.usefixtures("backlog")
def test_blocked_names_the_open_blocker_and_the_decomposition_apart(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two reasons, because the repair differs: finish the blocker, or work the children."""
    assert cli.main(["tracker", "blocked", "--json"]) == 0

    rows = {row["record"]: row for row in _json_out(capsys)["records"]}

    assert rows[ROOT]["children"] == [f"{ROOT}.1", f"{ROOT}.2"]
    assert rows[ROOT]["blocked_by"] == []
    assert rows[f"{ROOT}.2"]["blocked_by"] == [{"record": f"{ROOT}.1", "status": "open"}]
    assert rows[f"{ROOT}.2"]["children"] == []


def test_closing_the_blocker_moves_its_dependent_into_the_ready_set(
    backlog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ordering the backlog exists to express, read back through the verbs."""
    tracker.write(backlog, ["close", f"{ROOT}.1", "--reason", "landed"])

    assert cli.main(["tracker", "ready", "--json"]) == 0

    assert [row["record"] for row in _json_out(capsys)["records"]] == [f"{ROOT}.2"]


@pytest.mark.usefixtures("backlog")
def test_stats_totals_the_graph_by_status(capsys: pytest.CaptureFixture[str]) -> None:
    """The one query that answers what state the backlog is in."""
    assert cli.main(["tracker", "stats", "--json"]) == 0

    report = _json_out(capsys)

    assert report["records"] == 3
    assert report["by_status"] == {"open": 3}
    assert (report["ready"], report["blocked"]) == (1, 2)


@pytest.mark.usefixtures("backlog")
def test_show_prints_the_record_and_refuses_an_absent_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``found: false`` reads exactly like a record with no body, so the code says which."""
    assert cli.main(["tracker", "show", f"{ROOT}.1"]) == 0
    assert _json_out(capsys)["fields"]["title"] == "parse it"

    assert cli.main(["tracker", "show", "tq-nope"]) == 1
    assert _json_out(capsys)["found"] is False


def test_list_narrows_by_status(backlog: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The set, filtered — with the unfiltered count beside it as the control."""
    assert cli.main(["tracker", "list"]) == 0
    assert _json_out(capsys)["count"] == 3

    tracker.write(backlog, ["close", f"{ROOT}.1", "--reason", "landed"])

    assert cli.main(["tracker", "list", "--status", "closed"]) == 0
    assert [row["record"] for row in _json_out(capsys)["records"]] == [f"{ROOT}.1"]


@pytest.mark.usefixtures("backlog")
def test_a_table_is_printed_when_json_was_not_asked_for(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The default is for a human: a caller scripting this passes ``--json``."""
    assert cli.main(["tracker", "ready"]) == 0

    out = capsys.readouterr().out
    assert "Ready" in out
    assert f"{ROOT}.1" in out
    assert not out.lstrip().startswith("{")


def test_a_repository_with_no_tracker_is_refused_rather_than_answered_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty answer would read as an empty backlog, which is the fail-open direction."""
    monkeypatch.chdir(tmp_path)

    assert cli.main(["tracker", "stats"]) != 0
    assert "not installed" in capsys.readouterr().err


@pytest.mark.usefixtures("backlog")
def test_every_declared_verb_has_a_handler() -> None:
    """The parser and the dispatch table are two lists, and a verb in one only is dead.

    A verb the parser accepts and nothing handles falls through to the group's own
    refusal, which reads as an argument error rather than as a missing implementation.
    """
    declared = _subcommands_of(cli._build_parser(), "tracker")

    assert set(tracker_query.HANDLERS) <= declared

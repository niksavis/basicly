"""The tracker kit's write and query operations (basicly-vkh0.28, basicly-wpc8).

`test_kit_tracker_cli.py` owns the isolation claim — that a repository holding only the
kit can run it — and walks the whole graph through the entry point under an engine
blocker. This module owns each operation's own contract: what it writes, what it refuses,
and what a query answers about the set.

In process rather than through a subprocess, because these are facts about a function
rather than about an environment; the isolation claim is proven once, next door.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    """Load a standalone kit module by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


commands = _load(KIT_DIR / "commands.py", "tracker_commands")
# The modules `commands` itself loaded, never second copies: two loads of one file give
# two `Event` classes and a record written through one is not the type the other reads.
queries = commands.queries
events = commands.events


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """A ledger holding one root record, opened through the kit's own create."""
    directory = tmp_path / "ledger"
    commands.create_root(directory, {"title": "root"}, prefix="acme")
    return directory


def root_of(ledger: Path) -> str:
    """The only root record id in *ledger*."""
    (record,) = [key for key in queries.folded(ledger) if "." not in key]
    return record


def labels(ledger: Path, record: str) -> tuple:
    """*record*'s labels, split back out of whichever shape stored them."""
    return commands.labels_of(queries.folded(ledger)[record].fields.get("labels"))


# --- update ---------------------------------------------------------------------


def test_a_field_write_lands_under_the_name_it_was_given(ledger: Path) -> None:
    """The plain half of update, so the label half is not the only path exercised."""
    record = root_of(ledger)

    commands.update(ledger, record, fields={"priority": 1})

    assert queries.folded(ledger)[record].fields["priority"] == 1


def test_add_label_accumulates_against_the_set_the_record_already_holds(ledger: Path) -> None:
    """The whole reason a label write cannot be a plain field replacement."""
    record = root_of(ledger)

    commands.update(ledger, record, add_labels=["cut-a"])
    commands.update(ledger, record, add_labels=["cut-b"])

    assert labels(ledger, record) == ("cut-a", "cut-b")


def test_remove_label_drops_one_and_leaves_the_rest(ledger: Path) -> None:
    """A removal is the same read-modify-write, proven on the same path."""
    record = root_of(ledger)
    commands.update(ledger, record, add_labels=["cut-a,cut-b,cut-c"])

    commands.update(ledger, record, remove_labels=["cut-b"])

    assert labels(ledger, record) == ("cut-a", "cut-c")


def test_a_repeated_add_does_not_duplicate_the_label(ledger: Path) -> None:
    """A set, not a list: a reader matches by membership and a duplicate is noise."""
    record = root_of(ledger)

    commands.update(ledger, record, add_labels=["cut-a"])
    commands.update(ledger, record, add_labels=["cut-a"])

    assert labels(ledger, record) == ("cut-a",)


def test_an_update_asking_for_no_change_is_refused(ledger: Path) -> None:
    """A write that records nothing is indistinguishable from one that was lost."""
    with pytest.raises(events.LedgerError, match="no change"):
        commands.update(ledger, root_of(ledger))


def test_a_write_against_an_absent_record_is_refused(ledger: Path) -> None:
    """Otherwise it mints a record under a name nobody chose."""
    with pytest.raises(events.LedgerError, match="holds no record"):
        commands.update(ledger, "acme-nope", status="open")


# --- children, edges and the ready set -------------------------------------------


def test_a_child_nests_under_its_parent_and_carries_the_edge(ledger: Path) -> None:
    """The edge is what makes the parent read as decomposed rather than as the work."""
    record = root_of(ledger)

    child = commands.create_child(ledger, record, {"title": "a child"})[0].record

    assert child == record + ".1"
    views, children = queries.views_and_children(ledger)
    assert children[record] == [child]
    assert [edge.target for edge in views[child].dependencies] == [record]


def test_a_decomposed_parent_leaves_the_ready_set_and_its_child_enters_it(ledger: Path) -> None:
    """A parent with children is an anchor; the ready set is what can be worked now."""
    record = root_of(ledger)
    child = commands.create_child(ledger, record, {"title": "a child"})[0].record

    assert [row["record"] for row in queries.ready(ledger)["records"]] == [child]
    assert [row["record"] for row in queries.blocked(ledger)["records"]] == [record]


def test_a_blocking_edge_holds_the_dependent_until_the_blocker_closes(ledger: Path) -> None:
    """The ordering the backlog exists to express, end to end."""
    record = root_of(ledger)
    first = commands.create_child(ledger, record, {"title": "first"})[0].record
    second = commands.create_child(ledger, record, {"title": "second"})[0].record
    commands.add_dependency(ledger, second, first, edge_type="blocks")

    assert [row["record"] for row in queries.ready(ledger)["records"]] == [first]

    commands.close(ledger, [first], reason="landed")

    assert [row["record"] for row in queries.ready(ledger)["records"]] == [second]


def test_an_edge_into_a_record_the_ledger_does_not_hold_is_refused(ledger: Path) -> None:
    """An unknown blocker is unknown rather than satisfied, so it may not be recorded."""
    with pytest.raises(events.LedgerError, match="holds no record"):
        commands.add_dependency(ledger, root_of(ledger), "acme-nope", edge_type="blocks")


def test_an_edge_that_closes_a_cycle_is_refused(ledger: Path) -> None:
    """Every record on a cycle waits for another on it, so none is ever dispatchable."""
    record = root_of(ledger)
    first = commands.create_child(ledger, record, {"title": "first"})[0].record
    second = commands.create_child(ledger, record, {"title": "second"})[0].record
    commands.add_dependency(ledger, second, first, edge_type="blocks")

    with pytest.raises(events.LedgerError, match="closes a cycle"):
        commands.add_dependency(ledger, first, second, edge_type="blocks")


def test_a_crossing_of_two_edge_types_is_not_a_cycle(ledger: Path) -> None:
    """The control on the refusal above: a child that blocks its own parent is ordinary.

    The cycle walk is same-type only, so a `parent-child` path and a `blocks` path meeting
    is a shape the graph is meant to hold — refusing it would refuse a decomposition.
    """
    record = root_of(ledger)
    child = commands.create_child(ledger, record, {"title": "a child"})[0].record

    commands.add_dependency(ledger, record, child, edge_type="blocks")

    assert queries.blocked(ledger)["count"] >= 1


def test_a_blocker_the_ledger_does_not_hold_reads_as_unknown_not_as_satisfied(
    ledger: Path,
) -> None:
    """The direction to be wrong in: an unresolvable edge holds the dependent back.

    Written straight to the log rather than through :func:`commands.add_dependency`, which
    refuses this edge — the population is a ledger imported from elsewhere, and the read is
    what has to survive it.
    """
    record = root_of(ledger)
    events.append(
        ledger,
        [
            events.Draft(
                record,
                commands.migrate.KIND_EDGE,
                {
                    commands.migrate.EDGE_FROM: record,
                    commands.migrate.EDGE_TO: "acme-gone",
                    commands.migrate.EDGE_TYPE: "blocks",
                },
            )
        ],
    )

    (row,) = queries.blocked(ledger)["records"]
    assert row["blocked_by"] == [{"record": "acme-gone", "status": "unknown"}]


# --- comments, close and delete ---------------------------------------------------


def test_a_comment_is_appended_in_order(ledger: Path) -> None:
    """The work log: 45% of this repository's own tracker traffic is comments."""
    record = root_of(ledger)

    commands.comment(ledger, record, "first")
    commands.comment(ledger, record, "second")

    assert queries.folded(ledger)[record].comments == ["first", "second"]


def test_an_empty_comment_is_refused(ledger: Path) -> None:
    """It records nothing and is indistinguishable from one that was lost."""
    with pytest.raises(events.LedgerError, match="needs a body"):
        commands.comment(ledger, root_of(ledger), "")


def test_a_close_records_the_reason_beside_the_status(ledger: Path) -> None:
    """Why a record closed is the fact a later reader has no other way to get."""
    record = root_of(ledger)

    commands.close(ledger, [record], reason="shipped")

    state = queries.folded(ledger)[record]
    assert state.status == "closed"
    assert state.fields["close_reason"] == "shipped"


def test_a_delete_leaves_every_view_and_never_yields_its_id_again(ledger: Path) -> None:
    """An append-only log expresses a removal by keeping the record and flagging it."""
    record = root_of(ledger)
    child = commands.create_child(ledger, record, {"title": "a child"})[0].record

    commands.delete(ledger, child)

    assert queries.folded(ledger)[child].tombstoned
    assert queries.query_records(ledger) == [
        held for held in queries.query_records(ledger) if held["record"] != child
    ]
    assert commands.create_child(ledger, record, {"title": "next"})[0].record != child


def test_a_write_against_a_tombstoned_record_is_refused(ledger: Path) -> None:
    """A deleted record reads as absent, so a write to it has to refuse like an absent one."""
    record = root_of(ledger)
    child = commands.create_child(ledger, record, {"title": "a child"})[0].record
    commands.delete(ledger, child)

    with pytest.raises(events.LedgerError, match="holds no record"):
        commands.update(ledger, child, status="open")


# --- the totals -------------------------------------------------------------------


def test_stats_counts_by_status_and_leaves_the_tombstoned_out_of_the_total(
    ledger: Path,
) -> None:
    """The one query that answers what state the backlog is in."""
    record = root_of(ledger)
    first = commands.create_child(ledger, record, {"title": "first"})[0].record
    second = commands.create_child(ledger, record, {"title": "second"})[0].record
    commands.close(ledger, [first])
    commands.delete(ledger, second)

    report = commands.queries.stats(ledger)

    assert report["records"] == 2
    assert report["tombstoned"] == 1
    assert report["by_status"] == {"closed": 1, "open": 1}


def test_a_directory_that_is_not_a_ledger_is_refused_by_a_query(tmp_path: Path) -> None:
    """A mistyped path must not answer "no such record", which a correct path also gives."""
    with pytest.raises(events.LedgerError, match="not a ledger directory"):
        queries.stats(tmp_path / "nowhere")

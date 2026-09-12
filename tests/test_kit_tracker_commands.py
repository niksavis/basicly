from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


commands = _load(KIT_DIR / "commands.py", "tracker_commands")
queries = commands.queries
events = commands.events


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    directory = tmp_path / "ledger"
    commands.create_root(directory, {"title": "root"}, prefix="acme")
    return directory


def root_of(ledger: Path) -> str:
    (record,) = [key for key in queries.folded(ledger) if "." not in key]
    return record


def labels(ledger: Path, record: str) -> tuple:
    return commands.labels_of(queries.folded(ledger)[record].fields.get("labels"))


def test_a_field_write_lands_under_the_name_it_was_given(ledger: Path) -> None:
    record = root_of(ledger)

    commands.update(ledger, record, fields={"priority": 1})

    assert queries.folded(ledger)[record].fields["priority"] == 1


def test_add_label_accumulates_against_the_set_the_record_already_holds(ledger: Path) -> None:
    record = root_of(ledger)

    commands.update(ledger, record, add_labels=["cut-a"])
    commands.update(ledger, record, add_labels=["cut-b"])

    assert labels(ledger, record) == ("cut-a", "cut-b")


def test_remove_label_drops_one_and_leaves_the_rest(ledger: Path) -> None:
    record = root_of(ledger)
    commands.update(ledger, record, add_labels=["cut-a,cut-b,cut-c"])

    commands.update(ledger, record, remove_labels=["cut-b"])

    assert labels(ledger, record) == ("cut-a", "cut-c")


def test_a_repeated_add_does_not_duplicate_the_label(ledger: Path) -> None:
    record = root_of(ledger)

    commands.update(ledger, record, add_labels=["cut-a"])
    commands.update(ledger, record, add_labels=["cut-a"])

    assert labels(ledger, record) == ("cut-a",)


def test_an_update_asking_for_no_change_is_refused(ledger: Path) -> None:
    with pytest.raises(events.LedgerError, match="no change"):
        commands.update(ledger, root_of(ledger))


def test_a_write_against_an_absent_record_is_refused(ledger: Path) -> None:
    with pytest.raises(events.LedgerError, match="holds no record"):
        commands.update(ledger, "acme-nope", status="open")


def test_a_child_nests_under_its_parent_and_carries_the_edge(ledger: Path) -> None:
    record = root_of(ledger)

    child = commands.create_child(ledger, record, {"title": "a child"})[0].record

    assert child == record + ".1"
    views, children = queries.views_and_children(ledger)
    assert children[record] == [child]
    assert [edge.target for edge in views[child].dependencies] == [record]


def test_a_decomposed_parent_leaves_the_ready_set_and_its_child_enters_it(ledger: Path) -> None:
    record = root_of(ledger)
    child = commands.create_child(ledger, record, {"title": "a child"})[0].record

    assert [row["record"] for row in queries.ready(ledger)["records"]] == [child]
    assert [row["record"] for row in queries.blocked(ledger)["records"]] == [record]


def test_a_blocking_edge_holds_the_dependent_until_the_blocker_closes(ledger: Path) -> None:
    record = root_of(ledger)
    first = commands.create_child(ledger, record, {"title": "first"})[0].record
    second = commands.create_child(ledger, record, {"title": "second"})[0].record
    commands.add_dependency(ledger, second, first, edge_type="blocks")

    assert [row["record"] for row in queries.ready(ledger)["records"]] == [first]

    commands.close(ledger, [first], reason="landed")

    assert [row["record"] for row in queries.ready(ledger)["records"]] == [second]


def test_an_edge_into_a_record_the_ledger_does_not_hold_is_refused(ledger: Path) -> None:
    with pytest.raises(events.LedgerError, match="holds no record"):
        commands.add_dependency(ledger, root_of(ledger), "acme-nope", edge_type="blocks")


def test_an_edge_that_closes_a_cycle_is_refused(ledger: Path) -> None:
    record = root_of(ledger)
    first = commands.create_child(ledger, record, {"title": "first"})[0].record
    second = commands.create_child(ledger, record, {"title": "second"})[0].record
    commands.add_dependency(ledger, second, first, edge_type="blocks")

    with pytest.raises(events.LedgerError, match="closes a cycle"):
        commands.add_dependency(ledger, first, second, edge_type="blocks")


def test_a_crossing_of_two_edge_types_is_not_a_cycle(ledger: Path) -> None:

    record = root_of(ledger)
    child = commands.create_child(ledger, record, {"title": "a child"})[0].record

    commands.add_dependency(ledger, record, child, edge_type="blocks")

    assert queries.blocked(ledger)["count"] >= 1


def test_a_blocker_the_ledger_does_not_hold_reads_as_unknown_not_as_satisfied(
    ledger: Path,
) -> None:

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


def test_a_comment_is_appended_in_order(ledger: Path) -> None:
    record = root_of(ledger)

    commands.comment(ledger, record, "first")
    commands.comment(ledger, record, "second")

    assert queries.folded(ledger)[record].comments == ["first", "second"]


def test_prose_is_written_as_a_note_and_never_as_a_comment(ledger: Path) -> None:

    record = root_of(ledger)

    commands.comment(ledger, record, "written today")

    written = [event for event in events.read_events(ledger)[0] if event.payload.get("text")]
    assert [event.kind for event in written] == [events.KIND_NOTE]
    assert queries.folded(ledger)[record].comments == ["written today"]


def test_an_empty_comment_is_refused(ledger: Path) -> None:
    with pytest.raises(events.LedgerError, match="needs a body"):
        commands.comment(ledger, root_of(ledger), "")


def test_a_close_records_the_reason_beside_the_status(ledger: Path) -> None:
    record = root_of(ledger)

    commands.close(ledger, [record], reason="shipped")

    state = queries.folded(ledger)[record]
    assert state.status == "closed"
    assert state.fields["close_reason"] == "shipped"


def test_a_delete_leaves_every_view_and_never_yields_its_id_again(ledger: Path) -> None:
    record = root_of(ledger)
    child = commands.create_child(ledger, record, {"title": "a child"})[0].record

    commands.delete(ledger, child)

    assert queries.folded(ledger)[child].tombstoned
    assert queries.query_records(ledger) == [
        held for held in queries.query_records(ledger) if held["record"] != child
    ]
    assert commands.create_child(ledger, record, {"title": "next"})[0].record != child


def test_a_write_against_a_tombstoned_record_is_refused(ledger: Path) -> None:
    record = root_of(ledger)
    child = commands.create_child(ledger, record, {"title": "a child"})[0].record
    commands.delete(ledger, child)

    with pytest.raises(events.LedgerError, match="holds no record"):
        commands.update(ledger, child, status="open")


def test_stats_counts_by_status_and_leaves_the_tombstoned_out_of_the_total(
    ledger: Path,
) -> None:
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
    with pytest.raises(events.LedgerError, match="not a ledger directory"):
        queries.stats(tmp_path / "nowhere")

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
BOARD_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "board"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


commands = _load(KIT_DIR / "commands.py", "tracker_commands_record_types")
record_types = commands.values.record_types
queries = commands.queries
events = commands.events
SIX = ("task", "bug", "feature", "chore", "epic", "research")


def _root(ledger: Path, title: str, issue_type: str) -> str:

    before = set(queries.folded(ledger)) if ledger.exists() else set()
    commands.create_root(ledger, {"title": title, "issue_type": issue_type}, prefix="acme")
    (record,) = set(queries.folded(ledger)) - before
    return record


def _child(ledger: Path, parent: str, issue_type: str) -> str:

    before = set(queries.folded(ledger))
    commands.create_child(ledger, parent, {"title": issue_type, "issue_type": issue_type})
    (record,) = set(queries.folded(ledger)) - before
    return record


def test_the_six_record_types_are_declared_once() -> None:

    assert record_types.RECORD_TYPES == SIX
    assert set(record_types.CHILD_TYPES) == {"epic", "feature"}
    assert set(record_types.CHILD_TYPES["feature"]) == {"task", "bug", "chore"}


def test_an_unknown_record_type_is_refused_by_name(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    record = _root(ledger, "kept", "task")

    with pytest.raises(record_types.RefusedTypeError, match="use one of task, bug, feature"):
        commands.create_root(ledger, {"title": "x", "issue_type": "story"}, prefix="acme")
    with pytest.raises(record_types.RefusedTypeError, match="'docs' is not a record type"):
        commands.update(ledger, record, fields={"issue_type": "docs"})
    assert queries.folded(ledger)[record].fields["issue_type"] == "task"


def test_a_parent_the_hierarchy_forbids_is_refused(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    epic = _root(ledger, "epic", "epic")
    feature = _child(ledger, epic, "feature")
    task = _child(ledger, feature, "task")
    loose = _root(ledger, "loose research", "research")

    with pytest.raises(
        record_types.RefusedTypeError, match="a task may sit under an epic or a feature"
    ):
        commands.create_child(ledger, task, {"title": "sub", "issue_type": "task"})
    with pytest.raises(record_types.RefusedTypeError, match="a research may sit under an epic"):
        commands.add_dependency(ledger, loose, feature, edge_type="parent-child")
    with pytest.raises(record_types.RefusedTypeError, match="holds no task"):
        commands.update(ledger, feature, fields={"issue_type": "chore"})
    commands.add_dependency(ledger, loose, epic, edge_type="parent-child")


def test_a_record_without_a_type_is_not_held_to_the_hierarchy(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    commands.create_root(ledger, {"title": "untyped"}, prefix="acme")
    (parent,) = queries.folded(ledger)

    child = _child(ledger, parent, "task")

    assert child.startswith(parent + ".")


def test_a_legacy_link_still_folds_and_a_new_one_is_refused(tmp_path: Path) -> None:

    ledger = tmp_path / "ledger"
    outer = _root(ledger, "outer", "epic")
    commands.create_child(ledger, outer, {"title": "inner"})
    (inner,) = set(queries.folded(ledger)) - {outer}
    with events.LedgerLock(ledger) as lock:
        draft = events.Draft(inner, events.KIND_FIELD, {"name": "issue_type", "value": "epic"})
        events.append(
            ledger, [draft], actor=commands.writers.writer_class(os.environ), held_lock=lock
        )

    _views, children = queries.views_and_children(ledger)

    assert children[outer] == [inner]
    assert queries.folded(ledger)[inner].fields["issue_type"] == "epic"
    with pytest.raises(record_types.RefusedTypeError, match="holds no epic"):
        commands.create_child(ledger, outer, {"title": "new", "issue_type": "epic"})


@pytest.mark.live_ledger
def test_every_ledger_record_has_a_declared_type() -> None:

    found, quarantined = events.read_events(REPO_ROOT / ".basicly" / "ledger")
    assert quarantined == []
    states = events.fold(found).records
    undeclared = sorted(
        f"{record}={state.fields.get('issue_type')!r}"
        for record, state in states.items()
        if not state.tombstoned and state.fields.get("issue_type") not in SIX
    )

    assert len(states) > 1000
    assert undeclared == []


def test_the_page_reads_the_record_types_from_the_api(tmp_path: Path) -> None:

    server = _load(BOARD_DIR / "server.py", "basicly_tracker_kit_server")
    ledger = tmp_path / "ledger"
    _root(ledger, "one", "task")
    page = (BOARD_DIR / "web" / "index.html").read_text(encoding="utf-8")

    index = server.api_index(ledger)

    assert index["record_types"] == list(SIX)
    assert index["child_types"]["feature"] == ["task", "bug", "chore"]
    assert "index.record_types" in page
    assert "const TYPES" not in page


def test_the_edge_keys_match_the_ones_the_ledger_writes() -> None:

    migrate = commands.migrate
    vocabulary = commands.differential.DEFAULT_VOCABULARY

    assert (record_types.EDGE_FROM, record_types.EDGE_TO, record_types.EDGE_TYPE) == (
        migrate.EDGE_FROM,
        migrate.EDGE_TO,
        migrate.EDGE_TYPE,
    )
    assert vocabulary.parent_child_type == record_types.PARENT_CHILD
    assert record_types.events.KIND_EDGE == migrate.KIND_EDGE

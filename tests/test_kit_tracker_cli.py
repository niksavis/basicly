"""Tests for the tracker kit's standalone entry point (basicly-vkh0.28).

The acceptance criterion is about a **repository that never installed the engine**, so the
central test does not call :func:`cli.main` in this process at all. It copies the tracker
kit into a bare directory, runs the entry point there as a subprocess, and does it under a
meta-path finder that turns any ``import basicly`` into a failure — so "did not import the
engine" is enforced by the interpreter rather than asserted about an import list.

That blocker is worthless without its two controls, and both are tests here:

- ``test_the_engine_is_importable_in_the_same_environment`` — the subprocess runs the
  venv's own interpreter, where `basicly` is installed. Without this, a kit that quietly
  depended on the engine would pass simply because nothing could have imported it.
- ``test_the_blocker_fails_a_script_that_does_import_the_engine`` — the blocker is seeded
  with a module that imports the engine and has to turn red. A blocker that silently
  matched nothing is the fail-open shape this repo keeps paying for.

The rest run in process, because they are about the command surface rather than about
isolation. What is **not** asserted here, stated rather than left to be discovered: that
the mint and the append are one critical section. That property is a race, and this repo's
platform-hermetic rule is to assert by injection rather than by racing —
:func:`cli.create_record` builds its own lock and exposes no timeout to inject, so the
tests below assert only what follows from it (distinct ids across creates, and a released
lock afterwards).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
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


cli = _load(KIT_DIR / "cli.py", "tracker_cli")
# The modules the entry point itself loaded, never second copies: two loads of one file
# give two `Event` classes and a record written through one is not the type the other reads.
events = cli.events
snapshot = cli.snapshot

# Run a script with the engine barred. `find_spec` raises instead of returning None, so the
# failure names the module that was reached for and cannot be mistaken for "not installed".
_BLOCKER = '''\
import runpy
import sys


class RefuseEngine:
    """Turn any basicly import into a failure, wherever in the stack it happens."""

    def find_spec(self, name, path=None, target=None):
        if name == "basicly" or name.startswith("basicly."):
            raise AssertionError("the kit imported the engine: " + name)
        return None


sys.meta_path.insert(0, RefuseEngine())
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    """A repository that holds the tracker kit and nothing else — no engine, no install.

    Compiled caches are left behind: a copied ``__pycache__`` is bytecode this repo's own
    run produced, and importing it would weaken the claim that the copy is what ran.
    """
    shutil.copytree(KIT_DIR, tmp_path / "tracker", ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def _blocked(consumer: Path, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run *script* in *consumer* with the engine barred from being imported."""
    return subprocess.run(
        [sys.executable, "-c", _BLOCKER, str(script), *args],
        cwd=consumer,
        capture_output=True,
        text=True,
        check=False,
    )


# --- the two controls the isolation rests on ---------------------------------


def test_the_engine_is_importable_in_the_same_environment(consumer: Path) -> None:
    """Otherwise the blocked run below proves nothing about the kit."""
    proc = subprocess.run(
        [sys.executable, "-c", "import basicly; print(basicly.__name__)"],
        cwd=consumer,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "basicly"


def test_the_blocker_fails_a_script_that_does_import_the_engine(consumer: Path) -> None:
    """The instrument discriminates: a seeded engine import turns it red."""
    leak = consumer / "leak.py"
    leak.write_text("import basicly\n", encoding="utf-8")
    proc = _blocked(consumer, leak)
    assert proc.returncode != 0
    assert "the kit imported the engine: basicly" in proc.stderr


# --- the acceptance criterion ------------------------------------------------


def test_a_consumer_without_the_engine_creates_reads_and_queries(consumer: Path) -> None:
    """AC: the copied kit's entry point does all three, with no engine import."""
    entry = consumer / "tracker" / "cli.py"
    ledger = consumer / "ledger"

    created = _blocked(consumer, entry, "create", str(ledger), "--prefix", "acme", "--title", "a")
    assert created.returncode == 0, created.stderr
    assert created.stderr == ""
    record = json.loads(created.stdout)["record"]
    assert record.startswith("acme-")

    shown = _blocked(consumer, entry, "show", str(ledger), record)
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["fields"]["title"] == "a"

    queried = _blocked(consumer, entry, "list", str(ledger), "--status", "open")
    assert queried.returncode == 0, queried.stderr
    report = json.loads(queried.stdout)
    assert report["count"] == 1
    assert [held["record"] for held in report["records"]] == [record]


def test_a_consumer_without_the_engine_walks_a_whole_unit_of_work(consumer: Path) -> None:
    """AC: the kit is a *tracker*, not a log — decompose, order, rank, close, re-rank.

    The three verbs the kit shipped with could open a record and never advance one, so a
    repository that copied it had a backlog it could not work. This is the walk that says
    it can, and every step runs with the engine barred.
    """
    entry = consumer / "tracker" / "cli.py"
    ledger = str(consumer / "ledger")

    def kit(*args: str) -> dict:
        proc = _blocked(consumer, entry, args[0], ledger, *args[1:])
        assert proc.returncode == 0, proc.stderr or proc.stdout
        return json.loads(proc.stdout)

    root = kit("create", "--prefix", "acme", "--title", "ship it")["record"]
    first = kit("child", root, "--title", "parse", "--field", "priority=1")["record"]
    second = kit("child", root, "--title", "render", "--field", "priority=1")["record"]
    kit("dep", second, first, "--type", "blocks")
    kit("update", first, "--add-label", "cut-a")

    # The decomposed parent is not the work, and the blocked child is not ready either.
    assert [row["record"] for row in kit("ready")["records"]] == [first]
    assert {row["record"] for row in kit("blocked")["records"]} == {root, second}

    kit("close", first, "--reason", "landed")

    assert [row["record"] for row in kit("ready")["records"]] == [second]
    assert kit("stats")["by_status"] == {"closed": 1, "open": 2}


def test_the_consumer_writes_only_inside_its_own_directory(consumer: Path) -> None:
    """The ledger is an argument: nothing is written to a path the kit chose."""
    entry = consumer / "tracker" / "cli.py"
    proc = _blocked(consumer, entry, "create", str(consumer / "ledger"), "--prefix", "acme")
    assert proc.returncode == 0, proc.stderr
    # The log only: the snapshot is derived lazily by a *read*, so a create writes one file.
    written = {path.relative_to(consumer).as_posix() for path in consumer.rglob("*.jsonl")}
    assert written == {"ledger/events-0001.jsonl"}


# --- the command surface, in process -----------------------------------------


def test_create_appends_the_created_event_then_the_status_event(tmp_path: Path) -> None:
    """Status is its own kind, so a record written without one answers no query."""
    written = cli.create_record(tmp_path / "l", {"title": "a"}, prefix="acme")
    assert [event.kind for event in written] == [events.KIND_CREATED, events.KIND_STATUS]
    assert written[0].record == written[1].record
    assert written[1].payload["status"] == cli.DEFAULT_STATUS


def test_two_creates_mint_distinct_records(tmp_path: Path) -> None:
    """The second mint sees the first record, so it cannot be handed the same id."""
    ledger = tmp_path / "l"
    first = cli.create_record(ledger, {}, prefix="acme")[0].record
    second = cli.create_record(ledger, {}, prefix="acme")[0].record
    assert first != second
    assert sorted(snapshot.load(ledger).records) == sorted([first, second])


def test_the_ledger_lock_is_released_after_a_create(tmp_path: Path) -> None:
    """A create that left the lock behind would wedge every later writer for its timeout."""
    ledger = tmp_path / "l"
    cli.create_record(ledger, {}, prefix="acme")
    assert not (ledger / events.LOCK_NAME).exists()


def test_a_field_value_that_is_json_keeps_its_type(tmp_path: Path) -> None:
    """`priority` reaches the ledger as an integer or `scheduler._priority` ignores it."""
    written = cli.create_record(
        tmp_path / "l", cli._fields("a", ["priority=1", 'labels=["x"]']), prefix="acme"
    )
    assert written[0].payload["priority"] == 1
    assert written[0].payload["labels"] == ["x"]


def test_a_field_value_that_is_not_json_is_the_literal_string() -> None:
    """Which is what keeps an unquoted title from needing quotes."""
    assert cli._fields("", ["note=fix the parser"])["note"] == "fix the parser"
    assert cli._fields("", ["when=2026-08-16"])["when"] == "2026-08-16"


def test_the_title_is_stored_under_the_field_name_the_scheduler_reads() -> None:
    """One spelling per field (R2): a second literal here would be invisible to ranking."""
    assert cli._fields("a title", [])[cli.scheduler.TITLE_FIELD] == "a title"


def test_an_injected_redactor_is_applied_to_the_write(tmp_path: Path) -> None:
    """§4.2's redaction pass, injected rather than imported — the kit may not reach for one."""
    ledger = tmp_path / "l"
    code = cli.main(
        ["create", str(ledger), "--prefix", "acme", "--title", "a token"],
        redact=lambda text: "<redacted>" if "token" in text else text,
    )
    assert code == cli.EXIT_OK
    assert [state.fields["title"] for state in snapshot.load(ledger).records.values()] == [
        "<redacted>"
    ]


def test_without_a_redactor_the_text_is_stored_as_typed(tmp_path: Path) -> None:
    """The control: the assertion above measures the injected callable, not a default."""
    written = cli.create_record(tmp_path / "l", {"title": "a token"}, prefix="acme")
    assert written[0].payload["title"] == "a token"


def test_a_query_narrows_by_status_and_by_limit(tmp_path: Path) -> None:
    """AC: querying the set the create side wrote."""
    ledger = tmp_path / "l"
    open_record = cli.create_record(ledger, {}, prefix="acme")[0].record
    cli.create_record(ledger, {}, prefix="acme", status="closed")
    assert [held["record"] for held in cli.query_records(ledger, status="open")] == [open_record]
    assert len(cli.query_records(ledger)) == 2
    assert len(cli.query_records(ledger, limit=1)) == 1


def test_a_tombstoned_record_leaves_the_query_and_stays_readable(tmp_path: Path) -> None:
    """A delete is an event, not a removal, so `show` still answers for one."""
    ledger = tmp_path / "l"
    record = cli.create_record(ledger, {}, prefix="acme")[0].record
    events.append(ledger, [events.Draft(record, events.KIND_TOMBSTONE, {})])
    assert cli.query_records(ledger) == []
    held = cli.read_record(ledger, record)
    assert held is not None
    assert held["tombstoned"] is True


def test_a_swallowed_write_is_reported_not_raised(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A re-record appends nothing and used to raise; `close` names ids (basicly-wu4w8v)."""
    ledger = tmp_path / "l"
    record = cli.create_record(ledger, {}, prefix="acme")[0].record
    skipped = {"record": record, "events": [], "appended": False}
    for verb in (["update", "--status", "blocked"], ["close"]):
        argv = [verb[0], str(ledger), record, *verb[1:]]
        assert cli.main(argv) == cli.EXIT_OK
        capsys.readouterr()
        assert cli.main(argv) == cli.EXIT_OK
        assert json.loads(capsys.readouterr().out) == skipped


# --- the refusals ------------------------------------------------------------


def test_an_unknown_record_is_refused_and_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exit code is the verdict; the JSON says which record was asked for."""
    cli.create_record(tmp_path / "l", {}, prefix="acme")
    assert cli.main(["show", str(tmp_path / "l"), "acme-zzzz"]) == cli.EXIT_REFUSED
    assert json.loads(capsys.readouterr().out) == {"record": "acme-zzzz", "found": False}


def test_a_directory_that_is_not_a_ledger_is_refused_not_answered_as_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A mistyped path must not read as "no such record", which is a correct path's answer."""
    assert cli.main(["show", str(tmp_path / "absent"), "acme-zzzz"]) == cli.EXIT_REFUSED
    assert "is not a ledger directory" in json.loads(capsys.readouterr().out)["refused"]
    assert not (tmp_path / "absent").exists()


def test_a_field_pair_without_a_value_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """And nothing is written: the parse happens before the ledger is touched."""
    argv = ["create", str(tmp_path / "l"), "--prefix", "acme", "--field", "priority"]
    assert cli.main(argv) == cli.EXIT_REFUSED
    assert "is not name=value" in json.loads(capsys.readouterr().out)["refused"]
    assert not (tmp_path / "l").exists()


def test_an_unusable_prefix_is_refused_by_the_id_rules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`ids.IdError` subclasses ValueError, so one handler covers it — assert it is caught."""
    assert cli.main(["create", str(tmp_path / "l"), "--prefix", "ac-me"]) == cli.EXIT_REFUSED
    assert "prefix" in json.loads(capsys.readouterr().out)["refused"]


# --- the dependency graph (basicly-ztik9a) -----------------------------------


def test_show_carries_both_directions_of_the_dependency_graph(tmp_path: Path) -> None:
    """The fold holds an edge on the dependent, so a parent's children are in neither half.

    A consumer that copied only the kit had no command answering what a record blocks or
    what its children are, which is the whole graph the scheduler already ranks by.
    """
    ledger = tmp_path / "l"
    root = cli.create_record(ledger, {"title": "ship it"}, prefix="acme")[0].record
    child = cli.commands.create_child(ledger, root, {"title": "parse"})[0].record

    parent = cli.read_record(ledger, root)
    assert parent is not None
    assert parent["dependencies"] == []
    assert parent["dependents"] == [
        {"id": child, "dependency_type": "parent-child", "status": "open", "title": "parse"}
    ]

    held = cli.read_record(ledger, child)
    assert held is not None
    assert held["dependencies"] == [
        {"id": root, "dependency_type": "parent-child", "status": "open"}
    ]
    assert held["dependents"] == []


def test_a_record_with_no_edges_renders_both_keys_empty(tmp_path: Path) -> None:
    """Absence has to be distinguishable from a surface that never renders the keys."""
    ledger = tmp_path / "l"
    record = cli.create_record(ledger, {}, prefix="acme")[0].record

    shown = cli.read_record(ledger, record)

    assert shown is not None
    assert (shown["dependencies"], shown["dependents"]) == ([], [])

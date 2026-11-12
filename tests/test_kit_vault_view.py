from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from basicly import owned_store
from tests.test_kit_vault_share import _machine, _share, bare, vault_queue
from tests.tracker_process_fixture import recorded_review

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
__all__ = ["bare"]


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vault_view = _load("basicly_tracker_kit_vault_view", "vault_view.py")
queries = _load("basicly_tracker_kit_queries", "queries.py")
cli = _load("tracker_cli_vault_view", "cli.py")
events = vault_view.events
WANT = vault_view.Want("acme")
TRIGGER = "When a team shares a backlog, I want to see who holds a story, so I can avoid it."
SHAPED = ["--description", TRIGGER, "--acceptance", "- it is seen", "--requirements", "- stdlib"]


def _ev(record: str, kind: str, payload: dict[str, object], seq: int = 1) -> dict[str, object]:
    made = events.event_id_for(record, kind, payload)
    stamp = f"2026-11-12T00:00:0{seq}Z"
    return json.loads(events.to_json(events.Event(made, record, seq, kind, "t", stamp, payload)))


def _record(record: str, **extra: object) -> list[dict[str, object]]:
    created = _ev(record, "created", {"title": record, "issue_type": "task", **extra})
    return [created, _ev(record, "status", {"status": "open"}, 2)]


def _carry(ledger: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{"kind": "ledger", "record": e["record"], "ledger": e} for e in ledger]


def _push(tmp_path: Path, bare: Path, machine: str, drafts: list[dict[str, object]]) -> str:
    clone, state = _machine(tmp_path, bare, machine)
    origin = vault_queue.Origin("s1", False, time.time)
    vault_queue.write(clone, drafts, state=state, origin=origin)
    _share(clone, state)
    return vault_queue.writer_of(state)


def _view(tmp_path: Path, bare: Path, want: Any = WANT, **kw: Any) -> tuple[Any, Path]:
    reader, _state = _machine(tmp_path, bare, "reader")
    return reader, vault_view.present(reader, want, clock=kw.get("clock", time.time))


def _read(folder: Path) -> list[Any]:
    return events.read_events(folder)[0]


def test_the_view_holds_valid_carried_events_in_vault_order_and_ready_reads_it(
    tmp_path: Path, bare: Path
) -> None:
    first, second = _record("acme-a1"), _record("acme-b2")
    _push(tmp_path, bare, "m1", _carry(first))
    broken = {"kind": "ledger", "record": "acme-b2", "ledger": {"id": "x"}}
    _push(tmp_path, bare, "m2", [*_carry(second), broken, vault_queue.comment("acme-b2", "hi")])

    _reader, folder = _view(tmp_path, bare)

    assert [e.id for e in _read(folder)] == [e["id"] for e in first + second]
    assert not folder.is_relative_to(REPO_ROOT)


def test_ready_reads_the_view_as_it_reads_the_ledger_that_made_the_events(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source-ledger"
    for title in ("one", "two"):
        cli.main(["create", str(source), "--prefix", "acme", "--title", title, *SHAPED])
        recorded_review(cli, source, json.loads(capsys.readouterr().out)["record"])
    made = [json.loads(events.to_json(e)) for e in _read(source)]
    _push(tmp_path, bare, "m1", _carry(made))

    _reader, folder = _view(tmp_path, bare)

    expected = queries.ready(source)["records"]
    assert len(expected) == 2
    assert queries.ready(folder)["records"] == expected


def test_a_seq_collision_between_writers_folds_clean_and_the_later_line_wins(
    tmp_path: Path, bare: Path
) -> None:
    _push(tmp_path, bare, "m1", _carry(_record("acme-a1")))
    _push(tmp_path, bare, "m2", _carry([_ev("acme-a1", "status", {"status": "closed"}, 2)]))

    _reader, folder = _view(tmp_path, bare)
    folded = events.fold(_read(folder))

    assert folded.records["acme-a1"].status == "closed"
    assert (folded.forked, folded.mismatched_totals, folded.duplicate_ids) == ([], [], [])


def test_the_holder_comes_from_the_vault_fold_and_not_an_assignee_event(
    tmp_path: Path, bare: Path
) -> None:
    named = _ev("acme-a1", "field", {"name": "assignee", "value": "bob"}, 3)
    other = _ev("acme-b2", "field", {"name": "assignee", "value": "carol"}, 3)
    ledger = [*_record("acme-a1", assignee="alice"), named, *_record("acme-b2"), other]
    writer = _push(tmp_path, bare, "m1", [*_carry(ledger), {"kind": "claim", "record": "acme-a1"}])

    _reader, folder = _view(tmp_path, bare)
    records = events.fold(_read(folder)).records

    assert records["acme-a1"].fields["assignee"] == f"{writer}/s1"
    assert not records["acme-b2"].fields.get("assignee")
    assert events.fold(_read(folder)).mismatched_totals == []


def test_a_shallow_clone_refuses_by_name_and_its_printed_command_fixes_it(
    tmp_path: Path, bare: Path
) -> None:
    _push(tmp_path, bare, "m1", _carry(_record("acme-a1")))
    reader, _state = _machine(tmp_path, bare, "reader")
    reader.folder.mkdir(parents=True)
    git = ["git", "-c", "protocol.file.allow=always"]
    subprocess.run(
        [*git, "clone", "-q", "--bare", "--depth", "1", bare.as_uri(), str(reader.bare)], check=True
    )
    refspec = vault_view.vault_clone.FETCH_REFSPEC
    subprocess.run(
        ["git", "-C", str(reader.bare), "config", "remote.origin.fetch", refspec], check=True
    )

    with pytest.raises(vault_view.ViewError, match="shallow") as refused:
        vault_view.present(reader, WANT, clock=time.time)
    command = str(refused.value).split("`")[1]
    assert command == vault_view.unshallow_command(reader)
    subprocess.run(command.split(), check=True, capture_output=True)

    folder = vault_view.present(reader, WANT, clock=time.time)
    assert [e.record for e in _read(folder)] == ["acme-a1", "acme-a1"]


def test_only_the_prefix_shows_unless_all_projects_is_asked(tmp_path: Path, bare: Path) -> None:
    _push(tmp_path, bare, "m1", _carry([*_record("acme-a1"), *_record("acmex-z9")]))

    _reader, mine = _view(tmp_path, bare)
    _reader, every = _view(tmp_path, bare, vault_view.Want("acme", all_projects=True))

    assert {e.record for e in _read(mine)} == {"acme-a1"}
    assert {e.record for e in _read(every)} == {"acme-a1", "acmex-z9"}
    assert json.loads((mine / "template.json").read_text(encoding="utf-8"))["prefix"] == "acme"


def test_show_prints_id_status_holder_and_links(tmp_path: Path, bare: Path) -> None:
    edge = _ev("acme-a1", "edge", {"target": "acme-b2", "edge_type": "blocks"}, 3)
    ledger = [*_record("acme-a1"), edge, *_record("acme-b2")]
    writer = _push(tmp_path, bare, "m1", [*_carry(ledger), {"kind": "claim", "record": "acme-a1"}])
    reader, folder = _view(tmp_path, bare)

    shown = vault_view.show(folder, reader, ["acme-a1", "acme-b2", "acme-zz"])

    assert json.loads(json.dumps(shown)) == [
        {
            "id": "acme-a1",
            "status": "open",
            "holder": {"writer": writer, "session": "s1"},
            "links": [{"target": "acme-b2", "type": "blocks"}],
        },
        {"id": "acme-b2", "status": "open", "holder": None, "links": []},
        {"id": "acme-zz", "found": False},
    ]


def test_the_view_is_rebuilt_only_when_the_vault_head_moves(tmp_path: Path, bare: Path) -> None:
    _push(tmp_path, bare, "m1", _carry(_record("acme-a1")))
    _reader, folder = _view(tmp_path, bare)
    built = (folder / "events-0001.jsonl").stat().st_mtime_ns
    _push(tmp_path, bare, "m2", _carry(_record("acme-b2")))

    _reader, again = _view(tmp_path, bare)
    assert (again / "events-0001.jsonl").stat().st_mtime_ns == built
    _reader, moved = _view(tmp_path, bare, clock=lambda: time.time() + vault_view.FRESH_S)
    assert {e.record for e in _read(moved)} == {"acme-a1", "acme-b2"}


def test_present_ledger_reads_the_view_when_the_repository_names_a_vault(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = _push(
        tmp_path, bare, "m1", [*_carry(_record("acme-a1")), {"kind": "claim", "record": "acme-a1"}]
    )
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".basicly-tracker.toml").write_text(f'vault = "{bare.as_posix()}"\nprefix = "acme"\n')
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))

    folder = owned_store.present_ledger(repo)
    every = owned_store.present_ledger(repo, all_projects=True)

    assert folder.is_relative_to(tmp_path / "state")
    assert every != folder
    held = events.fold(_read(folder)).records["acme-a1"].fields["assignee"]
    assert held == f"{writer}/s1"
    (repo / ".basicly-tracker.toml").write_text('vault = "not a vault"\nprefix = "acme"\n')
    with pytest.raises(owned_store.VaultViewError, match="--vault"):
        owned_store.present_ledger(repo)

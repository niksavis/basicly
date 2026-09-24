from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import cli as engine_cli
from basicly import tracker
from tests import flipped_tracker

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"
TRIGGER = "When a user files a record, I want it kept, so I can read it back."
ROOT = "tf-1"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load(KIT_DIR / "cli.py", "tracker_cli_fields")
events = cli.events
snapshot = cli.snapshot


def _report(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def made(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, str]:
    ledger = tmp_path / "ledger"
    cli.main(["create", str(ledger), "--prefix", "acme", "--title", "a"])
    return ledger, _report(capsys)["record"]


@pytest.mark.parametrize(
    ("pair", "named"),
    [
        ("whatever=1", "not in the field table"),
        ("design=x", "put the design in the description"),
        ("notes=x", "add a comment instead"),
        ("created_at=2026-01-01", "import history, which only `import` writes"),
        ("dates=x", "computed from the event times"),
    ],
)
def test_a_field_no_reader_uses_is_refused_by_name_and_writes_nothing(
    made: tuple[Path, str], capsys: pytest.CaptureFixture[str], pair: str, named: str
) -> None:
    ledger, record = made
    before = events.read_events(ledger)[0]

    assert cli.main(["update", str(ledger), record, "--field", pair]) == cli.EXIT_REFUSED

    assert named in _report(capsys)["refused"]
    assert events.read_events(ledger)[0] == before


def test_a_create_carrying_an_unknown_field_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["create", str(tmp_path / "l"), "--prefix", "acme", "--field", "estimate=3"]

    assert cli.main(argv) == cli.EXIT_REFUSED
    assert "'estimate' is not in the field table" in _report(capsys)["refused"]


def test_a_field_the_template_declares_is_writable_and_listed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "template.json").write_text('{"sections": ["## Risks"]}', encoding="utf-8")
    argv = ["create", str(ledger), "--prefix", "acme", "--field", "risks=none known"]

    assert cli.main(argv) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["fields", str(ledger)]) == cli.EXIT_OK

    listed = {row["name"]: row["role"] for row in _report(capsys)["fields"]}
    assert listed["risks"] == "template"


def test_the_field_table_names_a_role_and_a_reader_for_every_field(
    made: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, _record = made

    assert cli.main(["fields", str(ledger)]) == cli.EXIT_OK

    report = _report(capsys)
    assert report["schema"] == "basicly.tracker.fields.v1"
    roles = {row["role"] for row in report["fields"]}
    assert {"required", "ready", "closing", "optional", "imported", "derived"} <= roles
    assert all(row["reader"] for row in report["fields"])


def test_the_dates_are_derived_from_the_events_and_a_reopen_clears_closed(
    made: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, record = made
    cli.main(["close", str(ledger), record, "--reason", "shipped"])
    capsys.readouterr()
    cli.main(["show", str(ledger), record])
    closed = _report(capsys)
    stamps = [event.ts for event in events.read_events(ledger)[0]]

    assert closed["dates"] == {"created": stamps[0], "updated": stamps[-1], "closed": stamps[-1]}
    assert "created_at" not in closed["fields"]

    cli.main(["update", str(ledger), record, "--status", "open"])
    capsys.readouterr()
    cli.main(["show", str(ledger), record])
    assert _report(capsys)["dates"]["closed"] is None


def _event(seq: int, kind: str, payload: dict[str, object]) -> Any:
    return events.Event(
        id=f"acme-a1#ev-{seq}",
        record="acme-a1",
        seq=seq,
        kind=kind,
        actor="import",
        ts="2026-08-07T16:15:13.465941Z",
        payload=payload,
    )


def test_an_imported_record_is_dated_by_the_times_its_source_recorded() -> None:
    mark = {"imported_from": "beads-export", "provenance": "EXTRACTED"}
    folded = events.fold([
        _event(
            1,
            "created",
            {
                **mark,
                "title": "t",
                "created_at": "2026-07-17T16:21:15.634707007Z",
                "updated_at": "2026-07-17T19:02:44.147293347Z",
                "closed_at": "2026-07-17T19:02:44.147224054Z",
            },
        ),
        _event(2, "status", {**mark, "status": "closed"}),
        _event(3, "comment", {**mark, "text": "c", "asserted_at": "2026-07-17T18:44:22Z"}),
    ])

    assert folded.records["acme-a1"].dates == {
        "created": "2026-07-17T16:21:15.634707007Z",
        "updated": "2026-07-17T19:02:44.147293347Z",
        "closed": "2026-07-17T19:02:44.147224054Z",
    }


def test_updated_is_the_last_event_even_when_the_wall_clock_went_back() -> None:
    first = _event(1, "created", {"title": "t"})
    later = events.Event(
        id="acme-a1#ev-2",
        record="acme-a1",
        seq=2,
        kind="status",
        actor="operator",
        ts="2026-08-07T16:15:10.000000Z",
        payload={"status": "closed"},
    )

    dates = events.fold([first, later]).records["acme-a1"].dates

    assert dates["updated"] == "2026-08-07T16:15:10.000000Z"
    assert dates["closed"] == "2026-08-07T16:15:10.000000Z"


def test_a_snapshot_of_the_previous_format_is_stale_and_rebuilds_with_dates(
    made: tuple[Path, str],
) -> None:
    ledger, record = made
    snapshot.rebuild(ledger)
    path = snapshot.snapshot_path(ledger)
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["version"] = 1
    body = [json.loads(line) for line in lines[1:]]
    for one in body:
        del one["dates"]
    path.write_text("\n".join(json.dumps(one) for one in [header, *body]) + "\n", "utf-8")

    assert snapshot.staleness(ledger).stale is True
    assert snapshot.load(ledger).records[record].dates["created"] is not None


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"a tracker write spawned a process: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)
    root = flipped_tracker.flipped_repo(tmp_path)
    (root / "basicly.toml").write_text(
        '[tracker]\nmode = "owned"\nprefix = "tf"\n', encoding="utf-8"
    )
    flipped_tracker.seed(root, ROOT, title="the root")
    monkeypatch.chdir(root)
    return root


def test_the_engine_route_refuses_the_same_field_with_the_same_remedy(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    argv = ["tracker", "write", "--", "update", ROOT, "--notes", "probe"]

    assert engine_cli.main(argv) != 0
    assert "add a comment instead" in capsys.readouterr().err
    assert (tracker.read_record(repo, ROOT) or {}).get("notes") is None


def test_the_engine_create_keeps_the_assignee_it_was_given(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    argv = ["tracker", "write", "--", "create", "a child", "--parent", ROOT, "-a", "alex"]

    assert engine_cli.main([*argv, "--json"]) == 0
    minted = json.loads(capsys.readouterr().out)["id"]
    assert (tracker.read_record(repo, minted) or {})["assignee"] == "alex"

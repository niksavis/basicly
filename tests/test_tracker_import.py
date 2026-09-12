from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from basicly import cli, owned_store, redact, tracker_import
from basicly.schema import ValidationError
from basicly.tracker_import import _REFUSALS_SHOWN

REPO = Path(__file__).parent.parent
KIT = Path(".basicly") / "core" / "kit" / "tracker"

_EXPORT = [
    {
        "id": "acme-99z",
        "title": "Vendor the fix",
        "status": "open",
        "priority": 1,
        "issue_type": "task",
        "created_by": "someone",
        "compaction_level": 0,
    },
    {
        "id": "acme-o2u",
        "title": "Lua template",
        "status": "closed",
        "priority": 2,
        "issue_type": "chore",
        "dependencies": [{"depends_on_id": "acme-99z", "type": "blocks", "issue_id": "acme-o2u"}],
        "comments": [{"id": "c1", "text": "had to skip a hook", "created_at": "2026-09-10T09:00Z"}],
    },
]


@pytest.fixture
def host(tmp_path: Path) -> Path:
    shutil.copytree(REPO / KIT, tmp_path / KIT)
    (tmp_path / ".basicly" / "ledger").mkdir(parents=True)
    return tmp_path


def _export(root: Path, records: list[dict] | str) -> Path:
    path = root / "issues.jsonl"
    if isinstance(records, str):
        path.write_text(records, encoding="utf-8")
    else:
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_a_dry_run_writes_nothing_and_names_what_would_be_refused(host: Path) -> None:

    export = _export(host, [*_EXPORT, {"id": "not an id", "title": "bad"}])

    code, lines = tracker_import.run_import(host, export, source_name="beads", dry_run=True)

    report = "\n".join(lines)
    assert code == 1
    assert "nothing written" in report
    assert "2 new record(s)" in report and "1 that would be refused" in report
    assert not list((host / ".basicly" / "ledger").glob("events-*.jsonl")), "it wrote a ledger"


def test_a_clean_dry_run_exits_zero(host: Path) -> None:
    code, _ = tracker_import.run_import(host, _export(host, _EXPORT), dry_run=True)

    assert code == 0


def test_the_source_ids_survive_the_import(host: Path) -> None:
    export = _export(host, _EXPORT)

    code, _ = tracker_import.run_import(host, export, source_name="beads")

    kit = owned_store.kit(host)
    ledger = owned_store.ledger_dir(host)
    folded = kit.events.fold(kit.events.read_events(ledger)[0]).records
    assert code == 0
    assert set(folded) == {"acme-99z", "acme-o2u"}


def test_a_rejection_sets_the_exit_code(host: Path) -> None:
    export = _export(host, [*_EXPORT, {"id": "not an id", "title": "bad"}])

    code, lines = tracker_import.run_import(host, export, source_name="beads")

    assert code == 1
    assert any("rejected" in line for line in lines)


def test_a_re_run_appends_nothing(host: Path) -> None:
    export = _export(host, _EXPORT)
    tracker_import.run_import(host, export, source_name="beads")

    _, lines = tracker_import.run_import(host, export, source_name="beads")

    assert "0 record(s) created, 0 event(s) appended" in lines[0]


def test_an_unparseable_line_is_reported_rather_than_tolerated(host: Path) -> None:
    export = _export(host, '{"id": "acme-99z", "status": "open"}\nnot json\n')

    code, lines = tracker_import.run_import(host, export, source_name="beads")

    assert code == 1
    assert any("unreadable" in line for line in lines)


def test_a_missing_export_is_an_error_not_a_traceback(host: Path) -> None:
    with pytest.raises(ValidationError):
        tracker_import.run_import(host, host / "nope.jsonl", source_name="beads")


def _hook():
    script = REPO / ".basicly" / "core" / "hooks" / "tracker-path-scan.py"
    spec = importlib.util.spec_from_file_location("tracker_path_scan", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_import_writes_a_ledger_its_own_commit_gate_accepts(host: Path) -> None:

    home = "/home" + "/someuser/development/acme"
    export = _export(
        host,
        [
            {
                "id": "acme-99z",
                "title": "Vendor the fix",
                "status": "open",
                "source_repo_path": home,
                "created_by": redact.machine_identity() or "someuser",
            }
        ],
    )

    code, _ = tracker_import.run_import(host, export, source_name="beads")

    written = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(owned_store.ledger_dir(host).glob("events-*.jsonl"))
    )
    assert code == 0
    assert home not in written
    assert _hook().findings(".basicly/ledger/events-0001.jsonl", written) == []


def test_tracker_scrub_repairs_a_ledger_the_commit_gate_refuses(
    host: Path, monkeypatch, capsys
) -> None:

    leak = "/home" + "/someuser/dev/acme"
    kit = owned_store.kit(host)
    snapshot = kit.migrate.read_snapshot(
        _export(host, [{"id": "acme-99z", "title": "t", "source_repo_path": leak}]),
        name="beads",
    )
    kit.migrate.import_snapshot(owned_store.ledger_dir(host), snapshot)
    monkeypatch.chdir(host)

    code = cli.main(["tracker", "scrub"])

    written = (owned_store.ledger_dir(host) / "events-0001.jsonl").read_text(encoding="utf-8")
    assert code == 0
    assert "Scrubbed 1 event(s)" in capsys.readouterr().out
    assert leak not in written


def test_the_import_names_the_id_prefix_when_the_repo_declares_none(host: Path) -> None:

    _, lines = tracker_import.run_import(host, _export(host, _EXPORT), source_name="beads")

    report = "\n".join(lines)
    assert "declares no [tracker] prefix" in report
    assert 'prefix = "acme"' in report


def test_the_import_stays_quiet_when_a_prefix_is_declared(host: Path) -> None:
    (host / "basicly.toml").write_text('[tracker]\nprefix = "acme"\n', encoding="utf-8")

    _, lines = tracker_import.run_import(host, _export(host, _EXPORT), source_name="beads")

    assert "[tracker] prefix" not in "\n".join(lines)


HYPHENATED = [
    {"id": f"burndown-chart-{index:03d}", "title": f"t{index}", "status": "open"}
    for index in range(1, 9)
]


def test_a_refusal_names_its_cause_once_not_every_id(host: Path) -> None:

    export = _export(host, [*HYPHENATED, {"id": "Not An Id", "title": "bad"}])

    code, lines = tracker_import.run_import(host, export, source_name="beads", dry_run=True)

    report = "\n".join(lines)
    assert code == 1
    assert "the prefix may not carry a hyphen" in report
    assert "The source prefix is 'burndown-chart'" in report
    assert report.count("burndown-chart-00") == _REFUSALS_SHOWN, "one line per id, not per cause"
    assert "and 3 more" in report


def test_a_dry_run_names_the_id_prefix_too(host: Path) -> None:

    _, lines = tracker_import.run_import(
        host, _export(host, _EXPORT), source_name="beads", dry_run=True
    )

    assert 'prefix = "acme"' in "\n".join(lines)

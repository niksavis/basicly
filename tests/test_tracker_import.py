"""`basicly tracker import` — the seam onto the kit's importer (basicly-lc2bd3v).

The kit has carried a tested importer with no production caller and no command, so two
consumers concluded the migration path was gone and planned to re-file live work by
hand. These tests hold the property that made that conclusion expensive: source ids
survive, so a commit message referencing one still resolves after the move.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from basicly import cli, owned_store, redact, tracker_import
from basicly.schema import ValidationError

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
    """A consumer-shaped repo with the kit copied in and an empty ledger."""
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
    """A pre-flight that skipped the id rule would list an id the write then refuses."""
    export = _export(host, [*_EXPORT, {"id": "not an id", "title": "bad"}])

    code, lines = tracker_import.run_import(host, export, source_name="beads", dry_run=True)

    report = "\n".join(lines)
    assert code == 0
    assert "nothing written" in report
    assert "2 new record(s)" in report and "1 that would be refused" in report
    assert not list((host / ".basicly" / "ledger").glob("events-*.jsonl")), "it wrote a ledger"


def test_the_source_ids_survive_the_import(host: Path) -> None:
    """Renumbering would strand every commit message that references an old id."""
    export = _export(host, _EXPORT)

    code, _ = tracker_import.run_import(host, export, source_name="beads")

    kit = owned_store.kit(host)
    ledger = owned_store.ledger_dir(host)
    folded = kit.events.fold(kit.events.read_events(ledger)[0]).records
    assert code == 0
    assert set(folded) == {"acme-99z", "acme-o2u"}


def test_a_rejection_sets_the_exit_code(host: Path) -> None:
    """A partial import that reported success would lose records silently."""
    export = _export(host, [*_EXPORT, {"id": "not an id", "title": "bad"}])

    code, lines = tracker_import.run_import(host, export, source_name="beads")

    assert code == 1
    assert any("rejected" in line for line in lines)


def test_a_re_run_appends_nothing(host: Path) -> None:
    """An import torn off at the tail must complete on a re-run, not double the history."""
    export = _export(host, _EXPORT)
    tracker_import.run_import(host, export, source_name="beads")

    _, lines = tracker_import.run_import(host, export, source_name="beads")

    assert "0 record(s) created, 0 event(s) appended" in lines[0]


def test_an_unparseable_line_is_reported_rather_than_tolerated(host: Path) -> None:
    """Format drift in somebody else's export is expected, and must be seen."""
    export = _export(host, '{"id": "acme-99z", "status": "open"}\nnot json\n')

    code, lines = tracker_import.run_import(host, export, source_name="beads")

    assert code == 1
    assert any("unreadable" in line for line in lines)


def test_a_missing_export_is_an_error_not_a_traceback(host: Path) -> None:
    """The path is user input, so it is a trust boundary rather than an assertion."""
    with pytest.raises(ValidationError):
        tracker_import.run_import(host, host / "nope.jsonl", source_name="beads")


def _hook():
    """The `tracker-path-scan` gate, loaded the way `test_tracker_path_scan.py` loads it."""
    script = REPO / ".basicly" / "core" / "hooks" / "tracker-path-scan.py"
    spec = importlib.util.spec_from_file_location("tracker_path_scan", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_import_writes_a_ledger_its_own_commit_gate_accepts(host: Path) -> None:
    """An import that leaks the source machine's paths cannot be committed (basicly-npiudkl).

    A real consumer landed 49 of 50 records and then `tracker-path-scan` refused the
    commit with 93 findings, because the export's `source_repo_path` and `created_by`
    reached the ledger verbatim. The redactor every other engine write passes was the
    one keyword this seam left off.
    """
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
    """The repair needs a named surface, not a `python -c` (basicly-9fagxpm).

    A consumer whose sandbox refuses an in-place rewrite from a raw interpreter could
    not apply the repair at all, so the documented adoption path could not complete.
    """
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

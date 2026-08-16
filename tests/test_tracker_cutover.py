"""Tests for the tracker cutover commands' entry points (basicly-vkh0.23).

The bead is an **entry point** defect, not an import defect: `migrate.import_snapshot` was
correct and had no caller, no `main()` and no CLI, so it was run once by hand and could
never be repeated. The ledger drifted 24 records behind within a day, and nothing a fresh
consumer runs could build one at all.

So these drive the command rather than the kit function — the kit's own behaviour is pinned
in `test_kit_tracker_migrate.py`, and re-asserting it here would test the import twice and
the wiring never. The one exception is the round trip, which builds a ledger from an export
end to end because "a fresh consumer can build one" is the claim and a mocked import cannot
carry it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import br, cli, tracker_cutover

if TYPE_CHECKING:
    import pytest

CONFIG = "basicly.toml"


def _repo(root: Path, records: list[dict], ledger: str = "") -> Path:
    """A repo with the kit installed, an export, and optionally a ledger already built."""
    kit_src = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"
    kit_dst = root / ".basicly" / "core" / "kit" / "tracker"
    kit_dst.mkdir(parents=True)
    for module in kit_src.glob("*.py"):
        (kit_dst / module.name).write_text(module.read_text(encoding="utf-8"), encoding="utf-8")
    (root / ".beads").mkdir()
    (root / ".beads" / "issues.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    (root / CONFIG).write_text('[tracker]\nmode = "dual"\n', encoding="utf-8")
    directory = root / ".basicly" / "ledger"
    directory.mkdir(parents=True)
    if ledger:
        (directory / "events-0001.jsonl").write_text(ledger, encoding="utf-8")
    return root


def _record(issue_id: str, **fields: object) -> dict:
    """One export record, in br's own spelling."""
    return {"id": issue_id, "title": issue_id, "status": "open", "issue_type": "task", **fields}


def test_the_import_is_reachable_from_the_cli_at_all() -> None:
    """The whole defect in one assertion: `basicly tracker --help` listed `shadow` only.

    Asserted against the parser rather than the help text, because a subcommand that
    parses is what makes the import re-runnable — the help string is how a human finds it.
    """
    parser = cli._build_parser()

    assert parser.parse_args(["tracker", "import", "--dry-run"]).tracker_command == "import"


def test_a_dry_run_reports_how_far_behind_the_ledger_is_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bead's own acceptance check, and the ledger is byte-identical afterwards.

    A dry run that writes is the failure this command would be least likely to notice,
    because its output would look right either way.
    """
    repo = _repo(tmp_path, [_record("basicly-a"), _record("basicly-b")])
    monkeypatch.chdir(repo)

    assert cli.main(["tracker", "import", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "ledger 0 records, export 2" in out
    assert "would add 2 records, 0 tombstones" in out
    assert not list((repo / ".basicly" / "ledger").glob("events-*.jsonl"))


def test_the_import_builds_a_ledger_a_fresh_consumer_could_not_have(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end, against the real kit: an export in, a ledger out.

    This is the consumer criterion the bead names — "nothing a fresh consumer runs can
    build the ledger at all" — so it runs the real import rather than asserting the call.
    """
    repo = _repo(tmp_path, [_record("basicly-a"), _record("basicly-b")])
    monkeypatch.chdir(repo)

    assert cli.main(["tracker", "import"]) == 0

    assert "added 2 records" in capsys.readouterr().out
    events = br.kit(repo).read_ledger(br.ledger_dir(repo))
    assert {str(event.record) for event in events} == {"basicly-a", "basicly-b"}


def test_a_second_import_adds_only_what_the_ledger_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Re-runnable is the point, so running it twice is the property, not an edge case.

    The first run's records are not created again — that is `ImportReport.diverged`'s
    contract — so a second run is how a drifted ledger is brought current.
    """
    repo = _repo(tmp_path, [_record("basicly-a")])
    monkeypatch.chdir(repo)
    assert cli.main(["tracker", "import"]) == 0
    (repo / ".beads" / "issues.jsonl").write_text(
        json.dumps(_record("basicly-a")) + "\n" + json.dumps(_record("basicly-b")) + "\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    assert cli.main(["tracker", "import"]) == 0

    assert "added 1 records" in capsys.readouterr().out


def test_a_ledger_holding_a_post_flip_record_refuses_the_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The refusal `basicly-c357` requires, without which this bead breaks that one.

    Once the dual write has created a record, re-importing would close the historical gap
    the differential is judged against and the agreement would prove nothing
    (`basicly-u4xu`). The native record is spelled here as an event carrying no import
    marker, which is exactly how the flip boundary classifies it.
    """
    native = json.dumps({
        "id": "basicly-n#ev-1",
        "record": "basicly-n",
        "seq": 1,
        "kind": "created",
        "actor": "",
        "ts": "2026-08-14T00:00:00Z",
        "payload": {"title": "native"},
        "totals": {},
    })
    repo = _repo(tmp_path, [_record("basicly-a")], ledger=native + "\n")
    monkeypatch.chdir(repo)

    assert cli.main(["tracker", "import"]) == 1

    assert "refused" in capsys.readouterr().out


def test_the_adopt_command_reports_what_it_repaired_and_what_it_could_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The entry point `basicly-vkh0.24`'s repair would be a one-shot without.

    Driven through `cli.main` because the wiring is the subject — `br.adopt_hand_writes`
    itself is pinned in `test_tracker_adoption.py`. The report is stubbed here for the same
    reason: reaching the real one needs a live br, which this module never spawns.
    """
    repo = _repo(tmp_path, [_record("basicly-a")])
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        tracker_cutover.br,
        "adopt_hand_writes",
        lambda _root: br.AdoptionReport(
            adopted=("basicly-a",), diverged=("basicly-c",), unadoptable=("basicly-b",)
        ),
    )

    assert cli.main(["tracker", "adopt"]) == 1

    out = capsys.readouterr().out
    assert "basicly-a" in out
    assert "basicly-c has a hand-edited field" in out
    assert "basicly-b is in br and not in the export" in out


def test_the_dry_run_reports_the_refusal_the_real_run_would(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A preview that says "would add 200" for a run that will refuse is worse than none.

    The pair with the test above is what makes this a preview rather than a second
    implementation: both go through `import_preview`, so they cannot answer differently.
    """
    native = json.dumps({
        "id": "basicly-n#ev-1",
        "record": "basicly-n",
        "seq": 1,
        "kind": "created",
        "actor": "",
        "ts": "2026-08-14T00:00:00Z",
        "payload": {"title": "native"},
        "totals": {},
    })
    repo = _repo(tmp_path, [_record("basicly-a")], ledger=native + "\n")
    monkeypatch.chdir(repo)

    assert cli.main(["tracker", "import", "--dry-run"]) == 1

    out = capsys.readouterr().out
    assert "refused" in out
    assert "would add" not in out

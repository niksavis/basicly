from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from basicly import config, redact

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


migrate = _load(KIT_DIR / "migrate.py", "kit_beads_test_migrate")
beads = _load(KIT_DIR / "beads.py", "kit_beads_test_beads")
events = migrate.events

SOURCE = "beads"
CLOCK = 1_000_000_000.0
HOME_REPO = "/home" + "/ada/work/acme"
DATA_REPO = "/data" + "/projects/acme"
WHEN = "2026-09-01T10:00:00Z"


def _bd_line(record: str, **overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "_type": "issue",
        "id": record,
        "title": f"the bd record {record}",
        "status": "hooked",
        "priority": 1,
        "issue_type": "story",
        "created_at": WHEN,
        "created_by": "ada",
        "updated_at": WHEN,
        "labels": ["cli"],
        "source_repo_path": HOME_REPO,
        "dependencies": [
            {
                "issue_id": record,
                "depends_on_id": "acme-bd02",
                "type": "blocks",
                "created_at": WHEN,
                "created_by": "ada",
                "metadata": "{}",
            }
        ],
        "comments": [
            {
                "id": "0194c1d2-comment",
                "issue_id": record,
                "author": "ada",
                "text": f"reproduced in {HOME_REPO}/src",
                "created_at": WHEN,
            }
        ],
        "dependency_count": 1,
        "dependent_count": 0,
        "comment_count": 1,
    }
    line.update(overrides)
    return line


def _br_line(record: str, **overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "id": record,
        "title": f"the br record {record}",
        "status": "draft",
        "priority": 2,
        "issue_type": "docs",
        "created_at": WHEN,
        "updated_at": WHEN,
        "source_repo": ".",
        "source_repo_path": DATA_REPO,
        "dependencies": [
            {
                "issue_id": record,
                "depends_on_id": "acme-br02",
                "type": "parent-child",
                "created_at": WHEN,
                "created_by": "ada",
                "metadata": "{}",
                "thread_id": "",
            }
        ],
        "comments": [
            {"id": 7, "issue_id": record, "author": "ada", "text": "a note", "created_at": WHEN}
        ],
    }
    line.update(overrides)
    return line


def _text(*lines: dict[str, Any]) -> str:
    return "".join(json.dumps(line) + "\n" for line in lines)


def _import(ledger: Path, text: str) -> Any:
    return migrate.import_snapshot(
        ledger, migrate.parse_snapshot(text, name=SOURCE), clock=lambda: CLOCK
    )


def _ledger_text(ledger: Path) -> str:
    return "".join(path.read_text(encoding="utf-8") for path in sorted(ledger.glob("*.jsonl")))


def _state(ledger: Path, record: str) -> Any:
    found, _ = events.read_events(ledger)
    return events.fold(found).records[record]


def _kinds(report: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in report.events:
        counts[event.kind] = counts.get(event.kind, 0) + 1
    return counts


def _payloads(report: Any, kind: str) -> list[dict[str, Any]]:
    return [event.payload for event in report.events if event.kind == kind]


def test_a_bd_export_imports_with_its_comment_and_edge_through_the_bd_table(
    tmp_path: Path,
) -> None:
    report = _import(tmp_path, _text(_bd_line("acme-bd01")))

    assert report.rejected == [] and report.unreadable == []
    assert _kinds(report) == {"created": 1, "status": 1, "comment": 1, migrate.KIND_EDGE: 1}
    state = _state(tmp_path, "acme-bd01")
    assert state.status == "in_progress"
    assert state.fields["issue_type"] == "feature"
    assert not {"_type", "comment_count", "dependency_count"} & set(state.fields)
    assert _payloads(report, "comment")[0]["source_id"] == "0194c1d2-comment"
    assert _payloads(report, migrate.KIND_EDGE)[0]["to"] == "acme-bd02"


def test_a_br_export_imports_with_its_comment_and_edge_through_the_br_table(
    tmp_path: Path,
) -> None:
    report = _import(tmp_path, _text(_br_line("acme-br01")))

    assert report.rejected == [] and report.unreadable == []
    assert _kinds(report) == {"created": 1, "status": 1, "comment": 1, migrate.KIND_EDGE: 1}
    state = _state(tmp_path, "acme-br01")
    assert state.status == "deferred"
    assert state.fields["issue_type"] == "chore"
    assert state.fields["source_repo"] == "."
    assert _payloads(report, "comment")[0]["source_id"] == 7
    assert _payloads(report, migrate.KIND_EDGE)[0]["type"] == "parent-child"


def test_no_machine_path_from_either_export_reaches_the_ledger(tmp_path: Path) -> None:
    text = _text(_bd_line("acme-bd01"), _br_line("acme-br01"))
    assert HOME_REPO in text and DATA_REPO in text

    _import(tmp_path, text)

    written = _ledger_text(tmp_path)
    assert "acme-bd01" in written and "acme-br01" in written
    assert HOME_REPO not in written
    assert DATA_REPO not in written
    assert "source_repo_path" not in written
    assert "reproduced in <redacted:posix-home-path>" in written


def test_a_status_outside_the_table_is_refused_by_name_and_not_imported(
    tmp_path: Path,
) -> None:
    report = _import(tmp_path, _text(_bd_line("acme-bd01", status="review")))

    assert report.imported == []
    [refusal] = report.unreadable
    assert "'acme-bd01' has status 'review'" in refusal.reason
    assert "which the bd table does not map" in refusal.reason


def test_a_type_outside_the_table_is_refused_by_name_and_not_imported(tmp_path: Path) -> None:
    report = _import(tmp_path, _text(_br_line("acme-br01", issue_type="gate")))

    assert report.imported == []
    [refusal] = report.unreadable
    assert "'acme-br01' has issue_type 'gate'" in refusal.reason
    assert "which the br table does not map" in refusal.reason


def test_a_br_tombstone_is_refused_as_a_deletion_not_imported_live(tmp_path: Path) -> None:
    report = _import(tmp_path, _text(_br_line("acme-br01", status="tombstone")))

    assert report.imported == []
    assert "marks a deletion in br" in report.unreadable[0].reason


def test_a_bd_memory_line_is_refused_by_name(tmp_path: Path) -> None:
    memory = {"_type": "memory", "key": "style", "value": "short"}

    report = _import(tmp_path, _text(memory, _bd_line("acme-bd01")))

    assert report.imported == ["acme-bd01"]
    assert "a bd memory line, not an issue" in report.unreadable[0].reason


def test_every_table_maps_onto_the_tracker_vocabulary() -> None:
    assert beads.WORK_TYPES == config.WORK_TYPES
    for source in (beads.BD, beads.BR):
        assert set(source.statuses.values()) <= set(beads.values.WRITABLE_STATUSES), source.name
        assert set(source.types.values()) <= set(config.WORK_TYPES), source.name


def test_the_kit_machine_path_rules_mirror_the_package_exactly() -> None:
    assert [(name, p.pattern) for name, p in beads.MACHINE_PATH_RULES] == [
        (name, p.pattern) for name, p in redact.MACHINE_PATH_RULES
    ]

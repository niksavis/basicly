from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"

RECORD = "acme-aa11"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fsck = _load(KIT_DIR / "fsck.py", "label_shape_fsck")
label_shape = fsck.label_shape
events = fsck.events
commands = _load(KIT_DIR / "commands.py", "label_shape_commands")


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    directory = tmp_path / "ledger"
    commands.create_root(directory, {"title": "root"}, prefix="acme")
    return directory


def _root(ledger: Path) -> str:
    (record,) = [key for key in commands.queries.folded(ledger) if "." not in key]
    return record


def _append(directory: Path, drafts: list[Any]) -> None:
    events.append(directory, drafts, actor="a-lane", clock=lambda: 1_000_000_000.0)


def _seed(directory: Path, labels: Any, *, kind: str = "created") -> Path:
    payload = (
        {"title": "a lane", "labels": labels}
        if kind == "created"
        else {"name": "labels", "value": labels}
    )
    drafts = [events.Draft(RECORD, "created", {"title": "a lane"})] if kind != "created" else []
    _append(directory, [*drafts, events.Draft(RECORD, kind, payload)])
    return directory


def _split_findings(report: Any) -> list[Any]:
    return [found for found in report.findings if found.kind == fsck.SPLIT_LABEL]


def test_the_joined_string_shape_is_split_on_the_separator_and_never_iterated() -> None:
    assert label_shape.labels_of("phase-2") == ("phase-2",)
    assert label_shape.labels_of("phase-2,ready") == ("phase-2", "ready")


def test_the_list_shape_passes_through_because_a_created_event_stores_one() -> None:
    assert label_shape.labels_of(["phase-2", "ready"]) == ("phase-2", "ready")
    assert label_shape.labels_of(None) == ()


def test_a_label_written_through_the_kit_seam_reads_back_as_the_whole_word(
    ledger: Path,
) -> None:
    record = _root(ledger)

    commands.update(ledger, record, add_labels=["phase-2"])

    stored = commands.queries.folded(ledger)[record].fields["labels"]
    assert isinstance(stored, str), "the field event stores the joined shape, not a list"
    assert label_shape.labels_of(stored) == ("phase-2",)


def test_the_reproduction_that_filed_this_reads_a_healthy_record_as_seven_labels(
    ledger: Path,
) -> None:
    record = _root(ledger)
    commands.update(ledger, record, add_labels=["phase-2"])

    stored = commands.queries.folded(ledger)[record].fields["labels"]

    assert list(stored) == ["p", "h", "a", "s", "e", "-", "2"]
    assert label_shape.labels_of(stored) != tuple(stored)


def test_a_log_whose_labels_are_whole_words_is_clean(tmp_path: Path) -> None:
    report = fsck.check(_seed(tmp_path / "ledger", ["phase-2", "ready"]))

    assert _split_findings(report) == []
    assert report.exit_code == fsck.EXIT_CLEAN


def test_a_created_event_carrying_one_character_labels_is_reported_broken(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger", ["p", "h", "a", "s", "e", "-", "2"])

    report = fsck.check(ledger)

    (found,) = _split_findings(report)
    assert found.severity == fsck.BROKEN
    assert found.subject == RECORD
    assert "7 label(s) of one character" in found.detail
    assert len(found.event_ids) == 1
    assert report.exit_code == fsck.EXIT_BROKEN


def test_a_field_event_whose_joined_value_is_characters_is_reported_broken(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger", "p,h,a,s,e,-,2", kind="field")

    (found,) = _split_findings(fsck.check(ledger))

    assert found.severity == fsck.BROKEN
    assert "7 label(s) of one character" in found.detail


def test_the_finding_names_every_offending_label_and_the_events_carrying_them(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger", "p,h", kind="field")
    _append(ledger, [events.Draft(RECORD, "field", {"name": "labels", "value": "x"})])

    (found,) = _split_findings(fsck.check(ledger))

    assert "'h'" in found.detail and "'p'" in found.detail and "'x'" in found.detail
    assert len(found.event_ids) == 2, "both writes carry the class, not only the winning one"


def test_a_corrected_record_still_reports_the_write_that_corrupted_it(
    tmp_path: Path,
) -> None:
    ledger = _seed(tmp_path / "ledger", "p,h,a,s,e,-,2", kind="field")
    _append(ledger, [events.Draft(RECORD, "field", {"name": "labels", "value": "phase-2"})])

    assert commands.queries.folded(ledger)[RECORD].fields["labels"] == "phase-2"
    assert _split_findings(fsck.check(ledger)) != []

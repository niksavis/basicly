"""The label shape the kit stores, and the `fsck` finding over a log that lost it.

Filed as basicly-0cpn51. The bead this lands under reported 44 corrupted labels. There were
none: its reproduction iterated the *string* storage shape, so it read `truth` as five
one-character labels and counted the records it had just corrupted in memory. The test named
for that reproduction pins it, because a probe reading a healthy log as broken is the finding.

What is real is that the shape makes the mistake easy and nothing caught it. So the tests
here are in two halves: the split contract (a string is split, a list passes through, and a
write through the kit's own seam reads back the whole word), and the checker (a log that
carries the class is reported `broken`, against a control that is not).
"""

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
    """Load a standalone kit module by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fsck = _load(KIT_DIR / "fsck.py", "label_shape_fsck")
# The modules `fsck` itself loaded, never second copies: two loads of one file mint two
# `Event` classes and a frozen dataclass compares unequal across them.
label_shape = fsck.label_shape
events = fsck.events
commands = _load(KIT_DIR / "commands.py", "label_shape_commands")


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """A ledger holding one record, created through the kit's own seam."""
    directory = tmp_path / "ledger"
    commands.create_root(directory, {"title": "root"}, prefix="acme")
    return directory


def _root(ledger: Path) -> str:
    """The only root record id in *ledger*."""
    (record,) = [key for key in commands.queries.folded(ledger) if "." not in key]
    return record


def _append(directory: Path, drafts: list[Any]) -> None:
    """Append *drafts* under a fixed injected clock, so nothing here reads a wall clock."""
    events.append(directory, drafts, actor="a-lane", clock=lambda: 1_000_000_000.0)


def _seed(directory: Path, labels: Any, *, kind: str = "created") -> Path:
    """A one-record ledger whose label write carries *labels* in *kind*'s shape."""
    payload = (
        {"title": "a lane", "labels": labels}
        if kind == "created"
        else {"name": "labels", "value": labels}
    )
    drafts = [events.Draft(RECORD, "created", {"title": "a lane"})] if kind != "created" else []
    _append(directory, [*drafts, events.Draft(RECORD, kind, payload)])
    return directory


def _split_findings(report: Any) -> list[Any]:
    """Every `split-label` finding in *report*."""
    return [found for found in report.findings if found.kind == fsck.SPLIT_LABEL]


# --- the split contract ---------------------------------------------------------


def test_the_joined_string_shape_is_split_on_the_separator_and_never_iterated() -> None:
    """The whole class in one assertion: `phase-2` is one label, not seven characters."""
    assert label_shape.labels_of("phase-2") == ("phase-2",)
    assert label_shape.labels_of("phase-2,ready") == ("phase-2", "ready")


def test_the_list_shape_passes_through_because_a_created_event_stores_one() -> None:
    """Both shapes are legitimate, so both have to answer the same labels."""
    assert label_shape.labels_of(["phase-2", "ready"]) == ("phase-2", "ready")
    assert label_shape.labels_of(None) == ()


def test_a_label_written_through_the_kit_seam_reads_back_as_the_whole_word(
    ledger: Path,
) -> None:
    """The round trip the bead asked for, over the path that stores the joined shape."""
    record = _root(ledger)

    commands.update(ledger, record, add_labels=["phase-2"])

    stored = commands.queries.folded(ledger)[record].fields["labels"]
    assert isinstance(stored, str), "the field event stores the joined shape, not a list"
    assert label_shape.labels_of(stored) == ("phase-2",)


def test_the_reproduction_that_filed_this_reads_a_healthy_record_as_seven_labels(
    ledger: Path,
) -> None:
    """The instrument, not the population. This is what produced the 44-label histogram."""
    record = _root(ledger)
    commands.update(ledger, record, add_labels=["phase-2"])

    stored = commands.queries.folded(ledger)[record].fields["labels"]

    assert list(stored) == ["p", "h", "a", "s", "e", "-", "2"]
    assert label_shape.labels_of(stored) != tuple(stored)


# --- the checker ----------------------------------------------------------------


def test_a_log_whose_labels_are_whole_words_is_clean(tmp_path: Path) -> None:
    """The control. Without it every red below could be a checker that fails on anything."""
    report = fsck.check(_seed(tmp_path / "ledger", ["phase-2", "ready"]))

    assert _split_findings(report) == []
    assert report.exit_code == fsck.EXIT_CLEAN


def test_a_created_event_carrying_one_character_labels_is_reported_broken(
    tmp_path: Path,
) -> None:
    """A list written by iterating `phase-2` instead of splitting it."""
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
    """The other storage shape, corrupted the same way and caught by the same rule."""
    ledger = _seed(tmp_path / "ledger", "p,h,a,s,e,-,2", kind="field")

    (found,) = _split_findings(fsck.check(ledger))

    assert found.severity == fsck.BROKEN
    assert "7 label(s) of one character" in found.detail


def test_the_finding_names_every_offending_label_and_the_events_carrying_them(
    tmp_path: Path,
) -> None:
    """A finding a reader can act on names the labels, not just the record."""
    ledger = _seed(tmp_path / "ledger", "p,h", kind="field")
    _append(ledger, [events.Draft(RECORD, "field", {"name": "labels", "value": "x"})])

    (found,) = _split_findings(fsck.check(ledger))

    assert "'h'" in found.detail and "'p'" in found.detail and "'x'" in found.detail
    assert len(found.event_ids) == 2, "both writes carry the class, not only the winning one"


def test_a_corrected_record_still_reports_the_write_that_corrupted_it(
    tmp_path: Path,
) -> None:
    """The fold keeps only the winning value, so a checker reading it would report nothing."""
    ledger = _seed(tmp_path / "ledger", "p,h,a,s,e,-,2", kind="field")
    _append(ledger, [events.Draft(RECORD, "field", {"name": "labels", "value": "phase-2"})])

    assert commands.queries.folded(ledger)[RECORD].fields["labels"] == "phase-2"
    assert _split_findings(fsck.check(ledger)) != []

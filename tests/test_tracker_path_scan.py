"""Tests for the tracker-path-scan hook and the export scrubber (basicly-vkh0.5).

Every machine-specific path in this file is assembled by concatenation, so committing it
never self-trips the sibling secret and path scanners.

The export scrubber's own tests left with the export (basicly-vkh0.42.7);
``test_br_scrub_ledger.py`` holds the ledger's, which is the repair that remains.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from basicly import redact

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "tracker-path-scan.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("tracker_path_scan", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan = _load_hook()

# Constructed so no literal machine path lives in this committed file.
POSIX_HOME = "/home" + "/someuser/development/basicly"
MAC_HOME = "/Users" + "/someuser/Development/basicly"
WINDOWS_DRIVE = "C:" + "\\" + "Development" + "\\" + "basicly"
WINDOWS_UNC = "\\" * 2 + "?" + "\\" + WINDOWS_DRIVE

EXPORT = ".beads/issues.jsonl"


def _record(**extra: object) -> str:
    """A tracker record serialized the way br writes the export."""
    base: dict[str, object] = {"id": "basicly-test", "title": "t", "status": "open"}
    base.update(extra)
    return json.dumps(base, separators=(",", ":"), ensure_ascii=False)


def _rules(content: str) -> list[str]:
    """The rule names the gate trips on *content*, in order."""
    return [rule for _, _, rule in scan.findings(EXPORT, content)]


# --- the mirror between the hook and the package ----------------------------


def test_the_hook_rule_set_mirrors_the_package_exactly() -> None:
    """The hook cannot import basicly, so the shared rules are checked, not trusted."""
    assert [(name, p.pattern) for name, p in scan._RULES] == [
        (name, p.pattern) for name, p in redact.MACHINE_PATH_RULES
    ]


# --- the gate ---------------------------------------------------------------


def test_br_source_repo_path_value_is_flagged() -> None:
    """The systematic leak: br's own path field."""
    assert _rules(_record(source_repo_path=POSIX_HOME)) == ["posix-home-path"]


def test_a_posix_home_path_in_prose_is_flagged() -> None:
    """A path pasted into a description leaks exactly as much as the field does."""
    assert _rules(_record(description=f"the export carried {POSIX_HOME}")) == ["posix-home-path"]


def test_a_mac_home_path_is_flagged() -> None:
    """/Users is the same defect as /home on a different platform."""
    assert _rules(_record(description=f"produced under {MAC_HOME}")) == ["posix-home-path"]


def test_a_windows_drive_path_is_flagged() -> None:
    """A working-directory layout is machine-specific even with no username in it."""
    assert _rules(_record(description=f"seen under {WINDOWS_DRIVE}")) == ["windows-drive-path"]


def test_a_windows_unc_path_is_labelled_as_unc_not_drive() -> None:
    """The longer form wins the label, so the report names the shape actually found."""
    assert _rules(_record(source_repo_path=WINDOWS_UNC)) == ["windows-unc-path"]


def test_a_path_nested_in_a_comment_is_flagged() -> None:
    """Comments are exported inside the record, and `br comments` cannot edit one."""
    line = _record(comments=[{"author": "someone", "text": f"prior art: {POSIX_HOME}"}])
    assert _rules(line) == ["posix-home-path"]


def test_a_path_free_record_is_clean() -> None:
    """The gate must not fire on ordinary tracker content."""
    assert _rules(_record(description="provenance is the repo identity, not a location")) == []


def test_a_redacted_placeholder_is_clean() -> None:
    """What the scrubber produces must not re-trip the gate, or landing never converges."""
    assert _rules(_record(description=redact.redact_machine_paths(POSIX_HOME))) == []


def test_the_line_number_reported_is_one_based() -> None:
    """A finding has to be locatable in a 380-line export."""
    content = "\n".join([
        _record(id="basicly-a"),
        _record(id="basicly-b", source_repo_path=MAC_HOME),
    ])
    assert [lineno for _, lineno, _ in scan.findings(EXPORT, content)] == [2]


def test_one_finding_per_record_even_when_several_rules_match() -> None:
    """Noise control: a record carrying both shapes reports once, not twice."""
    line = _record(source_repo_path=POSIX_HOME, description=WINDOWS_DRIVE)
    assert len(scan.findings(EXPORT, line)) == 1


def test_an_unparseable_line_is_scanned_as_raw_text() -> None:
    """A malformed record must not be a way to smuggle a path past the gate."""
    assert _rules("{not json " + POSIX_HOME) == ["posix-home-path"]


def test_a_blank_line_is_not_a_finding() -> None:
    """Trailing newlines in the export are not defects."""
    assert _rules(_record(id="basicly-a") + "\n") == []


def test_only_tracker_jsonl_paths_are_in_scope() -> None:
    """Paths are legitimate content everywhere else, so a wider scan would be noise."""
    assert scan._TRACKER_GLOB.match(".basicly/ledger/events-0001.jsonl")
    assert scan._TRACKER_GLOB.match(".basicly/ledger/events-2026q1.jsonl")
    assert not scan._TRACKER_GLOB.match("docs/requirements/work-tracker.md")
    assert not scan._TRACKER_GLOB.match("tests/fixtures/issues.jsonl")
    # The derived folds are not scanned: they are rebuilt from the log and git-ignored,
    # so a hit in one is a hit the log already carries.
    assert not scan._TRACKER_GLOB.match(".basicly/ledger/snapshot.jsonl")


# --- the redactor -----------------------------------------------------------


def test_redact_machine_paths_replaces_each_shape_with_a_labelled_placeholder() -> None:
    """The placeholder names the rule, matching redact_secrets' convention."""
    assert redact.redact_machine_paths(POSIX_HOME) == "<redacted:posix-home-path>"
    assert redact.redact_machine_paths(WINDOWS_DRIVE) == "<redacted:windows-drive-path>"


def test_redact_machine_paths_consumes_the_layout_not_just_the_username() -> None:
    """The tail of a path is the directory layout, which is machine-specific too."""
    assert "development" not in redact.redact_machine_paths(POSIX_HOME)
    assert "Development" not in redact.redact_machine_paths(WINDOWS_DRIVE)


def test_redact_machine_paths_stops_at_the_end_of_the_path_in_prose() -> None:
    """Surrounding sentence text must survive, or a comment becomes unreadable."""
    redacted = redact.redact_machine_paths(f"prior art: {POSIX_HOME} was the reference")
    assert redacted == "prior art: <redacted:posix-home-path> was the reference"


def test_redact_machine_paths_leaves_path_free_text_untouched() -> None:
    """Text with no machine path must survive byte-identically."""
    text = "the loop derives phase from the tracker"
    assert redact.redact_machine_paths(text) == text


def test_redact_machine_paths_is_idempotent() -> None:
    """Re-running on its own output must be a fixed point, or the export churns."""
    once = redact.redact_machine_paths(f"{POSIX_HOME} and {WINDOWS_DRIVE}")
    assert redact.redact_machine_paths(once) == once

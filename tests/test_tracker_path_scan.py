"""Tests for the tracker-path-scan hook and the export scrubber (basicly-vkh0.5).

Every machine-specific path in this file is assembled by concatenation, so
committing it never self-trips the sibling secret/path scanners — and the hook it
tests only ever reads ``.beads/*.jsonl`` anyway.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from basicly import br, redact

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
    assert scan._TRACKER_GLOB.match(".beads/issues.jsonl")
    assert scan._TRACKER_GLOB.match(".beads/beads.base.jsonl")
    assert not scan._TRACKER_GLOB.match("docs/requirements/work-tracker.md")
    assert not scan._TRACKER_GLOB.match("tests/fixtures/issues.jsonl")


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


# --- the scrubber -----------------------------------------------------------


def _export(tmp_path: Path, *lines: str) -> Path:
    """Write a tracker export under *tmp_path* and return its path."""
    beads = tmp_path / ".beads"
    beads.mkdir()
    export = beads / "issues.jsonl"
    export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return export


def test_scrub_export_removes_the_field_and_counts_records(tmp_path: Path) -> None:
    """The repair strips br's path field from every record that carries it."""
    export = _export(
        tmp_path,
        _record(id="basicly-a", source_repo_path=POSIX_HOME),
        _record(id="basicly-b", source_repo_path=MAC_HOME),
    )
    assert br.scrub_export(tmp_path) == 2
    for line in export.read_text(encoding="utf-8").splitlines():
        assert br.MACHINE_PATH_FIELD not in json.loads(line)


def test_scrub_export_redacts_a_path_nested_in_a_comment(tmp_path: Path) -> None:
    """The only available fix for a comment: `br comments` has no edit or delete."""
    export = _export(
        tmp_path,
        _record(id="basicly-a", comments=[{"text": f"prior art: {POSIX_HOME}"}]),
    )
    assert br.scrub_export(tmp_path) == 1
    record = json.loads(export.read_text(encoding="utf-8").splitlines()[0])
    assert "<redacted:posix-home-path>" in record["comments"][0]["text"]


def test_scrub_export_leaves_every_other_field_byte_identical(tmp_path: Path) -> None:
    """The diff must be only the changed fields — no reformatting of the export."""
    clean = _record(id="basicly-a", description="unicode section sign § and an em dash —")
    export = _export(tmp_path, clean, _record(id="basicly-b", source_repo_path=POSIX_HOME))
    br.scrub_export(tmp_path)
    lines = export.read_text(encoding="utf-8").splitlines()
    assert lines[0] == clean
    assert lines[1] == _record(id="basicly-b")


def test_scrub_export_is_a_no_op_on_an_already_clean_export(tmp_path: Path) -> None:
    """An idempotent repair: nothing to fix means the file is not rewritten."""
    export = _export(tmp_path, _record(id="basicly-a"))
    before = export.read_bytes()
    assert br.scrub_export(tmp_path) == 0
    assert export.read_bytes() == before


def test_scrub_export_reaches_a_fixed_point(tmp_path: Path) -> None:
    """Every engine tracker commit runs it, so a second pass must change nothing."""
    export = _export(
        tmp_path, _record(id="basicly-a", source_repo_path=POSIX_HOME, description=WINDOWS_DRIVE)
    )
    br.scrub_export(tmp_path)
    settled = export.read_bytes()
    assert br.scrub_export(tmp_path) == 0
    assert export.read_bytes() == settled


def test_the_scrubbed_export_passes_the_gate(tmp_path: Path) -> None:
    """The repair and the gate must agree, or a landing can never satisfy both."""
    export = _export(
        tmp_path,
        _record(id="basicly-a", source_repo_path=POSIX_HOME),
        _record(id="basicly-b", comments=[{"text": MAC_HOME}]),
        _record(id="basicly-c", description=f"builds under {WINDOWS_DRIVE}"),
    )
    br.scrub_export(tmp_path)
    assert scan.findings(EXPORT, export.read_text(encoding="utf-8")) == []


def test_scrub_export_tolerates_a_missing_export(tmp_path: Path) -> None:
    """It runs on the commit path, so an absent export must not raise."""
    assert br.scrub_export(tmp_path) == 0


def test_scrub_export_leaves_an_unparseable_line_alone(tmp_path: Path) -> None:
    """A malformed line must not cost tracker state on the commit path."""
    export = _export(tmp_path, "{not json", _record(id="basicly-b", source_repo_path=MAC_HOME))
    assert br.scrub_export(tmp_path) == 1
    lines = export.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "{not json"
    assert lines[1] == _record(id="basicly-b")


def test_scrub_export_preserves_a_trailing_newline(tmp_path: Path) -> None:
    """The export br writes ends with a newline; the repair must not strip it."""
    export = _export(tmp_path, _record(id="basicly-a", source_repo_path=POSIX_HOME))
    br.scrub_export(tmp_path)
    assert export.read_text(encoding="utf-8").endswith("\n")


# --- publishing the scrub is a rename, and a rename can be refused (vkh0.10) ---
#
# The refusal is Windows-only in the wild: `os.replace` needs delete access to the
# destination and CPython opens a file for reading without FILE_SHARE_DELETE, so
# renaming over a file a lane is mid-read raises there and succeeds on POSIX. Both
# tests below make that refusal *test data* rather than a platform, so the rule is
# asserted on every runner instead of only on the one that can produce it.


def test_a_publish_refused_once_retries_rather_than_losing_the_scrub(
    tmp_path: Path, monkeypatch
) -> None:
    """A reader holding the export must delay the repair, never cancel it."""
    export = _export(tmp_path, _record(id="basicly-a", source_repo_path=POSIX_HOME))
    real_replace = Path.replace
    refusals = [PermissionError(32, "The process cannot access the file")]

    def flaky_replace(self: Path, target):
        if refusals:
            raise refusals.pop()
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(br.time, "sleep", lambda _s: None)

    assert br.scrub_export(tmp_path) == 1
    assert export.read_text(encoding="utf-8") == _record(id="basicly-a") + "\n"


def test_a_publish_refused_past_the_deadline_leaves_the_export_whole(
    tmp_path: Path, monkeypatch
) -> None:
    """Failing to repair is safe; a half-written export is not.

    The scrub runs on the commit path and must never be the reason tracker state
    fails to land, so a publish that never wins reports "nothing changed" and leaves
    the file byte-identical. The unrepaired leak is then the ``tracker-path-scan``
    hook's to refuse — a gate failing closed, not a silent half-write.
    """
    dirty = _record(id="basicly-a", source_repo_path=POSIX_HOME)
    export = _export(tmp_path, dirty)
    original = export.read_bytes()

    def always_refused(_self: Path, _target):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(Path, "replace", always_refused)
    monkeypatch.setattr(br.time, "sleep", lambda _s: None)
    # Shorten the deadline rather than waiting it out: with sleep stubbed the loop
    # would otherwise spin for the full production budget of real CPU time.
    monkeypatch.setattr(br, "_PUBLISH_DEADLINE_S", 0.05)

    assert br.scrub_export(tmp_path) == 0
    assert export.read_bytes() == original
    # ...and the abandoned temp file does not become untracked dirt in .beads/.
    assert list((tmp_path / ".beads").glob("*.tmp")) == []

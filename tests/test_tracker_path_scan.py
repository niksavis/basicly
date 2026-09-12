from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from basicly import cli, redact

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "tracker-path-scan.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("tracker_path_scan", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan = _load_hook()

POSIX_HOME = "/home" + "/someuser/development/basicly"
MAC_HOME = "/Users" + "/someuser/Development/basicly"
WINDOWS_DRIVE = "C:" + "\\" + "Development" + "\\" + "basicly"
WINDOWS_UNC = "\\" * 2 + "?" + "\\" + WINDOWS_DRIVE

LEDGER = ".basicly/ledger/events-0001.jsonl"


def _record(**extra: object) -> str:
    base: dict[str, object] = {"id": "basicly-test", "title": "t", "status": "open"}
    base.update(extra)
    return json.dumps(base, separators=(",", ":"), ensure_ascii=False)


def _rules(content: str) -> list[str]:
    return [rule for _, _, rule in scan.findings(LEDGER, content)]


def test_the_hook_rule_set_mirrors_the_package_exactly() -> None:
    assert [(name, p.pattern) for name, p in scan._RULES] == [
        (name, p.pattern) for name, p in redact.MACHINE_PATH_RULES
    ]


def test_br_source_repo_path_value_is_flagged() -> None:
    assert _rules(_record(source_repo_path=POSIX_HOME)) == ["posix-home-path"]


def test_a_posix_home_path_in_prose_is_flagged() -> None:
    assert _rules(_record(description=f"the export carried {POSIX_HOME}")) == ["posix-home-path"]


def test_a_mac_home_path_is_flagged() -> None:
    assert _rules(_record(description=f"produced under {MAC_HOME}")) == ["posix-home-path"]


def test_a_windows_drive_path_is_flagged() -> None:
    assert _rules(_record(description=f"seen under {WINDOWS_DRIVE}")) == ["windows-drive-path"]


def test_a_windows_unc_path_is_labelled_as_unc_not_drive() -> None:
    assert _rules(_record(source_repo_path=WINDOWS_UNC)) == ["windows-unc-path"]


def test_a_path_nested_in_a_comment_is_flagged() -> None:
    line = _record(comments=[{"author": "someone", "text": f"prior art: {POSIX_HOME}"}])
    assert _rules(line) == ["posix-home-path"]


def test_a_path_free_record_is_clean() -> None:
    assert _rules(_record(description="provenance is the repo identity, not a location")) == []


def test_a_redacted_placeholder_is_clean() -> None:
    assert _rules(_record(description=redact.redact_machine_paths(POSIX_HOME))) == []


def test_the_line_number_reported_is_one_based() -> None:
    content = "\n".join([
        _record(id="basicly-a"),
        _record(id="basicly-b", source_repo_path=MAC_HOME),
    ])
    assert [lineno for _, lineno, _ in scan.findings(LEDGER, content)] == [2]


def test_one_finding_per_record_even_when_several_rules_match() -> None:
    line = _record(source_repo_path=POSIX_HOME, description=WINDOWS_DRIVE)
    assert len(scan.findings(LEDGER, line)) == 1


def test_an_unparseable_line_is_scanned_as_raw_text() -> None:
    assert _rules("{not json " + POSIX_HOME) == ["posix-home-path"]


def test_a_blank_line_is_not_a_finding() -> None:
    assert _rules(_record(id="basicly-a") + "\n") == []


def test_only_tracker_jsonl_paths_are_in_scope() -> None:
    assert scan._TRACKER_GLOB.match(".basicly/ledger/events-0001.jsonl")
    assert scan._TRACKER_GLOB.match(".basicly/ledger/events-2026q1.jsonl")
    assert not scan._TRACKER_GLOB.match("docs/architecture/status.md")
    assert not scan._TRACKER_GLOB.match("tests/fixtures/issues.jsonl")
    assert not scan._TRACKER_GLOB.match(".basicly/ledger/snapshot.jsonl")


def test_redact_machine_paths_replaces_each_shape_with_a_labelled_placeholder() -> None:
    assert redact.redact_machine_paths(POSIX_HOME) == "<redacted:posix-home-path>"
    assert redact.redact_machine_paths(WINDOWS_DRIVE) == "<redacted:windows-drive-path>"


def test_redact_machine_paths_consumes_the_layout_not_just_the_username() -> None:
    assert "development" not in redact.redact_machine_paths(POSIX_HOME)
    assert "Development" not in redact.redact_machine_paths(WINDOWS_DRIVE)


def test_redact_machine_paths_stops_at_the_end_of_the_path_in_prose() -> None:
    redacted = redact.redact_machine_paths(f"prior art: {POSIX_HOME} was the reference")
    assert redacted == "prior art: <redacted:posix-home-path> was the reference"


def test_redact_machine_paths_leaves_path_free_text_untouched() -> None:
    text = "the loop derives phase from the tracker"
    assert redact.redact_machine_paths(text) == text


def test_redact_machine_paths_is_idempotent() -> None:
    once = redact.redact_machine_paths(f"{POSIX_HOME} and {WINDOWS_DRIVE}")
    assert redact.redact_machine_paths(once) == once


def test_the_printed_repair_names_a_command_the_cli_accepts(monkeypatch, capsys) -> None:

    monkeypatch.setattr(scan, "staged_tracker_files", lambda: [LEDGER])
    monkeypatch.setattr(scan, "staged_content", lambda _path: _record(path=POSIX_HOME))

    assert scan.main() == 1

    remedy = capsys.readouterr().err
    match = re.search(r"Repair it with:\s+basicly ([a-z-]+) ([a-z-]+)", remedy)
    assert match, f"the remedy no longer names a `basicly` command:\n{remedy}"
    parsed = cli._build_parser().parse_args(list(match.groups()))
    assert (parsed.command, parsed.tracker_command) == match.groups()

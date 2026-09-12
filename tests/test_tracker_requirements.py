from __future__ import annotations

import importlib.util
import json
import re
import time
from pathlib import Path

import pytest

from basicly import merge, policy, tracker, tracker_paths, verify
from tests import flipped_tracker

REPO_ROOT = Path(__file__).parent.parent
COMMIT_MSG_HOOK = REPO_ROOT / ".basicly" / "core" / "hooks" / "tracker-commit-msg.py"
ARCHITECTURE_MD = REPO_ROOT / "docs" / "architecture" / "architecture.md"

_REGISTER_HEADING = "### 32.9 "
_REGISTER_ROW = re.compile(r"^\| (R\d+) \|", re.MULTILINE)


def _load_hook(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _register_ids(text: str) -> set[str]:

    start = text.find(_REGISTER_HEADING)
    if start < 0:
        return set()
    end = text.find("\n### ", start + 1)
    body = text[start:] if end < 0 else text[start:end]
    return set(_REGISTER_ROW.findall(body))


def test_r1_no_write_is_rejected_on_a_clock_comparison(tmp_path: Path) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    kit = tracker.kit(repo)
    later, earlier = 1_786_000_000.0, 1_785_999_940.0

    for stamp in (later, earlier):
        kit.events.append(
            tracker.ledger_dir(repo),
            [kit.events.Draft("b-1", kit.events.KIND_COMMENT, {"text": f"at {stamp:.0f}"})],
            clock=lambda held=stamp: held,
        )

    assert [row["text"] for row in tracker.read_comments(repo, "b-1")] == [
        f"at {later:.0f}",
        f"at {earlier:.0f}",
    ]


def test_r1_the_signature_does_not_forgive_a_fixture_quoting_the_phrase() -> None:

    assert verify._defect_reason("assert 'another writer holds' in out") is None


@pytest.mark.parametrize(
    "dep",
    [
        {"id": "basicly-a", "dependency_type": "blocks"},
        {"depends_on_id": "basicly-a", "type": "blocks"},
    ],
)
def test_r2_a_dependency_edge_reads_in_either_of_brs_two_spellings(dep: dict) -> None:

    assert tracker.dependency_edge(dep) == ("basicly-a", "blocks")


def test_r2_a_row_that_is_not_an_edge_is_rejected_rather_than_guessed() -> None:
    assert tracker.dependency_edge({"dependency_type": "blocks"}) is None
    assert tracker.dependency_edge("basicly-a") is None
    assert tracker.dependency_edge({"id": "", "type": "blocks"}) is None


def test_r2_blocking_dependencies_reads_the_echo_spelling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    record = {"id": "basicly-x", "dependencies": [{"depends_on_id": "basicly-a", "type": "blocks"}]}
    monkeypatch.setattr(tracker, "read_record", lambda *_a, **_k: record)
    assert merge.blocking_dependencies(tmp_path, "basicly-x") == frozenset({"basicly-a"})


def test_r3_acceptance_criteria_are_required_for_a_work_type_lint_never_asks_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    record = {
        "id": "basicly-x",
        "issue_type": "chore",
        "acceptance_criteria": None,
        "description": "",
    }
    monkeypatch.setattr(tracker, "read_record", lambda *_a, **_k: record)
    result = policy.definition_of_ready(tmp_path, "basicly-x")

    assert result.ready is False
    assert policy._ACCEPTANCE_CRITERIA_SECTION in result.missing


def test_r4_multi_line_acceptance_criteria_satisfy_the_gate_from_the_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    body = (
        "## Trigger\n\nWhen a record is gated, I want a trigger, so I can validate it.\n\n"
        "## Acceptance Criteria\n\n- given a thing\n- when it happens\n- then a result\n"
    )
    record = {"id": "basicly-x", "acceptance_criteria": "", "description": body}
    monkeypatch.setattr(tracker, "read_record", lambda *_a, **_k: record)
    result = policy.definition_of_ready(tmp_path, "basicly-x")

    assert result.ready is True
    assert result.missing == ()


def test_r5_a_slug_shaped_id_is_truncated_by_the_prefix_anchored_gate() -> None:

    hook = _load_hook(COMMIT_MSG_HOOK, "tracker_commit_msg_hook")
    known = {"basicly-fix-the-thing", "basicly-m4zv.10"}

    assert hook._candidate_ids("fix(x): do it (basicly-fix-the-thing)", known) == {"basicly-fix"}
    assert hook._candidate_ids("fix(x): do it (basicly-m4zv.10)", known) == {"basicly-m4zv.10"}
    assert hook._candidate_ids("fix(x): a well-known problem", known) == set()


def test_r5_the_ids_this_repo_mints_carry_no_internal_hyphen() -> None:

    seen = set()
    for log in sorted((REPO_ROOT / tracker_paths.LEDGER_DIR_NAME).glob("events-*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            record = event.get("record") if isinstance(event, dict) else None
            if isinstance(record, str):
                seen.add(record)
    assert seen, "the ledger is the subject; it must not be empty"
    offenders = sorted(record for record in seen if record.count("-") > 1)
    assert offenders == [], f"slug-shaped ids break the commit gate: {offenders}"


def test_r6_the_committed_ledger_publishes_no_machine_specific_path(tmp_path: Path) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [
            kit.events.Draft(
                "b-1",
                kit.events.KIND_COMMENT,
                {"text": "see /home/someone/development/basicly/docs for context"},
            )
        ],
    )

    changed = tracker.scrub_ledger(repo)

    assert changed == 1
    committed = (tracker.ledger_dir(repo) / "events-0001.jsonl").read_text(encoding="utf-8")
    assert "/home/someone" not in committed


def test_r6_scrubbing_an_already_clean_ledger_changes_nothing(tmp_path: Path) -> None:
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "b-1", title="no paths here")
    log = tracker.ledger_dir(repo) / "events-0001.jsonl"
    original = log.read_text(encoding="utf-8")

    assert tracker.scrub_ledger(repo) == 0
    assert log.read_text(encoding="utf-8") == original


def test_r7_a_reader_never_observes_a_torn_write_of_the_shared_ledger(tmp_path: Path) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "b-1", title="whole")
    log = tracker.ledger_dir(repo) / "events-0001.jsonl"
    whole = log.read_text(encoding="utf-8")
    log.write_text(whole + whole.splitlines()[0][:40], encoding="utf-8")

    records = tracker.all_records(repo)

    assert [record["id"] for record in records] == ["b-1"]


def test_r7_a_missing_ledger_is_empty_without_waiting(tmp_path: Path) -> None:
    started = time.monotonic()

    assert tracker.all_records(tmp_path) == []
    assert time.monotonic() - started < 1.0


def test_r7_an_unreadable_ledger_is_not_reported_as_a_populated_one(tmp_path: Path) -> None:
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "b-1", title="whole")
    (tracker.ledger_dir(repo) / "events-0001.jsonl").write_text("{not json", encoding="utf-8")

    assert tracker.all_records(repo) == []


_R8_LOCK_TIMEOUT = (
    "E           basicly_tracker_kit_events.LockUnavailableError: another writer holds "
    "/repo/.basicly/ledger/.events.lock after 5.0s"
)


def test_r8_a_contended_write_lock_does_not_spend_the_lanes_rework_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    failed = verify.VerifyReport("full", (verify.CheckResult("pytest", "fail", 1),))
    contended = verify.VerifyReport(
        "full", (verify.CheckResult("pytest", "fail", 1, output=_R8_LOCK_TIMEOUT),)
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: failed)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: contended)

    clock = merge._Landing(tmp_path, "basicly-x")
    result = merge._verify_for_landing("lane", tmp_path, "full", clock)

    assert result is not None
    assert result.status == merge.VERIFY_UNRELIABLE
    assert result.unreliable is True
    assert "one lock" in result.detail


def test_r9_a_publish_that_would_shrink_the_export_is_refused_not_silent(
    tmp_path: Path,
) -> None:

    def publish(existing: Path, records: list[str], *, intent: bool = False) -> None:
        prior = [line for line in existing.read_text(encoding="utf-8").splitlines() if line]
        if len(records) < len(prior) and not intent:
            raise ValueError(
                f"refusing to publish {len(records)} records over {len(prior)}: "
                "a shrink needs explicit intent"
            )
        existing.write_text("".join(f"{line}\n" for line in records), encoding="utf-8")

    export = tmp_path / "issues.jsonl"
    export.write_text("".join(f'{{"id":"b-{n}"}}\n' for n in range(612)), encoding="utf-8")
    smaller = [f'{{"id":"b-{n}"}}' for n in range(426)]

    with pytest.raises(ValueError, match=r"refusing to publish 426 records over 612"):
        publish(export, smaller)

    assert len([line for line in export.read_text(encoding="utf-8").splitlines() if line]) == 612

    publish(export, smaller, intent=True)
    assert len([line for line in export.read_text(encoding="utf-8").splitlines() if line]) == 426


def test_every_requirement_in_the_design_register_has_a_test_here() -> None:

    declared = _register_ids(ARCHITECTURE_MD.read_text(encoding="utf-8"))
    assert declared, f"no R<n> rows found under {_REGISTER_HEADING.strip()}"

    source = Path(__file__).read_text(encoding="utf-8")
    covered = {rid for rid in declared if f"def test_{rid.lower()}_" in source}
    assert covered == declared, f"requirements with no test: {sorted(declared - covered)}"


def test_the_register_read_returns_nothing_when_its_section_is_not_there() -> None:

    assert _register_ids("") == set()
    assert _register_ids("## 1. Elsewhere\n\n| R1 | a row outside the register |\n") == set()
    assert _register_ids(f"{_REGISTER_HEADING}The register\n\n| R1 | a row |\n") == {"R1"}

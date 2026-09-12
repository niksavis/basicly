from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

from basicly import artifact_record, catalog_source, handoff, merge, tracker
from basicly.checkout import git
from basicly.decompose import CreatedChild, DecomposeResult
from tests import flipped_tracker, plan_fixtures
from tests.test_artifact_record import artifact_events, legacy_marker, record_marker


class _FakeBr:
    def __init__(self) -> None:
        self.comments: dict[str, list[str]] = {}

    def add(self, _repo_root: Path, issue_id: str, body: str) -> None:
        self.comments.setdefault(issue_id, []).append(body)

    def read(self, _repo_root: Path, issue_id: str) -> list[dict]:
        return [{tracker.COMMENT_TEXT_KEY: text} for text in self.comments.get(issue_id, [])]


@pytest.fixture
def fake_br(monkeypatch: pytest.MonkeyPatch) -> _FakeBr:
    fake = _FakeBr()
    monkeypatch.setattr(tracker, "add_comment", fake.add)
    monkeypatch.setattr(tracker, "read_comments", fake.read)
    return fake


spec = plan_fixtures.planned


def decomposition() -> DecomposeResult:
    first = CreatedChild("proj-feat.1", spec("a"), 0, ())
    second = CreatedChild("proj-feat.2", spec("b"), 1, ("proj-feat.1",))
    return DecomposeResult("proj-feat", (first, second), (("proj-feat.1",), ("proj-feat.2",)))


@pytest.fixture(autouse=True)
def _open_the_synthetic_records(request: pytest.FixtureRequest) -> None:

    if "work_repo" not in request.fixturenames:
        return
    repo = request.getfixturevalue("work_repo")
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in ("proj-feat", "proj-i")
        ],
    )


def summary() -> dict:
    return handoff.summary_payload(
        "proj-i",
        "carry the plan into build",
        ("abc1234", ("src/basicly/handoff.py",)),
        handoff.SelfCheck("merged", "landed", passed=True),
    )


def test_plan_payload_carries_every_gated_field_and_the_graph(work_repo: Path) -> None:
    payload = handoff.plan_payload(decomposition())
    assert payload["feature"] == "proj-feat"
    assert payload["groups"] == [["proj-feat.1"], ["proj-feat.2"]]
    first = payload["tasks"][0]
    assert first["issue_id"] == "proj-feat.1"
    assert first["acceptance"] == ["given a plan when it is gated then it passes"]
    assert first["scope"] == ["src/a.py"]
    assert first["budget_tokens"] == 40_000
    assert first["integrity"] == "L2"
    assert first["demonstration"] == plan_fixtures.DEMONSTRATION
    assert payload["tasks"][1]["depends_on"] == ["proj-feat.1"]
    assert handoff.adopted(work_repo, handoff.IMPLEMENTATION_PLAN)


def _artifact_bodies(repo: Path, record: str) -> list[object]:
    return [event.payload.get(tracker.ARTIFACT_BODY_KEY) for event in artifact_events(repo, record)]


def test_a_sound_plan_records_and_reads_back_admitted(work_repo: Path) -> None:
    payload = handoff.plan_payload(decomposition())
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)

    assert _artifact_bodies(work_repo, "proj-feat") == [payload]
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert verdict.admitted and verdict.reason == ""


def test_a_plan_far_over_the_marker_cap_is_admitted_by_the_entry_predicate(
    work_repo: Path,
) -> None:

    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["acceptance"] = [
        f"given case {index} then it holds" for index in range(640)
    ]
    assert len(json.dumps(payload).encode("utf-8")) > 20_000

    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)

    assert artifact_record.read(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN) == payload
    assert handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted


def test_a_plan_missing_a_gated_field_is_refused_before_it_is_written(work_repo: Path) -> None:
    payload = handoff.plan_payload(decomposition())
    del payload["tasks"][0]["budget_tokens"]
    with pytest.raises(handoff.ArtifactError) as caught:
        handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    assert "budget_tokens" in caught.value.verdict.reason
    assert _artifact_bodies(work_repo, "proj-feat") == []


def test_recording_the_same_artifact_twice_writes_one_event(work_repo: Path) -> None:
    payload = handoff.plan_payload(decomposition())
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    assert len(_artifact_bodies(work_repo, "proj-feat")) == 1


def test_the_last_recorded_plan_is_the_one_read_back(work_repo: Path) -> None:
    handoff.record(
        work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    superseding = handoff.plan_payload(decomposition())
    superseding["tasks"] = superseding["tasks"][:1]
    superseding["groups"] = [["proj-feat.1"]]
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, superseding)
    recorded = artifact_record.read(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert isinstance(recorded, dict) and len(recorded["tasks"]) == 1


def test_a_unit_with_no_artifact_is_admitted(work_repo: Path) -> None:
    assert handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted


def test_an_unrelated_marker_family_is_not_an_artifact(work_repo: Path) -> None:
    record_marker(work_repo, "proj-feat", "[harness-policy] checkpoint=decompose approved")
    assert artifact_record.read(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN) is None


def test_the_other_kind_of_artifact_is_not_read_as_this_one(work_repo: Path) -> None:
    handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, summary())
    assert artifact_record.read(work_repo, "proj-i", handoff.IMPLEMENTATION_PLAN) is None
    assert artifact_record.read(work_repo, "proj-i", handoff.CHANGE_SUMMARY) is not None


def test_a_repo_without_the_schema_runs_neither_end(tmp_path: Path) -> None:

    assert not handoff.adopted(tmp_path, handoff.IMPLEMENTATION_PLAN)
    handoff.record(
        tmp_path, "proj-feat", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    assert not (tmp_path / tracker.LEDGER_DIR).exists()
    assert handoff.entry_verdict(tmp_path, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted


UNWIRED = tuple(kind for kind, producer in handoff.PRODUCERS.items() if producer is None)


def test_a_kind_no_producer_records_is_reported_unwired_and_not_counted_as_a_contract(
    work_repo: Path,
) -> None:

    installed = [
        kind
        for kind in UNWIRED
        if (work_repo / catalog_source.SCHEMAS_DIR / f"{kind}.schema.json").is_file()
    ]
    assert len(UNWIRED) == 5
    assert len(installed) == 4
    assert [kind for kind in UNWIRED if handoff.wired(kind)] == []
    assert [kind for kind in handoff.PRODUCERS if not handoff.adopted(work_repo, kind)] == list(
        UNWIRED
    )


def test_an_unwired_kind_writes_nothing_and_refuses_nothing(work_repo: Path) -> None:

    handoff.record(work_repo, "proj-u", "change-shape", {"not": "a change shape"})

    assert _artifact_bodies(work_repo, "proj-u") == []
    assert handoff.entry_verdict(work_repo, "proj-u", "change-shape").admitted


def _package_modules() -> dict[str, ast.Module]:

    package = Path(handoff.__file__).parent
    return {path.stem: ast.parse(path.read_text(encoding="utf-8")) for path in package.glob("*.py")}


def _called(modules: dict[str, ast.Module]) -> set[str]:
    funcs = (
        node.func
        for tree in modules.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )
    return {
        func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "") for func in funcs
    }


def _defined(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    return next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def test_a_declared_producer_that_stopped_recording_its_kind_is_a_defect_not_unwired() -> None:

    modules = _package_modules()
    called = _called(modules)
    assert len(modules) > 50, len(modules)
    assert "entry_verdict" in called

    defects = []
    for kind, producer in handoff.PRODUCERS.items():
        if producer is None:
            continue
        module, _, function = producer.partition(":")
        tree = modules.get(module)
        node = _defined(tree, function) if tree is not None else None
        constant = kind.replace("-", "_").upper()
        if node is None:
            defects.append(f"{kind}: {producer} defines no such function")
        elif getattr(handoff, constant, None) != kind:
            defects.append(f"{kind}: no constant named {constant} spells it")
        elif constant not in {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}:
            defects.append(f"{kind}: {producer} never names {constant}")
        elif function not in called:
            defects.append(f"{kind}: {producer} is never called")

    assert defects == []


def test_a_derived_change_summary_records_and_reads_back_admitted(work_repo: Path) -> None:
    handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, summary())
    assert handoff.entry_verdict(work_repo, "proj-i", handoff.CHANGE_SUMMARY).admitted


def test_a_build_that_changed_nothing_has_no_summary_to_hand_on(work_repo: Path) -> None:
    payload = handoff.summary_payload(
        "proj-i", "why", ("abc1234", ()), handoff.SelfCheck("merged", "landed", passed=True)
    )
    with pytest.raises(handoff.ArtifactError) as caught:
        handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    assert "changed" in caught.value.verdict.reason


def test_the_changed_paths_are_carried_as_a_count_and_a_digest_not_as_the_list() -> None:
    payload = summary()
    assert "changed" not in payload
    assert payload["changed_count"] == 1
    assert re.fullmatch("[0-9a-f]{64}", payload["changed_digest"])


def _lane_commit(repo: Path, paths: tuple[str, ...]) -> str:
    git(["init", "-q", "-b", "main"], cwd=repo)
    git(["config", "user.email", "tester@example.invalid"], cwd=repo)
    git(["config", "user.name", "tester"], cwd=repo)
    git(["commit", "-q", "--allow-empty", "-m", "base"], cwd=repo)
    git(["checkout", "-q", "-b", "harness/proj-i"], cwd=repo)
    for path in paths:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("the lane's work\n", encoding="utf-8")
    git(["add", *paths], cwd=repo)
    git(["commit", "-q", "-m", "the lane's work"], cwd=repo)
    return git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()


def test_a_reader_derives_the_changed_paths_from_the_commit_the_summary_carries(
    work_repo: Path,
) -> None:

    head = _lane_commit(work_repo, ("lane/a.py", "lane/b.py"))
    changed = merge.branch_changed_paths(work_repo, "main", "harness/proj-i")
    payload = handoff.summary_payload(
        "proj-i", "why", (head, changed), handoff.SelfCheck("merged", "landed", passed=True)
    )
    handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)

    derived = git(["show", "--name-only", "--format=", head], cwd=work_repo).stdout.split()
    digest = hashlib.sha256("\n".join(sorted(derived)).encode("utf-8")).hexdigest()
    stored = _artifact_bodies(work_repo, "proj-i")[-1]
    assert sorted(derived) == ["lane/a.py", "lane/b.py"]
    assert stored == payload
    assert (payload["changed_count"], payload["changed_digest"]) == (len(derived), digest)


def test_a_summary_written_before_the_list_was_dropped_is_still_accepted(
    work_repo: Path,
) -> None:

    payload = summary()
    payload["changed"] = ["src/basicly/handoff.py"]
    del payload["changed_count"], payload["changed_digest"]
    artifact_record.write(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    assert handoff.entry_verdict(work_repo, "proj-i", handoff.CHANGE_SUMMARY).admitted


def test_a_four_hundred_file_lane_is_stored_in_under_a_kilobyte(work_repo: Path) -> None:

    payload = handoff.summary_payload(
        "proj-i",
        "touch four hundred files",
        ("abc1234", tuple(f"src/basicly/generated_{index}.py" for index in range(400))),
        handoff.SelfCheck("merged", "landed", passed=True),
    )
    handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    body = _artifact_bodies(work_repo, "proj-i")[-1]
    assert payload["changed_count"] == 400
    stored = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert len(stored.encode("utf-8")) < 1000


def _stored_on_a_real_ledger(repo: Path, record: str, body: str) -> None:
    flipped_tracker.seed(repo, record, title="a recorded feature")
    record_marker(repo, record, body)


def _stored_text(repo: Path, record: str) -> str:
    return str(tracker.read_comments(repo, record)[-1][tracker.COMMENT_TEXT_KEY])


def test_a_plan_the_cap_cut_is_refused_naming_the_truncation_and_both_byte_counts(
    work_repo: Path,
) -> None:

    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["acceptance"] = ["y" * 6000]
    body = legacy_marker(handoff.IMPLEMENTATION_PLAN, payload)
    _stored_on_a_real_ledger(work_repo, "proj-cut", body)

    verdict = handoff.entry_verdict(work_repo, "proj-cut", handoff.IMPLEMENTATION_PLAN)
    stored = len(_stored_text(work_repo, "proj-cut").encode("utf-8"))
    assert not verdict.admitted
    assert "truncated" in verdict.reason
    assert str(stored) in verdict.reason
    assert str(len(body.encode("utf-8"))) in verdict.reason
    assert "re-record" in verdict.reason
    assert "is not of type" not in verdict.reason


def test_a_malformed_plan_the_cap_left_whole_keeps_the_reason_it_already_had(
    work_repo: Path,
) -> None:

    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["integrity"] = "L9"
    body = legacy_marker(handoff.IMPLEMENTATION_PLAN, payload)
    _stored_on_a_real_ledger(work_repo, "proj-whole", body)

    verdict = handoff.entry_verdict(work_repo, "proj-whole", handoff.IMPLEMENTATION_PLAN)
    assert _stored_text(work_repo, "proj-whole") == body
    assert not verdict.admitted
    assert "L9" in verdict.reason
    assert "truncated" not in verdict.reason


def test_a_sound_plan_on_a_real_ledger_is_still_admitted(work_repo: Path) -> None:
    body = legacy_marker(handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition()))
    _stored_on_a_real_ledger(work_repo, "proj-sound", body)

    assert handoff.entry_verdict(work_repo, "proj-sound", handoff.IMPLEMENTATION_PLAN).admitted

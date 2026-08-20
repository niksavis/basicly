"""The handoff artifact contract itself: what validates, what is refused, what is inert.

Every gate assertion here is a control pair — the same unit with a sound artifact and
with a corrupted one — because a predicate that only ever sees good input cannot be
shown to bind. The corruption is always applied to the *recorded* artifact, never to the
payload on its way in: the producer validates before it writes, so a defect the consumer
can actually meet has to arrive the way a hand-edit or an older producer would leave it.
Two populations answer that, and both are seeded here: an ``artifact`` event recorded
straight through :func:`artifact_record.write`, and a retired ``[harness-artifact]``
marker, which is the only one the 4096-byte cap can still have cut.

``work_repo`` rather than ``tmp_path`` throughout, because the schemas are catalog
sources: a repo that has not installed them runs neither end of the contract, and a
test on ``tmp_path`` would assert against a pair of no-ops.

Every id here carries a prefix because the owned store validates one
(``ids.RECORD_ID_PATTERN``) and the external binary did not: since ``[tracker] mode``
became ``owned`` the shorthand these fixtures used is refused at the write.

The loop states that produce and consume these artifacts are ``test_handoff_states.py``,
on the same boundary the module itself is drawn along: nothing here advances a phase,
and nothing there re-asserts the schema.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

from basicly import artifact_record, catalog_source, handoff, merge, plan_gate, tracker
from basicly.checkout import git
from basicly.decompose import CreatedChild, DecomposeResult
from tests import flipped_tracker, plan_fixtures
from tests.test_artifact_record import artifact_events, legacy_marker, record_marker


class _FakeBr:
    """Stand-in for the comment surface, kept for the markers a loop state still writes.

    No artifact travels here any more — that is an ``artifact`` event since
    `basicly-pp7q4i`, and every test below records one through the real ledger
    ``work_repo`` carries. What still needs a stand-in is the ``[harness-*]`` traffic
    around it, which is why `test_handoff_states` imports this and the fixture from here.
    """

    def __init__(self) -> None:
        self.comments: dict[str, list[str]] = {}

    def add(self, _repo_root: Path, issue_id: str, body: str) -> None:
        self.comments.setdefault(issue_id, []).append(body)

    def read(self, _repo_root: Path, issue_id: str) -> list[dict]:
        return [{tracker.COMMENT_TEXT_KEY: text} for text in self.comments.get(issue_id, [])]


@pytest.fixture
def fake_br(monkeypatch: pytest.MonkeyPatch) -> _FakeBr:
    """Route the marker seam — ``add_comment``/``read_comments`` — at a stateful fake."""
    fake = _FakeBr()
    monkeypatch.setattr(tracker, "add_comment", fake.add)
    monkeypatch.setattr(tracker, "read_comments", fake.read)
    return fake


# The child that passes the plan gate is ``plan_fixtures.planned``, not a copy of it: an
# artifact test whose fixture drifted from the gate's would assert the schema against a
# plan the gate would have refused, which is the one thing this contract may not do.
spec = plan_fixtures.planned


def decomposition() -> DecomposeResult:
    """A two-child decomposition in the shape ``decompose`` returns one."""
    first = CreatedChild("proj-feat.1", spec("a"), 0, ())
    second = CreatedChild("proj-feat.2", spec("b"), 1, ("proj-feat.1",))
    return DecomposeResult("proj-feat", (first, second), (("proj-feat.1",), ("proj-feat.2",)))


def summary() -> dict:
    """A change-summary as a finished landing derives one."""
    return handoff.summary_payload(
        "proj-i",
        "carry the plan into build",
        ("abc1234", ("src/basicly/handoff.py",)),
        handoff.SelfCheck("merged", "landed", passed=True),
    )


# --- the implementation-plan payload ----------------------------------------


def test_plan_payload_carries_every_gated_field_and_the_graph(work_repo: Path) -> None:
    """The artifact says what the children were created under, plus their grouping."""
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
    """Every ``artifact`` event body recorded on *record*, in file order."""
    return [event.payload.get(tracker.ARTIFACT_BODY_KEY) for event in artifact_events(repo, record)]


def test_a_sound_plan_records_and_reads_back_admitted(work_repo: Path) -> None:
    """The round trip: DECOMPOSE writes the artifact and BUILD's entry accepts it."""
    payload = handoff.plan_payload(decomposition())
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)

    assert _artifact_bodies(work_repo, "proj-feat") == [payload]
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert verdict.admitted and verdict.reason == ""


def test_a_plan_far_over_the_marker_cap_is_admitted_by_the_entry_predicate(
    work_repo: Path,
) -> None:
    """The demonstration, and it is red before the typed event: 20,000 bytes is 5x the cap.

    A 33-child decomposition renders about 21,890 characters (measured 2026-08-08), so this
    is the real shape rather than a stress case — and under the marker transport it came
    back as JSON cut mid-token, which is what refused 23 stored record-and-kind pairs.
    """
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["acceptance"] = [
        f"given case {index} then it holds" for index in range(640)
    ]
    assert len(json.dumps(payload).encode("utf-8")) > 20_000

    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)

    assert artifact_record.read(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN) == payload
    assert handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted


def test_a_plan_missing_a_gated_field_is_refused_before_it_is_written(work_repo: Path) -> None:
    """A payload the consumer would refuse never becomes an artifact in the first place."""
    payload = handoff.plan_payload(decomposition())
    del payload["tasks"][0]["budget_tokens"]
    with pytest.raises(handoff.ArtifactError) as caught:
        handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    assert "budget_tokens" in caught.value.verdict.reason
    assert _artifact_bodies(work_repo, "proj-feat") == []


def test_recording_the_same_artifact_twice_writes_one_event(work_repo: Path) -> None:
    """A state re-entered on every advance must not bury its artifact under copies."""
    payload = handoff.plan_payload(decomposition())
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    assert len(_artifact_bodies(work_repo, "proj-feat")) == 1


def test_the_last_recorded_plan_is_the_one_read_back(work_repo: Path) -> None:
    """Re-decomposed under a changed plan, BUILD is held to the plan that made the children."""
    handoff.record(
        work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    superseding = handoff.plan_payload(decomposition())
    superseding["tasks"] = superseding["tasks"][:1]
    superseding["groups"] = [["proj-feat.1"]]
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, superseding)
    recorded = artifact_record.read(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert isinstance(recorded, dict) and len(recorded["tasks"]) == 1


# --- the ratchet, and the population it discriminates ------------------------


def test_a_unit_with_no_artifact_is_admitted(work_repo: Path) -> None:
    """Absence predates the rule: a feature decomposed before this existed still builds."""
    assert handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted


def test_an_unrelated_marker_family_is_not_an_artifact(work_repo: Path) -> None:
    """Only this family's markers are read: a policy or run marker is not a handoff."""
    record_marker(work_repo, "proj-feat", "[harness-policy] checkpoint=decompose approved")
    assert artifact_record.read(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN) is None


def test_the_other_kind_of_artifact_is_not_read_as_this_one(work_repo: Path) -> None:
    """The kind is a field, so one family carries both without either answering for the other."""
    handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, summary())
    assert artifact_record.read(work_repo, "proj-i", handoff.IMPLEMENTATION_PLAN) is None
    assert artifact_record.read(work_repo, "proj-i", handoff.CHANGE_SUMMARY) is not None


def test_a_repo_without_the_schema_runs_neither_end(tmp_path: Path) -> None:
    """The contract is a catalog source: uninstalled, it writes nothing and refuses nothing.

    ``tmp_path`` carries no ledger either, so *both* ends would raise if they reached the
    store — which makes this the assertion that neither reached it, not only that neither
    complained.
    """
    assert not handoff.adopted(tmp_path, handoff.IMPLEMENTATION_PLAN)
    handoff.record(
        tmp_path, "proj-feat", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    assert not (tmp_path / tracker.LEDGER_DIR).exists()
    assert handoff.entry_verdict(tmp_path, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted


# --- which of the named kinds are wired at all -------------------------------

UNWIRED = tuple(kind for kind, producer in handoff.PRODUCERS.items() if producer is None)


def test_a_kind_no_producer_records_is_reported_unwired_and_not_counted_as_a_contract(
    work_repo: Path,
) -> None:
    """Five of the eight named kinds, in a repo that has installed every schema there is.

    ``work_repo`` is what makes this discriminate: four of the five have a schema file on
    disk here and would resolve, which is the whole defect — seven schemas were reading as
    seven live contracts. The wired three are the second control, because an assertion that
    nothing is adopted would pass just as well on a repo where nothing is installed at all.
    """
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
    """Inert at both ends, through the one seam each of them resolves a schema at.

    The payload is malformed on purpose: a kind whose schema still resolved would raise
    ``ArtifactError`` here rather than return, so a no-op write is what this asserts and
    not the accident of a payload that happened to validate.
    """
    handoff.record(work_repo, "proj-u", "change-shape", {"not": "a change shape"})

    assert _artifact_bodies(work_repo, "proj-u") == []
    assert handoff.entry_verdict(work_repo, "proj-u", "change-shape").admitted


def _package_modules() -> dict[str, ast.Module]:
    """Every module of the shipped package, parsed rather than imported.

    Parsed because ``handoff`` sits below every producer in the layering contract (§34):
    importing ``loop`` here to see what it calls would invert the tier the declaration
    exists to keep honest, while reading its source carries no dependency at all.
    """
    package = Path(handoff.__file__).parent
    return {path.stem: ast.parse(path.read_text(encoding="utf-8")) for path in package.glob("*.py")}


def _called(modules: dict[str, ast.Module]) -> set[str]:
    """Every name called anywhere in the package, bare or through an attribute."""
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
    """The top-level function *name* in *tree*, or None when it defines no such function."""
    return next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def test_a_declared_producer_that_stopped_recording_its_kind_is_a_defect_not_unwired() -> None:
    """Three claims per declaration, because one that can over-claim binds nothing.

    The symbol resolves to a function, that function names the kind's own constant, and
    something calls it. Each has to fail *here* rather than demote the kind inside
    :func:`handoff.wired`, which would hand the fail-open answer back to the absence it was
    taken away from: a renamed producer would then read as a kind nobody had ever wired.

    The two assertions before the loop are the positive control — a probe that parsed
    nothing, or collected no call at all, would report every declaration sound.
    """
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


# --- a corrupted artifact, which is the population that binds ----------------


def test_a_hand_corrupted_plan_is_refused_naming_the_failing_field(work_repo: Path) -> None:
    """The acceptance criterion: an artifact edited out of shape names the field it broke."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["integrity"] = "L9"
    artifact_record.write(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted
    assert "integrity" in verdict.reason and "L9" in verdict.reason


def test_a_task_naming_no_demonstration_is_refused_though_a_recorded_bead_is_not(
    work_repo: Path,
) -> None:
    """D18 binds on the artifact and not on ``PLAN_FIELDS``, and the two populations differ.

    A bead recorded before the field existed is admitted by ``plan_entry`` because its
    silence is ambiguous. This artifact has no such population — its only producer is a
    plan ``plan_gate.require_plan`` passed — so here the same silence is a defect.
    """
    payload = handoff.plan_payload(decomposition())
    del payload["tasks"][0]["demonstration"]
    artifact_record.write(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted
    assert plan_gate.DEMONSTRATION_FIELD in verdict.reason


def test_a_plan_whose_payload_is_not_json_is_refused_not_ignored(work_repo: Path) -> None:
    """A truncated marker is a corrupted artifact, never a unit that carries none."""
    record_marker(work_repo, "proj-feat", f"{artifact_record.MARKER} kind=implementation-plan {{n")
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted and "is not of type 'object'" in verdict.reason


def test_every_violation_is_reported_at_once(work_repo: Path) -> None:
    """One advance per fixed field is the round-trip cost this gate exists to avoid."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["acceptance"] = []
    payload["tasks"][1]["budget_tokens"] = 0
    artifact_record.write(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert len(verdict.violations) == 2


# --- the change-summary -----------------------------------------------------


def test_a_derived_change_summary_records_and_reads_back_admitted(work_repo: Path) -> None:
    """BUILD's handoff is composed from facts the engine holds, and VERIFY accepts it."""
    handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, summary())
    assert handoff.entry_verdict(work_repo, "proj-i", handoff.CHANGE_SUMMARY).admitted


def test_a_build_that_changed_nothing_has_no_summary_to_hand_on(work_repo: Path) -> None:
    """An empty changed set is refused at composition: VERIFY would have nothing to check."""
    payload = handoff.summary_payload(
        "proj-i", "why", ("abc1234", ()), handoff.SelfCheck("merged", "landed", passed=True)
    )
    with pytest.raises(handoff.ArtifactError) as caught:
        handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    assert "changed" in caught.value.verdict.reason


def test_a_hand_corrupted_change_summary_is_refused_naming_the_failing_field(
    work_repo: Path,
) -> None:
    """The BUILD->VERIFY half of the same control pair."""
    payload = summary()
    payload["self_check"]["passed"] = "yes"
    artifact_record.write(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    verdict = handoff.entry_verdict(work_repo, "proj-i", handoff.CHANGE_SUMMARY)
    assert not verdict.admitted and "passed" in verdict.reason


def test_the_changed_paths_are_carried_as_a_count_and_a_digest_not_as_the_list() -> None:
    """`basicly-gvlpxm`: the one field that grew with the diff is gone from the payload."""
    payload = summary()
    assert "changed" not in payload
    assert payload["changed_count"] == 1
    assert re.fullmatch("[0-9a-f]{64}", payload["changed_digest"])


def _lane_commit(repo: Path, paths: tuple[str, ...]) -> str:
    """Commit *paths* on a lane branch off ``main`` in *repo*; return the branch head."""
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
    """The other half of the cut: what was dropped is recoverable, and checkably so.

    A real repo, because the claim is that two *different* git reads answer with one path
    set — the producer's ``diff --name-only <base>...<branch>`` and a reader's ``show
    --name-only <commit>`` — which a fake handing both one canned answer cannot show.
    """
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
    """The population argument: 38 summaries are already stored carrying the list.

    An append-only log cannot re-derive one, so refusing the old form refuses those units.
    """
    payload = summary()
    payload["changed"] = ["src/basicly/handoff.py"]
    del payload["changed_count"], payload["changed_digest"]
    artifact_record.write(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    assert handoff.entry_verdict(work_repo, "proj-i", handoff.CHANGE_SUMMARY).admitted


def test_a_four_hundred_file_lane_is_stored_in_under_a_kilobyte(work_repo: Path) -> None:
    """The bound the cut buys: constant in the diff, so a big correct change stays storable.

    The largest summary stored under the old form was 18555 bytes, 4096 of them paths. The
    body is measured as the store writes it (``kit/tracker/events.py``: sorted keys, no
    separator whitespace, unescaped non-ascii), not as a second opinion about that.
    """
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


# --- a body the transport cut, which no fake can produce ---------------------


def _stored_on_a_real_ledger(repo: Path, record: str, body: str) -> None:
    """Seed *record* and put *body* on it as one comment, through the cap that may cut it."""
    flipped_tracker.seed(repo, record, title="a recorded feature")
    record_marker(repo, record, body)


def _stored_text(repo: Path, record: str) -> str:
    """The body as the store actually kept it, which is what the cap measured."""
    return str(tracker.read_comments(repo, record)[-1][tracker.COMMENT_TEXT_KEY])


def test_a_plan_the_cap_cut_is_refused_naming_the_truncation_and_both_byte_counts(
    work_repo: Path,
) -> None:
    """The defect: 23 stored pairs read as malformed when the transport destroyed them.

    The reason has to carry both sizes because they are what tells a cut body apart from
    a corrupted one, and the remedy because the log is append-only — the bodies of these
    23 are gone, so the only move left is to record the artifact again.
    """
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
    """The control, one variable from the pair above: same store, same route, small body.

    A real schema failure must not be swallowed into the truncation message — that would
    trade one misleading reason for another, on the population the gate exists for.
    """
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
    """The positive control the pair needs: an uncut artifact reaches the same yes."""
    body = legacy_marker(handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition()))
    _stored_on_a_real_ledger(work_repo, "proj-sound", body)

    assert handoff.entry_verdict(work_repo, "proj-sound", handoff.IMPLEMENTATION_PLAN).admitted

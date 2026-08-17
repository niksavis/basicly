"""The handoff artifact contract itself: what validates, what is refused, what is inert.

Every gate assertion here is a control pair — the same unit with a sound artifact and
with a corrupted one — because a predicate that only ever sees good input cannot be
shown to bind. The corruption is always applied to the *recorded marker*, not to the
payload on its way in: the producer validates before it writes, so a defect the
consumer can actually meet has to arrive the way a hand-edit or an older producer
would leave it.

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

from pathlib import Path

import pytest

from basicly import artifact_record, handoff, plan_gate, tracker
from basicly.decompose import CreatedChild, DecomposeResult
from tests import plan_fixtures


class _FakeBr:
    """Stand-in for the marker store, taught only the comment surface this seam uses.

    Comments are kept per issue so a write is readable back through the same fake,
    which is what makes the round trip — record, then admit — a real round trip rather
    than two assertions about one dictionary.

    Installed at the marker seam — ``add_comment``/``read_comments`` — rather than
    below it: those two are what :mod:`basicly.artifact_record` calls, and the spawn
    that used to sit under them is deleted since
    ``[tracker] mode`` became ``owned``.
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


def test_a_sound_plan_records_and_reads_back_admitted(work_repo: Path, fake_br: _FakeBr) -> None:
    """The round trip: DECOMPOSE writes the artifact and BUILD's entry accepts it."""
    handoff.record(
        work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    assert fake_br.comments["proj-feat"][0].startswith(
        f"{artifact_record.MARKER} kind=implementation-plan "
    )
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert verdict.admitted and verdict.reason == ""


def test_a_plan_missing_a_gated_field_is_refused_before_it_is_written(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """A payload the consumer would refuse never becomes an artifact in the first place."""
    payload = handoff.plan_payload(decomposition())
    del payload["tasks"][0]["budget_tokens"]
    with pytest.raises(handoff.ArtifactError) as caught:
        handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    assert "budget_tokens" in caught.value.verdict.reason
    assert fake_br.comments == {}


def test_recording_the_same_artifact_twice_writes_one_marker(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """A state re-entered on every advance must not bury its artifact under copies."""
    payload = handoff.plan_payload(decomposition())
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    handoff.record(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN, payload)
    assert len(fake_br.comments["proj-feat"]) == 1


@pytest.mark.usefixtures("fake_br")
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


@pytest.mark.usefixtures("fake_br")
def test_a_unit_with_no_artifact_is_admitted(work_repo: Path) -> None:
    """Absence predates the rule: a feature decomposed before this existed still builds."""
    assert handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted


def test_an_unrelated_marker_family_is_not_an_artifact(work_repo: Path, fake_br: _FakeBr) -> None:
    """Only this family's markers are read: a policy or run marker is not a handoff."""
    fake_br.comments["proj-feat"] = ["[harness-policy] checkpoint=decompose approved"]
    assert artifact_record.read(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN) is None


@pytest.mark.usefixtures("fake_br")
def test_the_other_kind_of_artifact_is_not_read_as_this_one(work_repo: Path) -> None:
    """The kind is a field, so one family carries both without either answering for the other."""
    handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, summary())
    assert artifact_record.read(work_repo, "proj-i", handoff.IMPLEMENTATION_PLAN) is None
    assert artifact_record.read(work_repo, "proj-i", handoff.CHANGE_SUMMARY) is not None


def test_a_repo_without_the_schema_runs_neither_end(tmp_path: Path, fake_br: _FakeBr) -> None:
    """The contract is a catalog source: uninstalled, it writes nothing and refuses nothing."""
    assert not handoff.adopted(tmp_path, handoff.IMPLEMENTATION_PLAN)
    handoff.record(
        tmp_path, "proj-feat", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    assert fake_br.comments == {}
    assert handoff.entry_verdict(tmp_path, "proj-feat", handoff.IMPLEMENTATION_PLAN).admitted


# --- a corrupted artifact, which is the population that binds ----------------


def test_a_hand_corrupted_plan_is_refused_naming_the_failing_field(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """The acceptance criterion: an artifact edited out of shape names the field it broke."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["integrity"] = "L9"
    fake_br.comments["proj-feat"] = [
        artifact_record.marker_body(handoff.IMPLEMENTATION_PLAN, payload)
    ]
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted
    assert "integrity" in verdict.reason and "L9" in verdict.reason


def test_a_task_naming_no_demonstration_is_refused_though_a_recorded_bead_is_not(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """D18 binds on the artifact and not on ``PLAN_FIELDS``, and the two populations differ.

    A bead recorded before the field existed is admitted by ``plan_entry`` because its
    silence is ambiguous. This artifact has no such population — its only producer is a
    plan ``plan_gate.require_plan`` passed — so here the same silence is a defect.
    """
    payload = handoff.plan_payload(decomposition())
    del payload["tasks"][0]["demonstration"]
    fake_br.comments["proj-feat"] = [
        artifact_record.marker_body(handoff.IMPLEMENTATION_PLAN, payload)
    ]
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted
    assert plan_gate.DEMONSTRATION_FIELD in verdict.reason


def test_a_plan_whose_payload_is_not_json_is_refused_not_ignored(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """A truncated marker is a corrupted artifact, never a unit that carries none."""
    fake_br.comments["proj-feat"] = [
        f"{artifact_record.MARKER} kind=implementation-plan {{not json"
    ]
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted and "is not of type 'object'" in verdict.reason


def test_every_violation_is_reported_at_once(work_repo: Path, fake_br: _FakeBr) -> None:
    """One advance per fixed field is the round-trip cost this gate exists to avoid."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["acceptance"] = []
    payload["tasks"][1]["budget_tokens"] = 0
    fake_br.comments["proj-feat"] = [
        artifact_record.marker_body(handoff.IMPLEMENTATION_PLAN, payload)
    ]
    verdict = handoff.entry_verdict(work_repo, "proj-feat", handoff.IMPLEMENTATION_PLAN)
    assert len(verdict.violations) == 2


# --- the change-summary -----------------------------------------------------


@pytest.mark.usefixtures("fake_br")
def test_a_derived_change_summary_records_and_reads_back_admitted(work_repo: Path) -> None:
    """BUILD's handoff is composed from facts the engine holds, and VERIFY accepts it."""
    handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, summary())
    assert handoff.entry_verdict(work_repo, "proj-i", handoff.CHANGE_SUMMARY).admitted


@pytest.mark.usefixtures("fake_br")
def test_a_build_that_changed_nothing_has_no_summary_to_hand_on(work_repo: Path) -> None:
    """An empty changed set is refused at composition: VERIFY would have nothing to check."""
    payload = handoff.summary_payload(
        "proj-i", "why", ("abc1234", ()), handoff.SelfCheck("merged", "landed", passed=True)
    )
    with pytest.raises(handoff.ArtifactError) as caught:
        handoff.record(work_repo, "proj-i", handoff.CHANGE_SUMMARY, payload)
    assert "changed" in caught.value.verdict.reason


def test_a_hand_corrupted_change_summary_is_refused_naming_the_failing_field(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """The BUILD->VERIFY half of the same control pair."""
    payload = summary()
    payload["self_check"]["passed"] = "yes"
    fake_br.comments["proj-i"] = [artifact_record.marker_body(handoff.CHANGE_SUMMARY, payload)]
    verdict = handoff.entry_verdict(work_repo, "proj-i", handoff.CHANGE_SUMMARY)
    assert not verdict.admitted and "passed" in verdict.reason

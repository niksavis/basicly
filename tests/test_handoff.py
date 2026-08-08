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

The loop states that produce and consume these artifacts are ``test_handoff_states.py``,
on the same boundary the module itself is drawn along: nothing here advances a phase,
and nothing there re-asserts the schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import artifact_record, br, handoff
from basicly.decompose import ChildSpec, CreatedChild, DecomposeResult


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    """Stand-in for the br CLI, taught only the comment surface the marker seam uses.

    Comments are kept per issue so a write is readable back through the same fake,
    which is what makes the round trip — record, then admit — a real round trip rather
    than two assertions about one dictionary.
    """

    def __init__(self) -> None:
        self.comments: dict[str, list[str]] = {}

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["comments", "add"]:
            self.comments.setdefault(args[2], []).append(args[3])
            return _Proc("")
        if args[:2] == ["comments", "list"]:
            texts = self.comments.get(args[2], [])
            return _Proc(json.dumps([{"text": text} for text in texts]))
        raise AssertionError(f"unexpected br call: {args}")


@pytest.fixture
def fake_br(monkeypatch: pytest.MonkeyPatch) -> _FakeBr:
    """Route the marker seam's own funnel — ``br.run_br`` — at a stateful fake."""
    fake = _FakeBr()
    monkeypatch.setattr(br, "run_br", fake)
    return fake


def spec(title: str, *scope: str) -> ChildSpec:
    """A child carrying every field the plan gate requires, so a plan is about the plan."""
    return ChildSpec(
        title=title,
        acceptance=("the thing is demonstrable",),
        scope=scope or (f"src/{title}.py",),
        depends_on=(),
        budget_tokens=40_000,
        integrity="L2",
    )


def decomposition() -> DecomposeResult:
    """A two-child decomposition in the shape ``decompose`` returns one."""
    first = CreatedChild("feat.1", spec("a"), 0, ())
    second = CreatedChild("feat.2", spec("b"), 1, ("feat.1",))
    return DecomposeResult("feat", (first, second), (("feat.1",), ("feat.2",)))


def summary() -> dict:
    """A change-summary as a finished landing derives one."""
    return handoff.summary_payload(
        "i",
        "carry the plan into build",
        ("abc1234", ("src/basicly/handoff.py",)),
        handoff.SelfCheck("merged", "landed", passed=True),
    )


# --- the implementation-plan payload ----------------------------------------


def test_plan_payload_carries_every_gated_field_and_the_graph(work_repo: Path) -> None:
    """The artifact says what the children were created under, plus their grouping."""
    payload = handoff.plan_payload(decomposition())
    assert payload["feature"] == "feat"
    assert payload["groups"] == [["feat.1"], ["feat.2"]]
    first = payload["tasks"][0]
    assert first["issue_id"] == "feat.1"
    assert first["acceptance"] == ["the thing is demonstrable"]
    assert first["scope"] == ["src/a.py"]
    assert first["budget_tokens"] == 40_000
    assert first["integrity"] == "L2"
    assert payload["tasks"][1]["depends_on"] == ["feat.1"]
    assert handoff.adopted(work_repo, handoff.IMPLEMENTATION_PLAN)


def test_a_sound_plan_records_and_reads_back_admitted(work_repo: Path, fake_br: _FakeBr) -> None:
    """The round trip: DECOMPOSE writes the artifact and BUILD's entry accepts it."""
    handoff.record(
        work_repo, "feat", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    assert fake_br.comments["feat"][0].startswith(
        f"{artifact_record.MARKER} kind=implementation-plan "
    )
    verdict = handoff.entry_verdict(work_repo, "feat", handoff.IMPLEMENTATION_PLAN)
    assert verdict.admitted and verdict.reason == ""


def test_a_plan_missing_a_gated_field_is_refused_before_it_is_written(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """A payload the consumer would refuse never becomes an artifact in the first place."""
    payload = handoff.plan_payload(decomposition())
    del payload["tasks"][0]["budget_tokens"]
    with pytest.raises(handoff.ArtifactError) as caught:
        handoff.record(work_repo, "feat", handoff.IMPLEMENTATION_PLAN, payload)
    assert "budget_tokens" in caught.value.verdict.reason
    assert fake_br.comments == {}


def test_recording_the_same_artifact_twice_writes_one_marker(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """A state re-entered on every advance must not bury its artifact under copies."""
    payload = handoff.plan_payload(decomposition())
    handoff.record(work_repo, "feat", handoff.IMPLEMENTATION_PLAN, payload)
    handoff.record(work_repo, "feat", handoff.IMPLEMENTATION_PLAN, payload)
    assert len(fake_br.comments["feat"]) == 1


@pytest.mark.usefixtures("fake_br")
def test_the_last_recorded_plan_is_the_one_read_back(work_repo: Path) -> None:
    """Re-decomposed under a changed plan, BUILD is held to the plan that made the children."""
    handoff.record(
        work_repo, "feat", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    superseding = handoff.plan_payload(decomposition())
    superseding["tasks"] = superseding["tasks"][:1]
    superseding["groups"] = [["feat.1"]]
    handoff.record(work_repo, "feat", handoff.IMPLEMENTATION_PLAN, superseding)
    recorded = artifact_record.read(work_repo, "feat", handoff.IMPLEMENTATION_PLAN)
    assert isinstance(recorded, dict) and len(recorded["tasks"]) == 1


# --- the ratchet, and the population it discriminates ------------------------


@pytest.mark.usefixtures("fake_br")
def test_a_unit_with_no_artifact_is_admitted(work_repo: Path) -> None:
    """Absence predates the rule: a feature decomposed before this existed still builds."""
    assert handoff.entry_verdict(work_repo, "feat", handoff.IMPLEMENTATION_PLAN).admitted


def test_an_unrelated_marker_family_is_not_an_artifact(work_repo: Path, fake_br: _FakeBr) -> None:
    """Only this family's markers are read: a policy or run marker is not a handoff."""
    fake_br.comments["feat"] = ["[harness-policy] checkpoint=decompose approved"]
    assert artifact_record.read(work_repo, "feat", handoff.IMPLEMENTATION_PLAN) is None


@pytest.mark.usefixtures("fake_br")
def test_the_other_kind_of_artifact_is_not_read_as_this_one(work_repo: Path) -> None:
    """The kind is a field, so one family carries both without either answering for the other."""
    handoff.record(work_repo, "i", handoff.CHANGE_SUMMARY, summary())
    assert artifact_record.read(work_repo, "i", handoff.IMPLEMENTATION_PLAN) is None
    assert artifact_record.read(work_repo, "i", handoff.CHANGE_SUMMARY) is not None


def test_a_repo_without_the_schema_runs_neither_end(tmp_path: Path, fake_br: _FakeBr) -> None:
    """The contract is a catalog source: uninstalled, it writes nothing and refuses nothing."""
    assert not handoff.adopted(tmp_path, handoff.IMPLEMENTATION_PLAN)
    handoff.record(
        tmp_path, "feat", handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(decomposition())
    )
    assert fake_br.comments == {}
    assert handoff.entry_verdict(tmp_path, "feat", handoff.IMPLEMENTATION_PLAN).admitted


# --- a corrupted artifact, which is the population that binds ----------------


def test_a_hand_corrupted_plan_is_refused_naming_the_failing_field(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """The acceptance criterion: an artifact edited out of shape names the field it broke."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["integrity"] = "L9"
    fake_br.comments["feat"] = [artifact_record.marker_body(handoff.IMPLEMENTATION_PLAN, payload)]
    verdict = handoff.entry_verdict(work_repo, "feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted
    assert "integrity" in verdict.reason and "L9" in verdict.reason


def test_a_plan_whose_payload_is_not_json_is_refused_not_ignored(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """A truncated marker is a corrupted artifact, never a unit that carries none."""
    fake_br.comments["feat"] = [f"{artifact_record.MARKER} kind=implementation-plan {{not json"]
    verdict = handoff.entry_verdict(work_repo, "feat", handoff.IMPLEMENTATION_PLAN)
    assert not verdict.admitted and "is not of type 'object'" in verdict.reason


def test_every_violation_is_reported_at_once(work_repo: Path, fake_br: _FakeBr) -> None:
    """One advance per fixed field is the round-trip cost this gate exists to avoid."""
    payload = handoff.plan_payload(decomposition())
    payload["tasks"][0]["acceptance"] = []
    payload["tasks"][1]["budget_tokens"] = 0
    fake_br.comments["feat"] = [artifact_record.marker_body(handoff.IMPLEMENTATION_PLAN, payload)]
    verdict = handoff.entry_verdict(work_repo, "feat", handoff.IMPLEMENTATION_PLAN)
    assert len(verdict.violations) == 2


# --- the change-summary -----------------------------------------------------


@pytest.mark.usefixtures("fake_br")
def test_a_derived_change_summary_records_and_reads_back_admitted(work_repo: Path) -> None:
    """BUILD's handoff is composed from facts the engine holds, and VERIFY accepts it."""
    handoff.record(work_repo, "i", handoff.CHANGE_SUMMARY, summary())
    assert handoff.entry_verdict(work_repo, "i", handoff.CHANGE_SUMMARY).admitted


@pytest.mark.usefixtures("fake_br")
def test_a_build_that_changed_nothing_has_no_summary_to_hand_on(work_repo: Path) -> None:
    """An empty changed set is refused at composition: VERIFY would have nothing to check."""
    payload = handoff.summary_payload(
        "i", "why", ("abc1234", ()), handoff.SelfCheck("merged", "landed", passed=True)
    )
    with pytest.raises(handoff.ArtifactError) as caught:
        handoff.record(work_repo, "i", handoff.CHANGE_SUMMARY, payload)
    assert "changed" in caught.value.verdict.reason


def test_a_hand_corrupted_change_summary_is_refused_naming_the_failing_field(
    work_repo: Path, fake_br: _FakeBr
) -> None:
    """The BUILD->VERIFY half of the same control pair."""
    payload = summary()
    payload["self_check"]["passed"] = "yes"
    fake_br.comments["i"] = [artifact_record.marker_body(handoff.CHANGE_SUMMARY, payload)]
    verdict = handoff.entry_verdict(work_repo, "i", handoff.CHANGE_SUMMARY)
    assert not verdict.admitted and "passed" in verdict.reason

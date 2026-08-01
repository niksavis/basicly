"""Tests for the gate & checkpoint policy engine (onb.3)."""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from basicly import policy, rubrics, run_record, verify
from basicly.config import (
    ENGINE_GATE_PROVIDERS,
    LOOP_PHASES,
    RUBRIC_GATE_PROVIDER,
    VERIFY_GATE_PROVIDER,
    PolicyConfig,
    SizingConfig,
)


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


# The tracker stamp a comment a test seeded directly (never written through the
# fake's `comments add`) reads as.
_EPOCH = "2026-01-01T00:00:00Z"


class _FakeBr:
    """Stateful stand-in for the br CLI, routed by subcommand.

    Holds a mutable comment list so record/approve writes are visible to the
    subsequent list reads, exactly as the real tracker behaves.
    """

    def __init__(  # noqa: PLR0913 — one knob per br surface the fake serves
        self,
        *,
        lint_missing: list[str] | None = None,
        gates: list[dict] | None = None,
        acceptance_criteria: str | None = None,
        description: str | None = None,
        dependents: list[dict] | None = None,
        status: str = "open",
        records: dict[str, dict] | None = None,
        gates_by_issue: dict[str, list[dict]] | None = None,
    ):
        self.lint_missing = lint_missing or []
        self.gates = gates or []
        self.acceptance_criteria = acceptance_criteria
        self.description = description
        self.dependents = dependents or []
        self.status = status
        self.records = records or {}
        # Per-issue gates, for the checks that must tell one bead's gates from
        # another's (lights-out scoping, basicly-kjc5.39); falls back to `gates`.
        self.gates_by_issue = gates_by_issue or {}
        self.comments: list[str] = []
        # br stamps every comment with a created_at, and the wait meter
        # (basicly-kjc5.51) reads it. Stamps are keyed by position so a test that
        # seeds self.comments directly still gets the default, and `now` is the
        # tracker's clock a test advances between writes.
        self.now = _EPOCH
        self.stamps: dict[int, str] = {}

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["lint"]:
            return _Proc(json.dumps({"results": [{"missing": self.lint_missing}]}))
        if args[:1] == ["show"]:
            if args[1] in self.records:
                return _Proc(json.dumps([self.records[args[1]]]))
            record = {
                "acceptance_criteria": self.acceptance_criteria,
                "description": self.description,
                "dependents": self.dependents,
                "status": self.status,
            }
            return _Proc(json.dumps([record]))
        if args[:2] == ["gate", "list"]:
            results = self.gates_by_issue.get(args[2], self.gates)
            return _Proc(json.dumps({"results": results}))
        if args[:2] == ["comments", "list"]:
            listing = [
                {"text": text, "created_at": self.stamps.get(i, _EPOCH)}
                for i, text in enumerate(self.comments)
            ]
            return _Proc(json.dumps(listing))
        if args[:2] == ["comments", "add"]:
            # br comments add <id> <text> — the marker text is the last arg.
            self.comments.append(args[-1])
            self.stamps[len(self.comments) - 1] = self.now
            return _Proc("")
        raise AssertionError(f"unexpected br call: {args}")


CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(policy, "_run_br", fake)


def test_definition_of_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """DoR is ready when lint reports nothing missing and criteria are recorded."""
    _install(monkeypatch, _FakeBr(lint_missing=[], acceptance_criteria="given x then y"))
    assert policy.definition_of_ready(tmp_path, "i").ready is True

    _install(monkeypatch, _FakeBr(lint_missing=["## Acceptance Criteria"]))
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.ready is False
    assert result.missing == ("## Acceptance Criteria",)


def test_dor_requires_acceptance_criteria_even_when_lint_never_asks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A silent lint is not evidence the criteria exist (basicly-kjc5.36).

    ``br lint`` derives required sections from the per-type template, and a chore
    is never asked for acceptance criteria — so a chore carrying none used to pass
    DoR vacuously and then meet a required validate gate with nothing to judge.
    """
    _install(monkeypatch, _FakeBr(lint_missing=[], acceptance_criteria=None))
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.ready is False
    assert result.missing == ("## Acceptance Criteria",)


def test_dor_accepts_criteria_from_the_description_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Either carrier satisfies the requirement: the body section, or the field."""
    _install(
        monkeypatch,
        _FakeBr(
            lint_missing=[],
            acceptance_criteria=None,
            description="## Acceptance Criteria\n\n- given x then y\n",
        ),
    )
    assert policy.definition_of_ready(tmp_path, "i").ready is True


def test_dor_added_requirement_does_not_duplicate_the_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When lint already reports AC missing, the rule must not list it twice."""
    _install(
        monkeypatch, _FakeBr(lint_missing=["## Acceptance Criteria"], acceptance_criteria=None)
    )
    assert policy.definition_of_ready(tmp_path, "i").missing == ("## Acceptance Criteria",)


def test_dor_keeps_other_missing_sections_when_adding_the_requirement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Adding the AC requirement never drops a section lint did report."""
    _install(monkeypatch, _FakeBr(lint_missing=["## Steps to Reproduce"], acceptance_criteria=None))
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.missing == ("## Steps to Reproduce", "## Acceptance Criteria")


def test_dor_structured_acceptance_field_satisfies_the_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-empty structured acceptance_criteria field clears the AC section (basicly-58iu)."""
    _install(
        monkeypatch,
        _FakeBr(lint_missing=["## Acceptance Criteria"], acceptance_criteria="the field is set"),
    )
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.ready is True
    assert result.missing == ()


def test_dor_structured_field_does_not_mask_other_missing_sections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The field clears only AC; other template sections still block (basicly-58iu)."""
    _install(
        monkeypatch,
        _FakeBr(
            lint_missing=["## Steps to Reproduce", "## Acceptance Criteria"],
            acceptance_criteria="fixed when x",
        ),
    )
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.ready is False
    assert result.missing == ("## Steps to Reproduce",)


def test_dor_empty_or_absent_acceptance_field_still_requires_the_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A blank or absent field does not satisfy the AC section (basicly-58iu)."""
    _install(
        monkeypatch, _FakeBr(lint_missing=["## Acceptance Criteria"], acceptance_criteria="  ")
    )
    assert policy.definition_of_ready(tmp_path, "i").ready is False
    _install(
        monkeypatch, _FakeBr(lint_missing=["## Acceptance Criteria"], acceptance_criteria=None)
    )
    assert policy.definition_of_ready(tmp_path, "i").ready is False


def test_required_sections_derives_the_set_from_the_work_type() -> None:
    """Per-type template sections, plus the AC every bead owes (basicly-kjc5.44)."""
    assert policy.required_sections("bug") == ("## Steps to Reproduce", "## Acceptance Criteria")
    assert policy.required_sections("epic") == ("## Success Criteria", "## Acceptance Criteria")
    for work_type in ("task", "chore", "feature"):
        assert policy.required_sections(work_type) == ("## Acceptance Criteria",)


def test_required_sections_of_an_unknown_type_still_owes_acceptance_criteria() -> None:
    """An unmapped type must not scaffold an empty body — the AC rule is type-blind."""
    assert policy.required_sections("docs") == ("## Acceptance Criteria",)


def test_compose_body_emits_every_required_section_with_a_placeholder() -> None:
    """The scaffold names the structure; the TODO marks the judgment left to do."""
    body = policy.compose_body("bug")
    assert body.startswith("## Steps to Reproduce\n\n")
    assert "## Acceptance Criteria\n\n" in body
    assert body.count("TODO") == 2


def test_compose_body_uses_supplied_content_instead_of_the_placeholder() -> None:
    """A caller with real content gets it under the heading, and no stray TODO."""
    body = policy.compose_body("task", {"## Acceptance Criteria": "- Given x when y then z"})
    assert body == "## Acceptance Criteria\n\n- Given x when y then z\n"


def test_compose_body_appends_a_non_required_section_rather_than_dropping_it() -> None:
    """``## Scope`` is not a DoR section, but a caller that supplies it must keep it.

    Dropping a heading the caller declared would silently lose a child's scope,
    which the calibration reader then sees as an unreadable bead.
    """
    body = policy.compose_body("bug", {"## Scope": "- `src/basicly/cli.py`"})
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == ["## Steps to Reproduce", "## Acceptance Criteria", "## Scope"]
    assert "- `src/basicly/cli.py`" in body


def test_compose_body_never_duplicates_a_required_heading() -> None:
    """Supplying content for an already-required section must not repeat the heading."""
    body = policy.compose_body("task", {"## Acceptance Criteria": "- given x then y"})
    assert body.count("## Acceptance Criteria") == 1


def test_compose_body_puts_a_preamble_above_the_first_heading() -> None:
    """An engine-composed body may carry context; it must not displace the structure."""
    body = policy.compose_body("task", preamble="Continues basicly-x: it overran.")
    assert body.startswith("Continues basicly-x: it overran.\n\n## Acceptance Criteria\n\n")


def test_gate_status_advances_when_required_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A passing required gate advances; an advisory gate never blocks.

    The advisory result keeps a non-engine provider deliberately: an advisory gate
    accepts any provider, and only the required set is filtered (basicly-jr0l.51).
    """
    _install(
        monkeypatch,
        _FakeBr(
            gates=[
                {"gate": "verify", "provider": VERIFY_GATE_PROVIDER, "passed": True},
                {"gate": "review", "provider": "ai", "passed": False},
            ]
        ),
    )
    status = policy.gate_status(tmp_path, "i", CONFIG)
    assert status.can_advance is True
    assert status.required_passed == ("verify",)
    assert [(v.gate, v.passed) for v in status.advisory] == [("review", False)]
    assert status.disregarded == ()


def test_gate_status_blocks_on_failed_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed required gate blocks advancement."""
    _install(
        monkeypatch,
        _FakeBr(gates=[{"gate": "verify", "provider": VERIFY_GATE_PROVIDER, "passed": False}]),
    )
    status = policy.gate_status(tmp_path, "i", CONFIG)
    assert status.can_advance is False
    assert status.required_failed == ("verify",)


def test_gate_status_blocks_on_missing_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A required gate with no recorded result blocks advancement."""
    _install(monkeypatch, _FakeBr(gates=[]))
    status = policy.gate_status(tmp_path, "i", CONFIG)
    assert status.can_advance is False
    assert status.required_missing == ("verify",)


def test_a_required_gate_ignores_a_pass_from_a_foreign_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The forgery this closes: one ``br gate report`` from inside a dispatch.

    A dispatched lane agent shares the real tracker through the worktree beads
    redirect, so before basicly-jr0l.51 a single report satisfied a required gate
    and no model-authority constraint stood in its way. The gate must read
    unsatisfied, and the disregarded result must be surfaced rather than dropped:
    when it is the only result recorded, a bare "missing" contradicts what `br gate
    list` plainly shows, leaving the operator nothing to act on.
    """
    _install(
        monkeypatch,
        _FakeBr(gates=[{"gate": "verify", "provider": "lane-agent", "passed": True}]),
    )
    status = policy.gate_status(tmp_path, "i", CONFIG)
    assert status.can_advance is False
    assert status.required_missing == ("verify",)
    assert status.required_passed == ()
    assert [(v.gate, v.provider) for v in status.disregarded] == [("verify", "lane-agent")]
    # A required gate's foreign result is not quietly reclassified as advisory —
    # that would let it read as an accepted verdict somewhere.
    assert status.advisory == ()


def test_a_required_gate_counts_the_engine_rubric_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Promoting the rubric gate to required must stay satisfiable.

    ``rubrics.RUBRIC_GATE`` is documented as promotable into ``[policy]
    required_gates`` by a consumer, and its deterministic pre-flight half is
    recorded by the engine under its own provider. Filtering to the verify provider
    alone would make that documented promotion permanently unsatisfiable.
    """
    config = PolicyConfig(required_gates=("verify", "rubric"), max_rework=2)
    _install(
        monkeypatch,
        _FakeBr(
            gates=[
                {"gate": "verify", "provider": VERIFY_GATE_PROVIDER, "passed": True},
                {"gate": "rubric", "provider": RUBRIC_GATE_PROVIDER, "passed": True},
            ]
        ),
    )
    status = policy.gate_status(tmp_path, "i", config)
    assert status.can_advance is True
    assert status.required_passed == ("verify", "rubric")
    assert status.disregarded == ()


def test_a_foreign_pass_cannot_shadow_the_engines_own_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The engine's verdict is selected independently of the rest of the rows.

    br keeps one result per (gate, provider) — verified against the real tracker,
    which returns both rows in no guaranteed order — so a gate genuinely carries a
    foreign row *alongside* the engine's. The previous reader collapsed every row
    for a gate and took the last, so a forged pass recorded after a real failure
    became the authoritative verdict. This is the ordering that must not decide it.
    """
    _install(
        monkeypatch,
        _FakeBr(
            gates=[
                {"gate": "verify", "provider": VERIFY_GATE_PROVIDER, "passed": False},
                {"gate": "verify", "provider": "lane-agent", "passed": True},
            ]
        ),
    )
    status = policy.gate_status(tmp_path, "i", CONFIG)
    assert status.can_advance is False
    assert status.required_failed == ("verify",)


def test_the_recogniser_and_the_recorders_share_one_provider_string() -> None:
    """A rename of either recorder's provider must not desynchronise the filter.

    ``gate_status`` recognises engine results by string. If ``verify`` or
    ``rubrics`` grew its own literal again, a rename there would silently stop
    every required gate from ever counting — the loop would block forever with no
    failing test to say why.
    """
    assert verify.GATE_PROVIDER in ENGINE_GATE_PROVIDERS
    assert rubrics.GATE_PROVIDER in ENGINE_GATE_PROVIDERS


def test_a_disregarded_result_is_explained_in_a_grant_violation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A blocked ship must say *why* the gate did not count, not just that it didn't."""
    _install(
        monkeypatch,
        _FakeBr(gates=[{"gate": "verify", "provider": "lane-agent", "passed": True}]),
    )
    violations = policy.lights_out_violations(tmp_path, "basicly-x", CONFIG, ids=())
    assert len(violations) == 1
    assert "lane-agent" in violations[0]
    assert "not the engine's own" in violations[0]


def test_rework_counts_and_escalates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Rework attempts accumulate and escalate once the cap is reached."""
    _install(monkeypatch, _FakeBr())
    assert policy.rework_attempts(tmp_path, "i", "verify") == 0
    assert policy.should_escalate(tmp_path, "i", "verify", CONFIG) is False

    assert policy.record_rework(tmp_path, "i", "verify") == 1
    assert policy.should_escalate(tmp_path, "i", "verify", CONFIG) is False

    assert policy.record_rework(tmp_path, "i", "verify") == 2
    assert policy.should_escalate(tmp_path, "i", "verify", CONFIG) is True


def test_rework_counter_is_per_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Rework markers are scoped to their gate, not shared."""
    _install(monkeypatch, _FakeBr())
    policy.record_rework(tmp_path, "i", "verify")
    assert policy.rework_attempts(tmp_path, "i", "verify") == 1
    assert policy.rework_attempts(tmp_path, "i", "security") == 0


def test_rework_recorded_totals_every_gate_but_no_allowance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The package-level total the cost rollup reports (basicly-kjc5.50)."""
    _install(monkeypatch, _FakeBr())
    assert policy.rework_recorded(tmp_path, "i") == 0
    policy.record_rework(tmp_path, "i", "verify")
    policy.record_rework(tmp_path, "i", "merge")
    policy.grant_rework_allowance(tmp_path, "i", "verify")
    # Two attempts across two gates; an allowance is not one of them.
    assert policy.rework_recorded(tmp_path, "i") == 2


# --- An answered `retry` must be executable (basicly-4tjt) -------------------


def _to_cap(tmp_path: Path) -> None:
    """Spend the whole rework budget on gate 'verify'."""
    for _ in range(CONFIG.max_rework):
        policy.record_rework(tmp_path, "i", "verify")


def test_an_allowance_permits_exactly_one_further_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point: at the cap, one grant buys one attempt and then re-escalates."""
    _install(monkeypatch, _FakeBr())
    _to_cap(tmp_path)
    assert policy.should_escalate(tmp_path, "i", "verify", CONFIG) is True

    policy.grant_rework_allowance(tmp_path, "i", "verify")
    assert policy.should_escalate(tmp_path, "i", "verify", CONFIG) is False

    policy.record_rework(tmp_path, "i", "verify")
    assert policy.should_escalate(tmp_path, "i", "verify", CONFIG) is True


def test_an_allowance_does_not_reset_the_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One grant is one attempt, not a fresh full budget — the operator answered retry."""
    _install(monkeypatch, _FakeBr())
    _to_cap(tmp_path)
    policy.grant_rework_allowance(tmp_path, "i", "verify")
    assert policy.rework_charged(tmp_path, "i", "verify") == CONFIG.max_rework - 1


def test_record_rework_returns_the_charged_count_not_the_raw_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every caller compares the return against the cap, so it must net off grants."""
    _install(monkeypatch, _FakeBr())
    policy.grant_rework_allowance(tmp_path, "i", "verify")
    assert policy.record_rework(tmp_path, "i", "verify") == 0
    assert policy.rework_attempts(tmp_path, "i", "verify") == 1


def test_the_raw_history_survives_a_grant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Tracker comments cannot be deleted, so the audit trail stays literal."""
    _install(monkeypatch, _FakeBr())
    _to_cap(tmp_path)
    policy.grant_rework_allowance(tmp_path, "i", "verify")
    assert policy.rework_attempts(tmp_path, "i", "verify") == CONFIG.max_rework
    assert policy.rework_allowances(tmp_path, "i", "verify") == 1


def test_charged_attempts_never_go_negative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A grant before any attempt must not bank credit against a future failure."""
    _install(monkeypatch, _FakeBr())
    policy.grant_rework_allowance(tmp_path, "i", "verify")
    policy.grant_rework_allowance(tmp_path, "i", "verify")
    assert policy.rework_charged(tmp_path, "i", "verify") == 0
    policy.record_rework(tmp_path, "i", "verify")
    assert policy.rework_charged(tmp_path, "i", "verify") == 0


def test_an_allowance_is_scoped_to_its_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Forgiving a merge flake must not extend the verify budget."""
    _install(monkeypatch, _FakeBr())
    policy.grant_rework_allowance(tmp_path, "i", "merge")
    policy.record_rework(tmp_path, "i", "verify")
    assert policy.rework_charged(tmp_path, "i", "verify") == 1
    assert policy.rework_charged(tmp_path, "i", "merge") == 0


def test_an_allowance_marker_is_not_counted_as_an_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`rework-allowance gate=x` must not token-match `rework gate=x`."""
    _install(monkeypatch, _FakeBr())
    policy.grant_rework_allowance(tmp_path, "i", "verify")
    assert policy.rework_attempts(tmp_path, "i", "verify") == 0


def test_the_escalation_question_round_trips_its_gate() -> None:
    """The queue's only carrier for the gate is the question, so write and read must agree."""
    question = policy.rework_escalation_question("merge")
    assert policy.gate_from_rework_escalation(question) == "merge"


def test_an_unrelated_question_yields_no_gate() -> None:
    """A non-rework decision must never be mistaken for one."""
    assert (
        policy.gate_from_rework_escalation("acceptance criteria unmet: accept or rework?") is None
    )


def test_checkpoint_approval_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A checkpoint reads pending until approved, then approved (idempotent)."""
    _install(monkeypatch, _FakeBr())
    assert policy.checkpoint_approved(tmp_path, "i", "decompose") is False
    policy.approve_checkpoint(tmp_path, "i", "decompose")
    policy.approve_checkpoint(tmp_path, "i", "decompose")  # idempotent
    assert policy.checkpoint_approved(tmp_path, "i", "decompose") is True
    # A different checkpoint is unaffected.
    assert policy.checkpoint_approved(tmp_path, "i", "ship") is False


def test_approve_unknown_checkpoint_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Approving a checkpoint outside the fixed three is a loud error."""
    _install(monkeypatch, _FakeBr())
    with pytest.raises(ValueError, match="unknown checkpoint"):
        policy.approve_checkpoint(tmp_path, "i", "deploy")


def test_rework_markers_do_not_cross_count_prefix_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attempts on gate verify-full must not inflate the count for verify."""
    comments = [
        "[harness-policy] rework gate=verify",
        "[harness-policy] rework gate=verify-full",
        "[harness-policy] rework gate=verify-full",
    ]
    monkeypatch.setattr(policy, "_comment_texts", lambda _root, _issue: comments)
    assert policy.rework_attempts(tmp_path, "x-1", "verify") == 1
    assert policy.rework_attempts(tmp_path, "x-1", "verify-full") == 2


def test_checkpoint_markers_are_token_exact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A checkpoint named ship must not approve one named ship-final."""
    comments = ["[harness-policy] checkpoint=ship approved"]
    monkeypatch.setattr(policy, "_comment_texts", lambda _root, _issue: comments)
    assert policy.checkpoint_approved(tmp_path, "x-1", "ship")
    assert not policy.checkpoint_approved(tmp_path, "x-1", "ship-final")


# --- Interactive-confirmation gate (basicly-shgo) ---------------------------


def _pin_code(monkeypatch: pytest.MonkeyPatch, code: str, now: float = 1000.0) -> None:
    monkeypatch.setattr(policy, "_new_code", lambda: code)
    monkeypatch.setattr(policy, "_now", lambda: now)


def test_guarded_approve_interactive_records_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An interactive TTY approves and records the marker with no confirm code."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    result = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=True)
    assert result.status == "approved"
    assert policy.checkpoint_approved(tmp_path, "i", "ship")


def test_guarded_approve_non_interactive_challenges_without_recording(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No TTY and no code yields a challenge code and records nothing."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_code(monkeypatch, "deadbeef")
    result = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    assert result.status == "challenge" and result.code == "deadbeef"
    assert not policy.checkpoint_approved(tmp_path, "i", "ship")


def test_guarded_approve_valid_confirm_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A matching, unexpired confirm code approves; the code is single-use."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_code(monkeypatch, "abc123")
    policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    ok = policy.approve_checkpoint_guarded(
        tmp_path, "i", "ship", interactive=False, confirm="abc123"
    )
    assert ok.status == "approved"
    assert policy.checkpoint_approved(tmp_path, "i", "ship")


def test_guarded_approve_wrong_code_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-matching code is rejected and records no marker."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_code(monkeypatch, "abc123")
    policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    bad = policy.approve_checkpoint_guarded(
        tmp_path, "i", "ship", interactive=False, confirm="nope"
    )
    assert bad.status == "rejected"
    assert not policy.checkpoint_approved(tmp_path, "i", "ship")


def test_guarded_approve_expired_code_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A code past its TTL is rejected even when it matches."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    monkeypatch.setattr(policy, "_new_code", lambda: "abc123")
    monkeypatch.setattr(policy, "_now", lambda: 1000.0)
    policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    monkeypatch.setattr(policy, "_now", lambda: 1000.0 + policy.CONFIRM_TTL_SECONDS + 1)
    stale = policy.approve_checkpoint_guarded(
        tmp_path, "i", "ship", interactive=False, confirm="abc123"
    )
    assert stale.status == "rejected"
    assert not policy.checkpoint_approved(tmp_path, "i", "ship")


def test_guarded_approve_already_approved_short_circuits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An already-approved checkpoint returns approved without demanding a TTY."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    policy.approve_checkpoint(tmp_path, "i", "ship")
    result = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    assert result.status == "approved"


def test_guarded_approve_unknown_checkpoint_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The guarded path enforces the fixed checkpoint set too."""
    _install(monkeypatch, _FakeBr())
    with pytest.raises(ValueError, match="unknown checkpoint"):
        policy.approve_checkpoint_guarded(tmp_path, "i", "deploy", interactive=True)


# --- Working-set sizing governor (basicly-kjc5.2, D8) ------------------------


def _sizing(**overrides) -> SizingConfig:
    defaults = {
        "working_set_min": 8_000,
        "working_set_max": 64_000,
        "build_factors": {"task": 3.0, "bug": 2.0, "chore": 1.5},
        "calibration_min_samples": 10,
        "calibration_window": 50,
    }
    defaults.update(overrides)
    return SizingConfig(**defaults)


def test_check_working_set_inside_band_fits() -> None:
    """An estimate inside the band (bounds inclusive) raises no violation."""
    sizing = _sizing()
    assert policy.check_working_set("t", 20_000, 5_000, sizing) is None
    assert policy.check_working_set("t", 8_000, 5_000, sizing) is None  # floor inclusive
    assert policy.check_working_set("t", 64_000, 5_000, sizing) is None  # ceiling inclusive


def test_check_working_set_above_ceiling_says_split() -> None:
    """Above working_set_max the engine refuses with flatten-and-split guidance."""
    message = policy.check_working_set("huge child", 65_000, 20_000, _sizing())
    assert message is not None
    assert "huge child" in message and "65000" in message
    assert "split" in message and "flatten" in message


def test_check_working_set_below_floor_says_merge_with_sibling() -> None:
    """Below working_set_min (with existing scope material) the guidance is to merge."""
    message = policy.check_working_set("tiny child", 2_000, 500, _sizing())
    assert message is not None
    assert "tiny child" in message and "2000" in message
    assert "merge" in message and "sibling" in message


def test_check_working_set_floor_skips_greenfield_scope() -> None:
    """A scope matching no existing files (nothing to read) is never floor-refused."""
    assert policy.check_working_set("new files child", 2_000, 0, _sizing()) is None


# --- Autonomy grants: session-scoped ledger (basicly-kjc5.3, design D3) --------


L3_CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2, autonomy="L3")
# The engine's own provider, because these tests stand in for a gate the engine
# recorded; a foreign provider no longer counts toward a required gate
# (basicly-jr0l.51).
_VERIFY_GREEN = [{"gate": "verify", "provider": VERIFY_GATE_PROVIDER, "passed": True}]


def test_active_grant_last_marker_wins_and_revocation_turns_it_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ledger is a last-wins scan: later grants replace, a revocation clears."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    assert policy.active_grant(tmp_path, "root") is None

    fake.comments.append("[harness-policy] grant level=L1")
    fake.comments.append("[harness-policy] grant level=L2 budget=100")
    assert policy.active_grant(tmp_path, "root") == policy.Grant(level="L2", token_budget=100)

    policy.revoke_grant(tmp_path, "root")
    assert policy.active_grant(tmp_path, "root") is None

    fake.comments.append("[harness-policy] grant level=L1")
    assert policy.active_grant(tmp_path, "root") == policy.Grant(level="L1", token_budget=None)


def test_parse_grant_skips_malformed_markers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A garbled level or budget never yields a phantom grant."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments += [
        "[harness-policy] grant level=L9",
        "[harness-policy] grant level=L2 budget=lots",
        "[harness-policy] grant level=L3",  # unmetered L2+ must not parse
        "[harness-policy] grant level=L2 budget=-5",
        "plain comment",
    ]
    assert policy.active_grant(tmp_path, "root") is None


def test_issue_grant_interactive_records_the_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A TTY caller under the config ceiling issues directly."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    result = policy.issue_grant_guarded(tmp_path, "root", "L2", 50_000, L3_CONFIG, interactive=True)
    assert result.status == "approved"
    assert policy.active_grant(tmp_path, "root") == policy.Grant(level="L2", token_budget=50_000)


def test_issue_grant_refuses_above_the_autonomy_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """[policy] autonomy is the opt-in ceiling; the default L0 makes grants unissuable."""
    _install(monkeypatch, _FakeBr())
    l0 = PolicyConfig(required_gates=("verify",), max_rework=2)
    result = policy.issue_grant_guarded(tmp_path, "root", "L1", None, l0, interactive=True)
    assert result.status == "rejected"
    assert "autonomy ceiling" in result.detail


def test_issue_grant_refuses_l2_plus_without_token_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unbounded lights-out must be unreachable: L2+ needs a positive budget."""
    _install(monkeypatch, _FakeBr())
    for level in ("L2", "L3"):
        result = policy.issue_grant_guarded(
            tmp_path, "root", level, None, L3_CONFIG, interactive=True
        )
        assert result.status == "rejected"
        assert "token_budget" in result.detail


def test_issue_grant_non_interactive_needs_a_relayed_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An agent cannot self-issue a grant: no TTY yields a challenge, the code approves."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_code(monkeypatch, "cafe0123")
    first = policy.issue_grant_guarded(tmp_path, "root", "L1", None, L3_CONFIG, interactive=False)
    assert first.status == "challenge" and first.code == "cafe0123"
    assert policy.active_grant(tmp_path, "root") is None

    second = policy.issue_grant_guarded(
        tmp_path, "root", "L1", None, L3_CONFIG, interactive=False, confirm="cafe0123"
    )
    assert second.status == "approved"
    assert policy.active_grant(tmp_path, "root") == policy.Grant(level="L1", token_budget=None)


def test_grant_delegates_only_covered_checkpoints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An L2 grant approves classify/decompose non-interactively; ship still challenges."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100000")

    for name in ("classify", "decompose"):
        result = policy.approve_checkpoint_guarded(tmp_path, "i", name, interactive=False)
        assert result.status == "approved"
        assert "delegated under L2 grant" in result.detail
        assert policy.checkpoint_approved(tmp_path, "i", name)

    result = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    assert result.status == "challenge"
    assert not policy.checkpoint_approved(tmp_path, "i", "ship")


def test_grant_spend_halt_drops_delegation_to_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run-record spend at the budget refuses delegation (human-only until re-granted)."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100")
    entry = run_record.build_record(
        agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=150
    )
    run_record.record(tmp_path, "root", entry)

    assert policy.session_spend_tokens(tmp_path, "root") == 150
    result = policy.approve_checkpoint_guarded(
        tmp_path, "root", "classify", interactive=False, grant_root="root"
    )
    assert result.status == "challenge"
    assert not policy.checkpoint_approved(tmp_path, "root", "classify")


def test_spend_status_is_the_one_halt_predicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D3's ceiling as a value, so approval, dispatch, and delegation share one rule."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100")
    for tokens in (60, 30):
        run_record.record(
            tmp_path,
            "root",
            run_record.build_record(
                agent="t",
                handoff=False,
                returncode=0,
                duration_s=1.0,
                command=("t",),
                tokens=tokens,
            ),
        )

    under = policy.spend_status(tmp_path, "root")
    assert (under.spent_tokens, under.halted, under.detail) == (90, False, "")

    run_record.record(
        tmp_path,
        "root",
        run_record.build_record(
            agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=10
        ),
    )
    at_budget = policy.spend_status(tmp_path, "root")
    assert (at_budget.spent_tokens, at_budget.halted) == (100, True)
    assert "100/100 tokens" in at_budget.detail
    assert "until re-granted" in at_budget.detail


def test_spend_status_without_a_budget_is_never_halted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No grant, and an L1 grant with no budget, mean no ceiling - not a halt.

    A halt-by-default would freeze every ungranted (human-driven) session the
    moment any run recorded a token.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    run_record.record(
        tmp_path,
        "root",
        run_record.build_record(
            agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=999_999
        ),
    )

    ungranted = policy.spend_status(tmp_path, "root")
    assert (ungranted.grant, ungranted.halted) == (None, False)

    fake.comments.append("[harness-policy] grant level=L1")
    unbudgeted = policy.spend_status(tmp_path, "root")
    assert unbudgeted.grant is not None
    assert (unbudgeted.grant.level, unbudgeted.halted) == ("L1", False)


def test_session_spend_sums_the_children_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The grant's meter covers the whole session: root plus parent-child beads."""
    fake = _FakeBr(
        dependents=[{"id": "root.1", "dependency_type": "parent-child", "status": "open"}]
    )
    _install(monkeypatch, fake)
    for issue_id, tokens in (("root", 40), ("root.1", 60), ("unrelated", 999)):
        entry = run_record.build_record(
            agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=tokens
        )
        run_record.record(tmp_path, issue_id, entry)
    assert policy.session_spend_tokens(tmp_path, "root") == 100


def test_l3_ship_delegates_only_when_preconditions_hold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lights-out ship needs green gates, no rework escalation, no needs-input (D3)."""
    fake = _FakeBr(gates=_VERIFY_GREEN)
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")

    result = policy.approve_checkpoint_guarded(tmp_path, "root", "ship", interactive=False)
    assert result.status == "approved"
    assert "delegated under L3 grant" in result.detail


def test_l3_ship_refuses_on_any_wrinkle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Any precondition violation drops ship back to human (challenge, no marker)."""
    # A needs-input event recorded in the session.
    fake = _FakeBr(gates=_VERIFY_GREEN)
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")
    policy.record_needs_input(tmp_path, "root", "which API version")
    result = policy.approve_checkpoint_guarded(tmp_path, "root", "ship", interactive=False)
    assert result.status == "challenge"

    # A rework escalation (attempts at the cap).
    fake = _FakeBr(gates=_VERIFY_GREEN)
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")
    fake.comments += ["[harness-policy] rework gate=verify"] * 2
    result = policy.approve_checkpoint_guarded(tmp_path, "root", "ship", interactive=False)
    assert result.status == "challenge"

    # Required gate not green.
    fake = _FakeBr(gates=[])
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")
    result = policy.approve_checkpoint_guarded(tmp_path, "root", "ship", interactive=False)
    assert result.status == "challenge"


def _open_epic_with_green_child(**gates: object) -> _FakeBr:
    """An open epic root whose own verify gate is missing, and a green child.

    The shape every real multi-lane session has mid-run: the epic cannot have a
    verify gate until it closes, so it is the child that is shippable.
    """
    child = {"id": "root.1", "dependency_type": "parent-child", "status": "open"}
    return _FakeBr(
        dependents=[child],
        gates_by_issue={"root": [], "root.1": _VERIFY_GREEN},
        **gates,  # type: ignore[arg-type]
    )


def test_l3_delegates_a_green_childs_ship_while_the_root_is_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The case lights-out exists for (basicly-kjc5.39, owner decision 2026-07-25).

    Scoped to the grant root's gates this refused unconditionally - an epic's own
    verify gate is missing until the epic closes - so L3 degraded to L2 for every
    multi-lane session.
    """
    fake = _open_epic_with_green_child()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")

    result = policy.approve_checkpoint_guarded(
        tmp_path, "root.1", "ship", interactive=False, grant_root="root"
    )

    assert result.status == "approved"
    assert "delegated under L3 grant" in result.detail
    assert policy.checkpoint_approved(tmp_path, "root.1", "ship")


def test_l3_still_refuses_a_child_whose_own_gates_are_not_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Scoping the check moved it to the shipped node; it did not remove it."""
    fake = _FakeBr(
        dependents=[{"id": "root.1", "dependency_type": "parent-child", "status": "open"}],
        gates_by_issue={"root": _VERIFY_GREEN, "root.1": []},
    )
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")

    result = policy.approve_checkpoint_guarded(
        tmp_path, "root.1", "ship", interactive=False, grant_root="root"
    )

    assert result.status == "challenge"
    assert not policy.checkpoint_approved(tmp_path, "root.1", "ship")


def test_l3_child_ship_still_refuses_on_a_session_wide_wrinkle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other two preconditions stay session-wide: a wrinkle anywhere drops ship."""
    fake = _open_epic_with_green_child()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")
    # Recorded on the ROOT, not on the child being shipped.
    policy.record_needs_input(tmp_path, "root", "which API version")

    result = policy.approve_checkpoint_guarded(
        tmp_path, "root.1", "ship", interactive=False, grant_root="root"
    )

    assert result.status == "challenge"


class _PerIssueBr(_FakeBr):
    """A fake whose comments are per bead, so a violation can name a *sibling*.

    The base fake shares one comment list across every issue, which cannot express
    the incident this diagnostic exists for (basicly-5ltn): the wrinkle sits on a
    different bead than the one being shipped.
    """

    def __init__(self, comments: dict[str, list[str]], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.per_issue = comments

    def __call__(self, repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["comments", "list"]:
            texts = self.per_issue.get(args[2], [])
            return _Proc(json.dumps([{"text": t, "created_at": _EPOCH} for t in texts]))
        if args[:2] == ["comments", "add"]:
            self.per_issue.setdefault(args[2], []).append(args[-1])
            return _Proc("")
        return super().__call__(repo_root, args, _check=_check)


def _epic_with_a_wrinkled_sibling(
    *, sibling_status: str = "open", wrinkle: str = "[harness-policy] rework gate=verify"
) -> _PerIssueBr:
    """An L3-granted epic, a green child to ship, and a wrinkle on its sibling.

    *sibling_status* is the whole distinction basicly-i1s8 turns on: the same two
    markers are a live violation while root.2 is open and resolved history once it
    is closed. *wrinkle* swaps the carrier, since needs-input is discounted on the
    same rule.
    """
    children = [
        {"id": "root.1", "dependency_type": "parent-child", "status": "open"},
        {"id": "root.2", "dependency_type": "parent-child", "status": sibling_status},
    ]
    return _PerIssueBr(
        {
            "root": ["[harness-policy] grant level=L3 budget=1000000"],
            "root.2": [wrinkle] * 2,
        },
        records={
            "root": {"status": "open", "dependents": children},
            "root.1": {"status": "open", "dependents": []},
            "root.2": {"status": sibling_status, "dependents": []},
        },
        gates_by_issue={"root": [], "root.1": _VERIFY_GREEN, "root.2": []},
    )


def test_a_declined_child_ship_names_the_precondition_and_its_sibling_bead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The measured incident (basicly-5ltn): a bare confirmation request said nothing.

    A grant existed, covered ship, was not spend-halted, and declined because of a
    rework escalation on another bead in the epic's session - which took several
    tool calls to find by hand. The decision is unchanged; only the reason is new.
    """
    _install(monkeypatch, _epic_with_a_wrinkled_sibling())

    result = policy.approve_checkpoint_guarded(
        tmp_path, "root.1", "ship", interactive=False, grant_root="root"
    )

    assert result.status == "challenge"
    assert "the active L3 grant covers ship but declined it" in result.detail
    assert "lights-out preconditions across session root" in result.detail
    # The wrinkle is on a sibling, not on the node being shipped: naming the bead
    # is the whole point of the message.
    assert "rework escalation on root.2 (gate verify: 2/2)" in result.detail
    # Still refused, with no marker recorded: a code must still come back.
    assert result.code
    assert not policy.checkpoint_approved(tmp_path, "root.1", "ship")


def test_a_rework_escalation_counts_only_while_its_bead_is_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """basicly-i1s8, both directions of the one rule, on identical markers.

    Rework markers are append-only and nothing marks an escalation resolved, so the
    escalation on a bead that was fixed, shipped and closed read as a live
    session-wide violation forever (basicly-kjc5.56 poisoned every ship under
    basicly-kjc5). Closing the bead resolves it; leaving it open does not.
    """
    _install(monkeypatch, _epic_with_a_wrinkled_sibling(sibling_status="open"))
    live = policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1")
    assert live == ("rework escalation on root.2 (gate verify: 2/2)",)

    _install(monkeypatch, _epic_with_a_wrinkled_sibling(sibling_status="closed"))
    assert policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1") == ()


def test_a_needs_input_event_counts_only_while_its_bead_is_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other append-only carrier is discounted on the same rule, not left behind."""
    missing_fact = "[harness-policy] needs-input which flag"
    _install(monkeypatch, _epic_with_a_wrinkled_sibling(wrinkle=missing_fact))
    live = policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1")
    assert live == ("2 needs-input event(s) recorded on root.2",)

    _install(
        monkeypatch,
        _epic_with_a_wrinkled_sibling(sibling_status="closed", wrinkle=missing_fact),
    )
    assert policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1") == ()


def test_a_closed_siblings_escalation_lets_the_grant_delegate_the_child_ship(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The consequence the fix exists for: L3 stops degrading to L2 for the epic's life."""
    _install(monkeypatch, _epic_with_a_wrinkled_sibling(sibling_status="closed"))

    result = policy.approve_checkpoint_guarded(
        tmp_path, "root.1", "ship", interactive=False, grant_root="root"
    )

    assert result.status == "approved"
    assert result.detail == "delegated under L3 grant"
    assert policy.checkpoint_approved(tmp_path, "root.1", "ship")


def test_a_challenge_with_no_grant_carries_no_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No grant means nothing was consulted, so the challenge stays exactly as bare."""
    _install(monkeypatch, _FakeBr(gates=_VERIFY_GREEN))

    result = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)

    assert result.status == "challenge"
    assert result.detail == ""


def test_a_spend_halted_grant_says_so_on_the_challenge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A halt is the other silent decline: it quotes spend_status's own wording."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100")
    run_record.record(
        tmp_path,
        "root",
        run_record.build_record(
            agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=150
        ),
    )

    result = policy.approve_checkpoint_guarded(
        tmp_path, "root", "classify", interactive=False, grant_root="root"
    )

    assert result.status == "challenge"
    assert "the active L2 grant covers classify but declined it" in result.detail
    assert "token_budget spent (150/100 tokens" in result.detail


def test_a_grant_that_does_not_delegate_the_checkpoint_says_which_level_it_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An L2 grant and a ship ask: the operator is told the level, not left guessing."""
    fake = _FakeBr(gates=_VERIFY_GREEN)
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100000")

    result = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)

    assert result.status == "challenge"
    assert "the active L2 grant on i does not delegate ship" in result.detail


def test_a_grant_outside_its_session_says_the_issue_is_not_in_the_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The foreign-issue refusal is silent too, and reads as "no grant" without this."""
    fake = _FakeBr()  # no dependents: the session is just "root"
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100000")

    result = policy.approve_checkpoint_guarded(
        tmp_path, "unrelated", "classify", interactive=False, grant_root="root"
    )

    assert result.status == "challenge"
    assert "the active L2 grant on root does not cover unrelated" in result.detail
    assert "not in that session's issue tree" in result.detail


def test_lights_out_gate_check_defaults_to_the_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Omitting *shipping* keeps the old single-node behaviour: the root is the node."""
    _install(monkeypatch, _FakeBr(gates_by_issue={"root": [], "root.1": _VERIFY_GREEN}))

    violations = policy.lights_out_violations(tmp_path, "root", CONFIG)
    assert any("required gates not green on root" in v for v in violations)

    scoped = policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1")
    assert not any("required gates not green" in v for v in scoped)


def test_lights_out_violations_name_each_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The precondition report is specific enough to act on."""
    fake = _FakeBr(gates=[])
    _install(monkeypatch, fake)
    policy.record_needs_input(tmp_path, "root", "missing fact")
    fake.comments += ["[harness-policy] rework gate=verify"] * 2

    violations = policy.lights_out_violations(tmp_path, "root", CONFIG)

    assert any("required gates not green" in v for v in violations)
    assert any("needs-input" in v for v in violations)
    assert any("rework escalation" in v for v in violations)


def test_grant_never_authorizes_outside_its_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """grant_root is caller-supplied: a grant covers only its own session tree."""
    fake = _FakeBr()  # no dependents: the session is just "root"
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100000")

    foreign = policy.approve_checkpoint_guarded(
        tmp_path, "unrelated", "classify", interactive=False, grant_root="root"
    )
    assert foreign.status == "challenge"
    assert not policy.checkpoint_approved(tmp_path, "unrelated", "classify")

    fake.dependents.append({"id": "unrelated", "dependency_type": "parent-child"})
    member = policy.approve_checkpoint_guarded(
        tmp_path, "unrelated", "classify", interactive=False, grant_root="root"
    )
    assert member.status == "approved"


def test_active_grant_expires_with_a_closed_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A grant on a closed session root is dead without an explicit revocation."""
    fake = _FakeBr(status="closed")
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L1")
    assert policy.active_grant(tmp_path, "root") is None


def test_session_ids_walk_the_tree_transitively(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Grandchild spend counts toward the budget: the session walk is not depth-1."""
    child = {"id": "root.1", "dependency_type": "parent-child"}
    grandchild = {"id": "root.1.1", "dependency_type": "parent-child"}
    fake = _FakeBr(
        records={
            "root": {"status": "open", "dependents": [child]},
            "root.1": {"status": "open", "dependents": [grandchild]},
            "root.1.1": {"status": "open", "dependents": []},
        }
    )
    _install(monkeypatch, fake)
    entry = run_record.build_record(
        agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=70
    )
    run_record.record(tmp_path, "root.1.1", entry)
    assert policy.session_spend_tokens(tmp_path, "root") == 70


def test_grant_confirm_code_binds_level_and_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relayed code issues exactly the grant the human saw, not a swapped budget."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_code(monkeypatch, "beef4242")
    challenge = policy.issue_grant_guarded(
        tmp_path, "root", "L2", 5_000, L3_CONFIG, interactive=False
    )
    assert challenge.status == "challenge"

    swapped = policy.issue_grant_guarded(
        tmp_path, "root", "L2", 999_999, L3_CONFIG, interactive=False, confirm="beef4242"
    )
    assert swapped.status == "rejected"
    assert policy.active_grant(tmp_path, "root") is None

    exact = policy.issue_grant_guarded(
        tmp_path, "root", "L2", 5_000, L3_CONFIG, interactive=False, confirm="beef4242"
    )
    assert exact.status == "approved"
    assert policy.active_grant(tmp_path, "root") == policy.Grant(level="L2", token_budget=5_000)


# --- Forgiving a flake without hiding it (basicly-55yh) ----------------------


def test_an_unreliable_gate_event_is_recorded_and_counted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Forgiven is not the same as invisible: a chronic flake has to stay countable."""
    _install(monkeypatch, _FakeBr())
    assert policy.unreliable_gate_events(tmp_path, "i", "merge") == 0

    assert policy.record_unreliable_gate(tmp_path, "i", "merge", "pytest passed on re-run") == 1
    assert policy.record_unreliable_gate(tmp_path, "i", "merge") == 2
    assert policy.unreliable_gate_events(tmp_path, "i", "merge") == 2


def test_an_unreliable_gate_event_is_not_a_rework_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two markers must never be confused by a counter — that was the bug."""
    _install(monkeypatch, _FakeBr())
    policy.record_unreliable_gate(tmp_path, "i", "merge")

    assert policy.rework_attempts(tmp_path, "i", "merge") == 0
    assert policy.rework_charged(tmp_path, "i", "merge") == 0
    assert policy.should_escalate(tmp_path, "i", "merge", CONFIG) is False


def test_an_unreliable_gate_event_is_scoped_to_its_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A flaky merge gate says nothing about the verify gate."""
    _install(monkeypatch, _FakeBr())
    policy.record_unreliable_gate(tmp_path, "i", "merge")
    assert policy.unreliable_gate_events(tmp_path, "i", "verify") == 0


# --- ...and escalating it rather than deferring forever (basicly-jr0l.41) ------


def test_the_count_reaches_a_bound_that_forces_a_human_look(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Forgiving without a bound is a livelock: no budget spent, so no cap reached.

    The count was always returned here; what was missing was anything comparing
    it to a limit, so a chronically unreliable gate deferred its lane forever
    while the lane looked merely slow.
    """
    _install(monkeypatch, _FakeBr())
    counts = [
        policy.record_unreliable_gate(tmp_path, "i", "merge")
        for _ in range(policy.MAX_UNRELIABLE_GATE_EVENTS)
    ]

    assert counts[-1] == policy.MAX_UNRELIABLE_GATE_EVENTS
    assert counts[-2] < policy.MAX_UNRELIABLE_GATE_EVENTS  # the bound is reached, not skipped
    # Still not rework: the flake is no evidence against the work.
    assert policy.rework_charged(tmp_path, "i", "merge") == 0


def test_the_two_escalations_stay_distinguishable_in_one_queue() -> None:
    """Both ride the same decision kind, so only the question text tells them apart.

    They answer different questions — untrustworthy result versus wrong work — and
    a driver acting on one must never parse it as the other.
    """
    unreliable = policy.unreliable_gate_escalation_question("merge")
    rework = policy.rework_escalation_question("merge")

    assert policy.gate_from_unreliable_escalation(unreliable) == "merge"
    assert policy.gate_from_rework_escalation(rework) == "merge"
    assert policy.gate_from_rework_escalation(unreliable) is None
    assert policy.gate_from_unreliable_escalation(rework) is None


# --- A budget meters the grant, not the session's lifetime (basicly-jr0l.17) ---


def _with_spend(monkeypatch: pytest.MonkeyPatch, total: int) -> None:
    """Pin the session's run-record spend total."""
    monkeypatch.setattr(policy, "session_spend_tokens", lambda *_a, **_k: total)


def test_a_fresh_grant_admits_dispatch_on_a_historically_expensive_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reported defect: 30.7M of history halted a 5M grant before it dispatched once."""
    _install(monkeypatch, _FakeBr())
    _with_spend(monkeypatch, 30_705_839)
    grant = policy.Grant(level="L1", token_budget=5_000_000, spent_at_issue=30_705_839)

    status = policy.spend_status(tmp_path, "root", grant=grant)

    assert status.halted is False


def test_the_budget_halts_once_spend_under_this_grant_reaches_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It is still a real ceiling — only the baseline moved."""
    _install(monkeypatch, _FakeBr())
    _with_spend(monkeypatch, 30_705_839 + 5_000_000)
    grant = policy.Grant(level="L1", token_budget=5_000_000, spent_at_issue=30_705_839)

    status = policy.spend_status(tmp_path, "root", grant=grant)

    assert status.halted is True
    assert "5000000/5000000 tokens under this grant" in status.detail
    assert "35705839 lifetime" in status.detail  # both numbers, so neither misleads


def test_a_grant_without_a_baseline_keeps_the_strict_lifetime_behaviour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A marker issued before this existed must not become quietly unbounded."""
    _install(monkeypatch, _FakeBr())
    _with_spend(monkeypatch, 6_000_000)
    grant = policy.Grant(level="L1", token_budget=5_000_000)

    assert grant.spent_at_issue == 0
    assert policy.spend_status(tmp_path, "root", grant=grant).halted is True


def test_lost_run_records_cannot_buy_extra_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Spend below the baseline clamps at zero rather than crediting the session."""
    _install(monkeypatch, _FakeBr())
    _with_spend(monkeypatch, 1_000)  # records pruned since issuance
    grant = policy.Grant(level="L1", token_budget=5_000, spent_at_issue=30_000)

    status = policy.spend_status(tmp_path, "root", grant=grant)

    assert status.halted is False
    assert "0/5000 tokens under this grant" in status.detail or not status.detail


def test_the_baseline_round_trips_through_the_grant_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ledger is comment markers, so the baseline has to survive a write/read."""
    _install(monkeypatch, _FakeBr())
    monkeypatch.setattr(policy, "session_spend_tokens", lambda *_a, **_k: 30_705_839)
    result = policy.issue_grant_guarded(
        tmp_path, "root", "L1", 5_000_000, L3_CONFIG, interactive=True
    )
    assert result.status == "approved"

    grant = policy.active_grant(tmp_path, "root")
    assert grant is not None
    assert grant.spent_at_issue == 30_705_839
    assert grant.token_budget == 5_000_000


def test_a_grant_with_no_prior_spend_records_no_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean session's marker stays exactly as it was before this change."""
    _install(monkeypatch, _FakeBr())
    monkeypatch.setattr(policy, "session_spend_tokens", lambda *_a, **_k: 0)
    policy.issue_grant_guarded(tmp_path, "root", "L1", 5_000_000, L3_CONFIG, interactive=True)

    assert "baseline=" not in policy._run_br.comments[-1]  # type: ignore[attr-defined]


def test_nothing_in_the_spend_gate_reads_a_clock() -> None:
    """Determinism pin: a spend gate must not branch on a wall clock.

    The tracker's own ``updated_at cannot be before created_at`` defect is what
    this rule exists for — a clock-ordered budget would fail the same way, and
    silently.

    Identifiers only, parsed rather than grepped: a prose mention of "timestamp"
    in a comment explaining that nothing compares one is not a clock read, and
    scanning the raw text flagged exactly that.
    """
    banned = {"timestamp", "datetime", "created_at", "updated_at", "time", "now"}
    for func in (policy.spend_status, policy.session_spend_tokens):
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        used = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Name | ast.Attribute)
        }
        leaked = used & banned
        assert not leaked, f"{func.__name__} reads a clock: {leaked}"


# --- Human wait time (basicly-kjc5.51, D11) ----------------------------------

_ASKED_AT = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _pin_clocks(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr, *, waited_s: int) -> None:
    """Ask at :data:`_ASKED_AT` on the tracker's clock, answer *waited_s* later.

    Both clocks are pinned rather than slept on: the interval under test is the
    measurement itself, so it must not depend on how long the test took to run.
    """
    fake.now = _ASKED_AT.isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(policy, "_now", lambda: _ASKED_AT.timestamp() + waited_s)


def test_a_checkpoint_challenge_starts_the_clock_a_relayed_code_stops_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The wait a human made the track sit through is recorded with who ended it."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_clocks(monkeypatch, fake, waited_s=420)

    challenge = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    assert challenge.status == "challenge"
    assert policy.wait_events(tmp_path, "i") == ()  # still waiting: nothing to record yet

    approved = policy.approve_checkpoint_guarded(
        tmp_path, "i", "ship", interactive=False, confirm=challenge.code
    )
    assert approved.status == "approved"

    (event,) = policy.wait_events(tmp_path, "i")
    assert (event.kind, event.subject, event.waited_s) == ("checkpoint", "ship", 420)
    assert (event.answered_by, event.delegated) == (policy.HUMAN_BY, False)
    assert event.wait_id == policy.wait_id_for_checkpoint("i", "ship")


def test_a_reissued_challenge_neither_duplicates_nor_restarts_the_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The wait began at the first ask; a later ask is the same wait, still running.

    A code expires after 15 minutes, so a track waiting on a human overnight is
    challenged repeatedly — restarting the clock on each one would report the last
    few minutes and hide the whole night.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_clocks(monkeypatch, fake, waited_s=7_200)

    first = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    fake.now = "2026-07-26T13:30:00Z"  # the tracker's clock moved on
    again = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    assert (first.status, again.status) == ("challenge", "challenge")
    assert sum(1 for text in fake.comments if "kind=checkpoint requested" in text) == 1

    policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False, confirm=again.code)
    (event,) = policy.wait_events(tmp_path, "i")
    assert event.waited_s == 7_200


def test_a_covering_grant_records_no_wait_because_it_removed_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The value of an autonomy grant is the wait absent from the rollup (D3/D11)."""
    fake = _FakeBr(gates=_VERIFY_GREEN)
    _install(monkeypatch, fake)
    _pin_clocks(monkeypatch, fake, waited_s=600)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")

    approved = policy.approve_checkpoint_guarded(tmp_path, "root", "ship", interactive=False)

    assert approved.status == "approved"
    assert policy.wait_events(tmp_path, "root") == ()
    summary = policy.session_wait_summary(tmp_path, "root")
    assert (summary.human_wait_s, summary.delegated_wait_s) == (0, 0)


def test_a_grant_that_ends_an_open_wait_is_recorded_as_delegated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A wait the harness ended itself is not human time — it is measured apart."""
    fake = _FakeBr(gates=_VERIFY_GREEN)
    _install(monkeypatch, fake)
    _pin_clocks(monkeypatch, fake, waited_s=900)

    # Asked first (no grant yet), then a grant arrives and disposes of it.
    asked = policy.approve_checkpoint_guarded(tmp_path, "root", "ship", interactive=False)
    assert asked.status == "challenge"
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")
    approved = policy.approve_checkpoint_guarded(tmp_path, "root", "ship", interactive=False)
    assert approved.status == "approved"

    (event,) = policy.wait_events(tmp_path, "root")
    assert (event.answered_by, event.delegated, event.waited_s) == ("grant:L3", True, 900)
    summary = policy.session_wait_summary(tmp_path, "root")
    assert (summary.human_wait_s, summary.delegated_wait_s) == (0, 900)


def test_the_session_rollup_reports_wait_apart_from_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two durations are never added: dispatch is compute, wait is wall clock."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_clocks(monkeypatch, fake, waited_s=1_800)
    challenge = policy.approve_checkpoint_guarded(tmp_path, "root", "classify", interactive=False)
    policy.approve_checkpoint_guarded(
        tmp_path, "root", "classify", interactive=False, confirm=challenge.code
    )
    for duration in (12.5, 30.0):
        run_record.record(
            tmp_path,
            "root",
            run_record.build_record(
                agent="t", handoff=False, returncode=0, duration_s=duration, command=("t",)
            ),
        )

    summary = policy.session_wait_summary(tmp_path, "root")

    assert summary.human_wait_s == 1_800
    assert summary.dispatch_s == 42.5
    assert [e.subject for e in summary.events] == ["classify"]


def test_dispatch_seconds_cover_the_children_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Session-scoped like the spend meter: a lane's time is the session's time."""
    fake = _FakeBr(
        dependents=[{"id": "root.1", "dependency_type": "parent-child", "status": "open"}]
    )
    _install(monkeypatch, fake)
    for issue_id, duration in (("root", 10.0), ("root.1", 5.0), ("unrelated", 900.0)):
        run_record.record(
            tmp_path,
            issue_id,
            run_record.build_record(
                agent="t", handoff=False, returncode=0, duration_s=duration, command=("t",)
            ),
        )
    assert policy.session_dispatch_seconds(tmp_path, "root") == 15.0


def _wait_marker(wait_id: str, requested_at: str, answered_at: str, /, **overrides: object) -> str:
    """One answered ``[harness-wait]`` marker, as :func:`policy.record_wait` writes it."""
    start, end = policy._parse_ts(requested_at), policy._parse_ts(answered_at)
    assert start is not None and end is not None
    payload: dict[str, object] = {
        "answered_at": answered_at,
        "by": policy.HUMAN_BY,
        "delegated": False,
        "kind": "checkpoint",
        "requested_at": requested_at,
        "subject": "ship",
        "waited_s": int(end.timestamp() - start.timestamp()),
    }
    payload |= overrides
    header = f"{policy.WAIT_MARKER} id={wait_id} kind={payload['kind']} answered"
    return f"{header}\n{json.dumps(payload, sort_keys=True)}"


def test_overlapping_waits_count_once_but_separate_ones_add_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lanes wait on the same human at the same time, so the total is a union.

    One ask can also be recorded twice — the supervisor queues a ``checkpoint``
    decision *and* mints the challenge behind it — and summing those would report
    twice the wall clock a forecast is trying to predict.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments += [
        _wait_marker("i#a", "2026-07-26T12:00:00Z", "2026-07-26T13:00:00Z"),
        _wait_marker("i#b", "2026-07-26T12:30:00Z", "2026-07-26T13:30:00Z"),
    ]
    assert policy.session_wait_summary(tmp_path, "i").human_wait_s == 5_400  # 12:00 -> 13:30

    fake.comments += [_wait_marker("i#c", "2026-07-26T14:00:00Z", "2026-07-26T14:20:00Z")]
    assert policy.session_wait_summary(tmp_path, "i").human_wait_s == 6_600


def test_a_wait_with_unusable_stamps_still_counts_its_own_interval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A garbled marker must under-report the overlap, never the wait itself."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append(
        _wait_marker("i#a", "2026-07-26T12:00:00Z", "2026-07-26T12:05:00Z", requested_at="whenever")
    )
    assert policy.session_wait_summary(tmp_path, "i").human_wait_s == 300


def test_a_garbled_wait_marker_is_skipped_not_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Evidence reads stay best-effort: a malformed marker never wedges a rollup."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments += [
        "[harness-wait] kind=checkpoint answered",  # no id
        "[harness-wait] id=i#wait-ship kind=checkpoint answered\nnot json",
        '[harness-wait] id=i#wait-ship kind=vibes answered\n{"kind": "vibes", "waited_s": 5}',
        '[harness-wait] id=i#wait-ship kind=decision answered\n{"kind": "decision"}',
    ]
    assert policy.wait_events(tmp_path, "i") == ()


def test_a_clock_that_steps_backwards_records_no_negative_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A wait cannot be negative; the tracker's clock and ours can still disagree."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_clocks(monkeypatch, fake, waited_s=-90)

    event = policy.record_wait(
        tmp_path,
        "i",
        wait_id="i#wait-ship",
        kind="checkpoint",
        subject="ship",
        requested_at=fake.now,
        by=policy.HUMAN_BY,
        delegated=False,
    )

    assert event is not None
    assert event.waited_s == 0


def test_an_unmeasurable_start_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No parseable start means no interval - and an invented one would be worse."""
    fake = _FakeBr()
    _install(monkeypatch, fake)

    assert (
        policy.record_wait(
            tmp_path,
            "i",
            wait_id="i#wait-ship",
            kind="checkpoint",
            subject="ship",
            requested_at="",
            by=policy.HUMAN_BY,
            delegated=False,
        )
        is None
    )
    assert fake.comments == []


def test_record_wait_rejects_an_unknown_kind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The kind is what the rollup groups by, so an unknown one is a programming error."""
    _install(monkeypatch, _FakeBr())
    with pytest.raises(ValueError, match="unknown wait kind"):
        policy.record_wait(
            tmp_path,
            "i",
            wait_id="i#wait-ship",
            kind="vibes",
            subject="ship",
            requested_at=_EPOCH,
            by=policy.HUMAN_BY,
            delegated=False,
        )


# --- declared evidence artifacts (basicly-m4zv.13) --------------------------


def _evidence_config(**declarations: str) -> PolicyConfig:
    return PolicyConfig(required_gates=("verify",), max_rework=2, evidence=dict(declarations))


def test_a_phase_that_declares_nothing_is_satisfied(tmp_path: Path) -> None:
    """The mechanism is opt-in: no declaration is not a failure, and costs no I/O."""
    status = policy.evidence_status(tmp_path, CONFIG, "verify")
    assert status.satisfied and status.declared is None and status.path is None


def test_a_declared_artifact_that_is_present_and_non_empty_satisfies(tmp_path: Path) -> None:
    """The whole positive case: the file is there, so the phase may report success."""
    (tmp_path / "run.log").write_text("2 passed", encoding="utf-8")
    status = policy.evidence_status(tmp_path, _evidence_config(verify="run.log"), "verify")
    assert status.satisfied and status.declared == "run.log"
    assert status.path == tmp_path / "run.log"


def test_a_declared_artifact_that_is_missing_refuses_with_the_remedy(tmp_path: Path) -> None:
    """The refusal names the path and what to do, not just that something is wrong."""
    status = policy.evidence_status(tmp_path, _evidence_config(verify="run.log"), "verify")
    assert not status.satisfied
    assert "run.log" in status.reason
    assert "produce it before advancing" in status.reason
    assert "[policy.evidence] verify declaration" in status.reason


def test_a_declared_artifact_that_is_empty_refuses(tmp_path: Path) -> None:
    """An empty file is the shape a redirect that captured nothing leaves behind."""
    (tmp_path / "run.log").write_text("", encoding="utf-8")
    status = policy.evidence_status(tmp_path, _evidence_config(verify="run.log"), "verify")
    assert not status.satisfied and "is empty" in status.reason


def test_a_directory_is_not_an_artifact(tmp_path: Path) -> None:
    """`mkdir -p` on the declared path must not read as evidence."""
    (tmp_path / "evidence").mkdir()
    status = policy.evidence_status(tmp_path, _evidence_config(verify="evidence"), "verify")
    assert not status.satisfied and "not a readable file" in status.reason


def test_the_engine_never_looks_inside_the_artifact(tmp_path: Path) -> None:
    """Presence only: unparseable bytes satisfy it exactly as a tidy report would.

    Pinned because the cheapness of this gate *is* the absence of a parser — the
    moment content is judged, a schema and a verdict move onto the deterministic
    side of the gate contract.
    """
    (tmp_path / "run.log").write_bytes(b"\x00\xff not json, not text")
    assert policy.evidence_status(tmp_path, _evidence_config(verify="run.log"), "verify").satisfied


def test_an_absolute_declared_path_is_refused(tmp_path: Path) -> None:
    """`Path(root) / '/etc/hostname'` is `/etc/hostname` — a gate satisfied forever."""
    status = policy.evidence_status(tmp_path, _evidence_config(verify="/etc/hostname"), "verify")
    assert not status.satisfied and "absolute path" in status.reason


def test_a_traversing_declared_path_is_refused(tmp_path: Path) -> None:
    """Refused after resolution, so it does not depend on spotting a literal '..'."""
    outside = tmp_path.parent / "outside.log"
    outside.write_text("x", encoding="utf-8")
    status = policy.evidence_status(
        tmp_path, _evidence_config(verify=f"../{outside.name}"), "verify"
    )
    assert not status.satisfied and "outside the checkout" in status.reason


def test_containment_holds_for_a_checkout_reached_through_a_symlink(tmp_path: Path) -> None:
    """A symlinked checkout must not fail its own containment test.

    macOS `/tmp` is a symlink to `/private/tmp`, so comparing an unresolved root
    against a resolved candidate would refuse every declaration there — a
    platform-only break, made test data rather than left to CI (basicly-m4zv.12
    hit exactly this shape).
    """
    real = tmp_path / "real"
    real.mkdir()
    (real / "run.log").write_text("x", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError, NotImplementedError:  # pragma: no cover - unprivileged Windows
        pytest.skip("symlinks not available on this platform")
    assert policy.evidence_status(link, _evidence_config(verify="run.log"), "verify").satisfied


def test_a_declaration_naming_the_checkout_itself_is_reported_as_not_a_file(
    tmp_path: Path,
) -> None:
    """`verify = "."` resolves to the root, which is inside it — so say what is wrong.

    Pins the ordering in the containment check: comparing membership alone would
    report the checkout root as being outside itself.
    """
    status = policy.evidence_status(tmp_path, _evidence_config(verify="."), "verify")
    assert not status.satisfied and "not a readable file" in status.reason


def test_an_empty_declaration_refuses_rather_than_reading_as_absent(tmp_path: Path) -> None:
    """`verify = ""` is a declaration, so it blocks; silently ignoring it would be a lie."""
    status = policy.evidence_status(tmp_path, _evidence_config(verify=""), "verify")
    assert not status.satisfied and "declares an empty path" in status.reason


def test_a_misspelled_phase_refuses_every_phase(tmp_path: Path) -> None:
    """Fail closed: the engine cannot tell which phase `verfiy` meant.

    The alternative is worse than strict — a requirement the operator believes is
    on and that never fires once. So the refusal covers phases the typo does not
    name, and says which key to fix.
    """
    config = PolicyConfig(required_gates=("verify",), max_rework=2, evidence={"verfiy": "run.log"})
    for phase in ("intake", "build", "verify", "ship"):
        status = policy.evidence_status(tmp_path, config, phase)
        assert not status.satisfied
        assert "verfiy" in status.reason and "unknown phase" in status.reason


def test_unknown_evidence_phases_lists_only_the_unknown_ones() -> None:
    """Every real phase name is accepted, so the guard cannot reject a valid config."""
    config = PolicyConfig(
        required_gates=("verify",),
        max_rework=2,
        evidence=dict.fromkeys(LOOP_PHASES, "run.log") | {"zzz": "x", "aaa": "y"},
    )
    assert policy.unknown_evidence_phases(config) == ("aaa", "zzz")


def test_record_evidence_writes_one_marker_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The path is recorded on the bead once, so a re-entered advance cannot stack it."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    assert policy.record_evidence(tmp_path, "i", "verify", ".basicly/evidence/verify.log")
    assert not policy.record_evidence(tmp_path, "i", "verify", ".basicly/evidence/verify.log")
    assert fake.comments == [
        f"{policy.EVIDENCE_MARKER} phase=verify path=.basicly/evidence/verify.log"
    ]


def test_record_evidence_keeps_one_marker_per_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two phases declaring artifacts leave two markers, not one shadowing the other."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    policy.record_evidence(tmp_path, "i", "verify", "v.log")
    policy.record_evidence(tmp_path, "i", "build", "b.log")
    assert len(fake.comments) == 2
    assert any("phase=build" in text for text in fake.comments)


# --- D3 looking forward: the remainder and the pass predicate (basicly-jr0l.22) ---


def _status(budget: int | None, spent: int, *, baseline: int = 0) -> policy.SpendStatus:
    grant = policy.Grant(level="L3", token_budget=budget, spent_at_issue=baseline)
    return policy.SpendStatus(grant=grant, spent_tokens=spent, halted=False)


def test_remaining_tokens_is_the_budget_less_what_this_grant_authorized() -> None:
    """Metered against the grant's own baseline, not the session's lifetime spend."""
    assert _status(10_000, 30_000, baseline=25_000).remaining_tokens == 5_000


def test_remaining_tokens_never_goes_negative() -> None:
    """An overspent grant has nothing left, not a negative allowance to compare against."""
    assert _status(10_000, 12_000).remaining_tokens == 0


def test_remaining_tokens_ignores_spend_below_the_grant_baseline() -> None:
    """Pruned or lost run records must never buy extra budget.

    The same clamp :func:`spend_status` applies: a total that has dropped below the
    baseline reads as zero spent under this grant, not as a credit.
    """
    assert _status(10_000, 3_000, baseline=8_000).remaining_tokens == 10_000


def test_remaining_tokens_is_none_without_a_ceiling() -> None:
    """No grant and an L1 grant with no budget both mean there is nothing to enforce."""
    ungranted = policy.SpendStatus(grant=None, spent_tokens=5_000, halted=False)
    assert ungranted.remaining_tokens is None
    assert _status(None, 5_000).remaining_tokens is None


def test_check_pass_spend_refuses_a_forecast_over_the_remainder() -> None:
    """Both numbers an operator has to act on travel in the message."""
    violation = policy.check_pass_spend(8_000, _status(10_000, 5_000))

    assert violation is not None
    assert "8000" in violation
    assert "5000" in violation


def test_check_pass_spend_admits_a_forecast_that_exactly_fits() -> None:
    """The boundary is inclusive: spending the last token of a budget is authorized.

    Without this the gate would refuse a pass it has the money for, and the control
    that a refuse-everything implementation must fail.
    """
    assert policy.check_pass_spend(5_000, _status(10_000, 5_000)) is None


def test_check_pass_spend_admits_when_no_ceiling_applies() -> None:
    """An ungranted session is already human-driven; there is no budget to overrun."""
    ungranted = policy.SpendStatus(grant=None, spent_tokens=0, halted=False)
    assert policy.check_pass_spend(10_000_000, ungranted) is None

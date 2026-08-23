"""Tests for the gate & checkpoint policy engine (onb.3)."""

from __future__ import annotations

import ast
import contextlib
import inspect
import json
import textwrap
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from basicly import decisions, integrity, policy, rubrics, run_record, tracker, verify
from basicly.config import (
    ENGINE_GATE_PROVIDERS,
    LOOP_PHASES,
    RUBRIC_GATE_PROVIDER,
    VERIFY_GATE_PROVIDER,
    PolicyConfig,
    SizingConfig,
)
from tests import fake_tracker


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
        issue_type: str = "",
        gates: list[dict] | None = None,
        acceptance_criteria: str | None = None,
        description: str | None = None,
        dependents: list[dict] | None = None,
        status: str = "open",
        records: dict[str, dict] | None = None,
        gates_by_issue: dict[str, list[dict]] | None = None,
    ):
        self.issue_type = issue_type
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
        # Which issue each written comment landed on, keyed by position like the
        # stamps. A comment a test seeds directly has no owner and every issue reads
        # it, which is what keeps the single-issue tests reading their own fixtures;
        # a comment written through `comments add` is visible only on its own issue,
        # so a check that must tell one bead's markers from another's can (the
        # attribution in basicly-qorx writes to two beads in one call).
        self.owners: dict[int, str] = {}

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["show"]:
            if args[1] in self.records:
                return _Proc(json.dumps([self.records[args[1]]]))
            record = {
                "acceptance_criteria": self.acceptance_criteria,
                "description": self.description,
                "dependents": self.dependents,
                "issue_type": self.issue_type,
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
                if self.owners.get(i, args[2]) == args[2]
            ]
            return _Proc(json.dumps(listing))
        if args[:2] == ["comments", "add"]:
            # br comments add <id> <text> — the marker text is the last arg.
            self.comments.append(args[-1])
            self.stamps[len(self.comments) - 1] = self.now
            self.owners[len(self.comments) - 1] = args[2]
            return _Proc("")
        raise AssertionError(f"unexpected br call: {args}")


CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    """Route every module's tracker access at *fake*, sharing one comment list.

    ``decisions`` used to need its own stub here, because approving a checkpoint settles
    the queue item behind it (basicly-jr0l.24) and its alias was a second spawn point;
    leaving it unpatched spawned a real br per approval test, and the settle's
    best-effort suppression swallowed the failure. It no longer has one — its markers go
    through `tracker.add_comment`/`tracker.read_comments` (basicly-s5li), which is the same
    collapse `tracker.read_record` made and the reason the two funnels below are the whole
    installation.
    """
    monkeypatch.setattr(policy, "_write", fake)
    # Installed on the seams every consumer shares (basicly-tcmy.14, basicly-s5li) rather
    # than on each module's alias. This is the reason the seams exist: one stub point
    # instead of eleven readers to keep in step.
    fake_tracker.install(monkeypatch, fake)


def test_definition_of_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """DoR is ready when the record carries every section its work type requires."""
    _install(monkeypatch, _FakeBr(acceptance_criteria="given x then y"))
    assert policy.definition_of_ready(tmp_path, "i").ready is True

    _install(monkeypatch, _FakeBr())
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.ready is False
    assert result.missing == ("## Acceptance Criteria",)


def test_dor_requires_acceptance_criteria_whatever_the_work_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The requirement no per-type template carries (basicly-kjc5.36).

    ``br lint`` derived its required set from the per-type template, and a chore is
    never asked for acceptance criteria — so a chore carrying none used to pass DoR
    vacuously and then meet a required validate gate with nothing to judge.
    """
    _install(monkeypatch, _FakeBr(issue_type="chore", acceptance_criteria=None))
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
            acceptance_criteria=None,
            description="## Acceptance Criteria\n\n- given x then y\n",
        ),
    )
    assert policy.definition_of_ready(tmp_path, "i").ready is True


def test_dor_keeps_other_missing_sections_when_adding_the_requirement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A work type's own template section blocks alongside the AC requirement."""
    _install(monkeypatch, _FakeBr(issue_type="bug", acceptance_criteria=None))
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.missing == ("## Steps to Reproduce", "## Acceptance Criteria")


def test_dor_reads_the_required_sections_from_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Changing what a work type owes is a config edit, never a code change (R3)."""
    (tmp_path / "basicly.toml").write_text(
        '[policy.type_sections]\nbug = ["## Repro"]\n', encoding="utf-8"
    )
    _install(monkeypatch, _FakeBr(issue_type="bug", acceptance_criteria=None))
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.missing == ("## Repro", "## Acceptance Criteria")


def test_dor_structured_acceptance_field_satisfies_the_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-empty structured acceptance_criteria field clears the AC section (basicly-58iu)."""
    _install(
        monkeypatch,
        _FakeBr(acceptance_criteria="the field is set"),
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
        _FakeBr(issue_type="bug", acceptance_criteria="fixed when x"),
    )
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.ready is False
    assert result.missing == ("## Steps to Reproduce",)


def test_dor_empty_or_absent_acceptance_field_still_requires_the_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A blank or absent field does not satisfy the AC section (basicly-58iu)."""
    _install(monkeypatch, _FakeBr(acceptance_criteria="  "))
    assert policy.definition_of_ready(tmp_path, "i").ready is False
    _install(monkeypatch, _FakeBr(acceptance_criteria=None))
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


def test_scaffold_body_emits_scope_although_the_dor_never_requires_it() -> None:
    """Show the section, never block on it — the split that fixes basicly-tuy6.

    An author told only that a scope exists writes one that parses to nothing;
    requiring it instead would refuse most of an existing tracker.
    """
    body = policy.scaffold_body("bug")
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == ["## Steps to Reproduce", "## Acceptance Criteria", "## Scope"]
    assert "## Scope" not in policy.required_sections("bug")


def test_scaffold_body_shows_the_scope_line_format_rather_than_naming_it() -> None:
    """The hint has to carry the literal form; 'declare a scope' is what already failed."""
    assert policy.SCOPE_LINE_EXAMPLE in policy.scaffold_body("task")


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
    """Rework attempts accumulate and reach the cap every caller compares against."""
    _install(monkeypatch, _FakeBr())
    assert policy.rework_attempts(tmp_path, "i", "verify") == 0
    assert policy.rework_charged(tmp_path, "i", "verify") < CONFIG.max_rework

    assert policy.record_rework(tmp_path, "i", "verify") == 1
    assert policy.rework_charged(tmp_path, "i", "verify") < CONFIG.max_rework

    assert policy.record_rework(tmp_path, "i", "verify") == 2
    assert policy.rework_charged(tmp_path, "i", "verify") >= CONFIG.max_rework


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
    assert policy.rework_charged(tmp_path, "i", "verify") >= CONFIG.max_rework

    policy.grant_rework_allowance(tmp_path, "i", "verify")
    assert policy.rework_charged(tmp_path, "i", "verify") < CONFIG.max_rework

    policy.record_rework(tmp_path, "i", "verify")
    assert policy.rework_charged(tmp_path, "i", "verify") >= CONFIG.max_rework


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


def _queued_ship_ask(repo_root: Path, issue_id: str = "i") -> decisions.DecisionItem:
    """The supervisor's ship-checkpoint ask, as `_route_landed_lane` enqueues it."""
    return decisions.enqueue(
        repo_root,
        issue_id,
        "checkpoint",
        f"approve the ship checkpoint for {issue_id}",
    )


def _decision_waits(repo_root: Path, issue_id: str, decision_id: str) -> list[policy.WaitEvent]:
    return [
        event for event in policy.wait_events(repo_root, issue_id) if event.wait_id == decision_id
    ]


def test_a_confirm_code_approval_settles_the_queued_checkpoint_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect: a bead shipped and closed with its own approval ask still pending.

    `approve_checkpoint_guarded` recorded the marker and nothing answered the queue item
    behind it, so only `loop answer` ever cleared one — and the supervisor queues the ask
    on every non-delegated ship. Five such items sat on main after the proof run
    (basicly-jr0l.24).
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_code(monkeypatch, "abc123")
    item = _queued_ship_ask(tmp_path)
    policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)

    ok = policy.approve_checkpoint_guarded(
        tmp_path, "i", "ship", interactive=False, confirm="abc123"
    )

    assert ok.status == "approved"
    settled = decisions.get(tmp_path, item.decision_id)
    assert settled is not None and not settled.pending, "the ask must not outlive its approval"
    assert settled.answered_by == policy.HUMAN_BY, "a relayed code is a human's decision"


def test_a_settled_ask_closes_its_wait_interval_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The wait meter must not double-count: the ask and the checkpoint are two clocks.

    The item's interval is keyed on its decision id and the checkpoint's on its own wait
    id, so both close once. A second approval attempt is idempotent and must not record
    a second close for either.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _pin_code(monkeypatch, "abc123")
    item = _queued_ship_ask(tmp_path)
    policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)
    policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False, confirm="abc123")

    again = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=True)

    assert again.detail == "already approved"
    closes = _decision_waits(tmp_path, "i", item.decision_id)
    assert len(closes) == 1, f"the ask's interval closed {len(closes)} times, not once"


def test_an_already_approved_checkpoint_settles_a_stale_ask_as_the_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The path every historical stale item arrives on, and it decided nothing.

    Attributing this reconciliation to a human or a grant would put a judgment nobody
    made into the audit trail, so it is charged to the engine.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    policy.approve_checkpoint(tmp_path, "i", "ship")
    item = _queued_ship_ask(tmp_path)

    result = policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=False)

    assert result.detail == "already approved"
    settled = decisions.get(tmp_path, item.decision_id)
    assert settled is not None and not settled.pending
    assert settled.answered_by == decisions.ENGINE_BY


def test_a_pending_ask_for_another_checkpoint_survives_this_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Settling is scoped to the checkpoint approved, not to the bead.

    Matching on kind alone would clear a classify ask when ship was approved, turning a
    tidy-up into a silent loss of a real question.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    classify_ask = decisions.enqueue(
        tmp_path, "i", "checkpoint", "approve the classify checkpoint for i"
    )
    ship_ask = _queued_ship_ask(tmp_path)

    policy.approve_checkpoint_guarded(tmp_path, "i", "ship", interactive=True)

    still_open = decisions.get(tmp_path, classify_ask.decision_id)
    assert still_open is not None and still_open.pending, "the classify ask was not approved"
    settled = decisions.get(tmp_path, ship_ask.decision_id)
    assert settled is not None and not settled.pending


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


# --- delegated proposals (basicly-u6jq.2) ------------------------------------


def test_a_grant_that_approves_the_checkpoint_may_not_originate_the_proposal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The two gates, told apart at the level that has exactly one of them.

    An L1 grant approves the decompose *checkpoint* and always could; what it may
    not do is originate the child plan that checkpoint approves. Reading the first
    as the second is what let an operator conclude the factory was autonomous at a
    phase whose input nothing produced.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L1")

    approval = policy.approve_checkpoint_guarded(tmp_path, "i", "decompose", interactive=False)
    proposal = policy.proposal_delegated(tmp_path, "i", "children", "i")

    assert approval.status == "approved"
    assert not proposal.allowed
    assert "approves the checkpoint but does not originate" in proposal.reason


def test_l2_originates_both_proposals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """L2 is where D3 starts delegating judgment, so it is where a proposer may run."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100000")

    for kind in ("work_type", "children"):
        verdict = policy.proposal_delegated(tmp_path, "i", kind, "i")
        assert verdict.allowed and verdict.level == "L2"


def test_no_grant_originates_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The safe state, and it says a ledger was consulted rather than staying bare."""
    _install(monkeypatch, _FakeBr())

    verdict = policy.proposal_delegated(tmp_path, "i", "work_type", "root")

    assert not verdict.allowed and "no active grant on root" in verdict.reason


def test_a_spend_halted_grant_originates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D3's one halt predicate reaches the proposer too — a proposal costs real tokens."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100")
    entry = run_record.build_record(
        agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=150
    )
    run_record.record(tmp_path, "root", entry)

    verdict = policy.proposal_delegated(tmp_path, "root", "children", "root")

    assert not verdict.allowed
    assert "150/100 tokens" in verdict.reason


def test_a_grant_originates_nothing_outside_its_own_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same session boundary a delegated approval holds: a caller names the root."""
    fake = _FakeBr(records={"root": {"status": "open", "dependents": []}})
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=100000")

    verdict = policy.proposal_delegated(tmp_path, "stranger", "children", "root")

    assert not verdict.allowed
    assert "not in that session's issue tree" in verdict.reason


def test_an_unknown_proposal_kind_is_loud(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A typo must not read as "this level declines it" — that would be silently inert."""
    _install(monkeypatch, _FakeBr())

    with pytest.raises(ValueError, match="unknown proposal kind"):
        policy.proposal_delegated(tmp_path, "i", "work-type", "i")


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

    assert policy.session_spend(tmp_path, "root").measured_tokens == 150
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
    assert policy.session_spend(tmp_path, "root").measured_tokens == 100


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


def test_a_grant_delegated_ship_settles_the_ask_attributed_to_the_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A delegated approval must clear the ask too, and say the grant did it.

    The supervisor queues the ship ask whenever a grant declines, and a later re-grant
    can approve the same checkpoint — so this path settles items as well, and charging
    them to the human column would overstate the very wait a grant exists to remove
    (basicly-jr0l.24, D11).
    """
    fake = _FakeBr(gates=_VERIFY_GREEN)
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")
    item = _queued_ship_ask(tmp_path, "root")

    result = policy.approve_checkpoint_guarded(tmp_path, "root", "ship", interactive=False)

    assert result.status == "approved"
    settled = decisions.get(tmp_path, item.decision_id)
    assert settled is not None and not settled.pending
    assert settled.answered_by == "grant:L3"


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

    *sibling_status* is the basicly-i1s8 distinction: the same two markers are live
    while root.2 is open and history once closed. *wrinkle* swaps the carrier.
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

    The decline came from a rework escalation on another bead in the session, found
    by hand over several tool calls. The decision is unchanged; the reason is new.
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

    Nothing marks an append-only escalation resolved, so a fixed, shipped, closed
    bead read as a live violation forever. Closing resolves it; open does not.
    """
    _install(monkeypatch, _epic_with_a_wrinkled_sibling(sibling_status="open"))
    live = policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1")
    assert live == ("rework escalation on root.2 (gate verify: 2/2)",)

    _install(monkeypatch, _epic_with_a_wrinkled_sibling(sibling_status="closed"))
    assert policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1") == ()


def test_a_granted_allowance_discounts_the_escalation_like_every_other_consumer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The escalation counts charged rework, not raw markers (basicly-54t8w5).

    Two attempts plus one `--allow-retry` allowance is one charged of two allowed;
    counting raw read `2/2` and degraded L3 to L2 for the whole session.
    """
    fake = _epic_with_a_wrinkled_sibling()
    fake.per_issue["root.2"].append("[harness-policy] rework-allowance gate=verify")
    _install(monkeypatch, fake)

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


def _seed_answered_wrinkle(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch, kind: str, question: str, *, answer: bool
) -> _PerIssueBr:
    """Install the epic fixture with root.2's wrinkle also queued, answered when asked.

    Marker and queue item travel together at every real call site, so seeding only
    the marker cannot tell answered from unanswered (basicly-jr0l.65). *answer* is
    the control: the same fixture with the item left pending.
    """
    marker = (
        f"[harness-policy] needs-input {question}"
        if kind == "needs-input"
        else "[harness-policy] rework gate=verify"
    )
    fake = _epic_with_a_wrinkled_sibling(wrinkle=marker)
    _install(monkeypatch, fake)
    item = decisions.enqueue(repo_root, "root.2", kind, question)
    if answer:
        decisions.answer(repo_root, item.decision_id, "use v2", by="human")
    return fake


@pytest.mark.parametrize(
    ("kind", "question"),
    [
        ("needs-input", "which API version"),
        (policy.REWORK_ESCALATION_KIND, policy.rework_escalation_question("verify")),
    ],
)
def test_an_answered_wrinkle_stops_counting_while_its_bead_stays_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, question: str
) -> None:
    """basicly-jr0l.65, both directions of the one rule, on both marker families.

    Answering is a resolution exactly as closing is, so the marker retires on the
    same rule; the control, the same marker still pending, must still refuse.
    """
    _seed_answered_wrinkle(tmp_path, monkeypatch, kind, question, answer=False)
    live = policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1")
    assert len(live) == 1

    _seed_answered_wrinkle(tmp_path, monkeypatch, kind, question, answer=True)
    assert policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1") == ()


def test_an_answered_wrinkle_lets_the_grant_delegate_the_child_ship(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The consequence: the two merged, verified children ship without asking again."""
    _seed_answered_wrinkle(tmp_path, monkeypatch, "needs-input", "which API version", answer=True)

    result = policy.approve_checkpoint_guarded(
        tmp_path, "root.1", "ship", interactive=False, grant_root="root"
    )

    assert result.status == "approved"
    assert result.detail == "delegated under L3 grant"
    assert policy.checkpoint_approved(tmp_path, "root.1", "ship")


def test_a_fact_that_blocks_again_after_an_answer_counts_as_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-opening is what keeps the discount from being a permanent free pass.

    ``decisions.enqueue`` re-opens an answered question under the next generation,
    so the same fact blocking a second time is a *new*, unanswered item. Keying the
    discount on the answered item alone would retire every later recurrence of that
    fact for the rest of the session.
    """
    _seed_answered_wrinkle(tmp_path, monkeypatch, "needs-input", "which API version", answer=True)
    policy.record_needs_input(tmp_path, "root.2", "which API version")
    decisions.enqueue(tmp_path, "root.2", "needs-input", "which API version")

    live = policy.lights_out_violations(tmp_path, "root", CONFIG, shipping="root.1")

    assert live == ("3 needs-input event(s) recorded on root.2",)


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
    assert policy.session_spend(tmp_path, "root").measured_tokens == 70


def _gating_track() -> _FakeBr:
    """The basicly-jr0l.40 shape: a root that gates work it did not parent.

    ``root`` is blocked by a bead living under another epic (``other.1``, which
    has decomposed) and by a parentless standalone bead — the release-epic
    topology, where none of the track descends from the grant root.
    """
    return _FakeBr(
        records={
            "root": {
                "status": "open",
                "dependents": [],
                "dependencies": [
                    {"id": "other.1", "dependency_type": "blocks"},
                    {"id": "standalone", "dependency_type": "blocks"},
                ],
            },
            "other.1": {
                "status": "open",
                "dependents": [{"id": "other.1.1", "dependency_type": "parent-child"}],
                "dependencies": [],
            },
            "other.1.1": {"status": "open", "dependents": [], "dependencies": []},
            "standalone": {"status": "open", "dependents": [], "dependencies": []},
        }
    )


def test_a_grant_covers_a_track_assembled_from_gating_edges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A grant on a gating root delegates its track's checkpoints (basicly-jr0l.40).

    The release epic gates its work with ``blocks`` edges, so a descent-only
    session walk covered exactly one bead and every checkpoint still demanded a
    confirm relay — the grant, and its token ceiling, metered nothing.
    """
    for issue in ("other.1", "standalone", "other.1.1"):
        # A fresh tracker per bead: the fake keeps one comment list for every
        # issue, so a previous approval's marker would read as "already approved".
        fake = _gating_track()
        _install(monkeypatch, fake)
        fake.comments.append("[harness-policy] grant level=L2 budget=100000")

        result = policy.approve_checkpoint_guarded(
            tmp_path, issue, "classify", interactive=False, grant_root="root"
        )
        assert result.status == "approved", issue
        assert "delegated under L2 grant" in result.detail


def test_a_gating_dependent_stays_outside_the_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only work the root waits *on* is the track; work waiting on the root is not.

    The edge is followed in one direction on purpose — the reverse would widen a
    grant onto everything downstream of the root, which nobody granted over.
    """
    fake = _FakeBr(
        records={
            "root": {
                "status": "open",
                "dependents": [{"id": "downstream", "dependency_type": "blocks"}],
                "dependencies": [],
            },
            "downstream": {"status": "open", "dependents": [], "dependencies": []},
        }
    )
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100000")

    result = policy.approve_checkpoint_guarded(
        tmp_path, "downstream", "classify", interactive=False, grant_root="root"
    )

    assert result.status == "challenge"
    assert "not in that session's issue tree" in result.detail


def test_session_coverage_counts_the_whole_track(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Coverage is the count issuance reports: root, gated beads, and their children."""
    _install(monkeypatch, _gating_track())
    assert policy.session_coverage(tmp_path, "root") == 4

    _install(monkeypatch, _FakeBr())  # no edges at all: the session is one leaf
    assert policy.session_coverage(tmp_path, "root") == 1


def test_gating_spend_counts_toward_the_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The meter widens with the coverage: a gated bead's spend is the grant's spend."""
    _install(monkeypatch, _gating_track())
    entry = run_record.build_record(
        agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=70
    )
    run_record.record(tmp_path, "standalone", entry)
    assert policy.session_spend(tmp_path, "root").measured_tokens == 70


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


# --- ...and the `land anyway` it offers is carried out (basicly-tcmy.6) ---------


def test_land_anyway_is_told_apart_from_the_other_offered_choice() -> None:
    """The question offers two remedies, so recognising one must reject the other.

    A rationale may follow the choice, as on the rework escalation — what must not
    happen is `fix the flake` reading as permission to skip the gate.
    """
    assert policy.answer_lands_anyway("land anyway") is True
    assert policy.answer_lands_anyway("  Land Anyway - the flake is upstream") is True
    assert policy.answer_lands_anyway("land  anyway") is True  # answers are typed by hand
    assert policy.answer_lands_anyway("fix the flake") is False
    assert policy.answer_lands_anyway("do not land anyway") is False
    assert policy.answer_lands_anyway("") is False


def test_the_gate_override_is_spendable_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One answer authorises one landing — a standing answer must not bypass forever."""
    _install(monkeypatch, _FakeBr())
    assert policy.gate_override_spent(tmp_path, "i", "merge") is False

    assert policy.spend_gate_override(tmp_path, "i", "merge") is True
    assert policy.gate_override_spent(tmp_path, "i", "merge") is True
    assert policy.spend_gate_override(tmp_path, "i", "merge") is False


def test_the_gate_override_is_scoped_to_its_gate_and_is_not_a_rework_credit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A third marker on the same bead, so no existing counter may read it as its own."""
    _install(monkeypatch, _FakeBr())
    policy.spend_gate_override(tmp_path, "i", "merge")

    assert policy.gate_override_spent(tmp_path, "i", "verify") is False
    assert policy.unreliable_gate_events(tmp_path, "i", "merge") == 0
    assert policy.rework_attempts(tmp_path, "i", "merge") == 0
    assert policy.rework_allowances(tmp_path, "i", "merge") == 0
    assert policy.rework_charged(tmp_path, "i", "merge") == 0


# --- Is the rework loop converging? (basicly-m4zv.5) -------------------------


def _round(tmp_path: Path, *findings: str, gate: str = "verify") -> policy.Convergence:
    """One rework round on *gate*, reporting *findings*."""
    return policy.record_finding_set(tmp_path, "i", gate, findings)


def test_a_repeated_finding_set_is_stalled_and_counts_its_consecutive_rounds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect: nothing anywhere compared attempt 2's findings against attempt 1's.

    The count is not the measure — these three rounds report two findings each and
    the third has learned exactly what the first did.
    """
    _install(monkeypatch, _FakeBr())

    first = _round(tmp_path, "pytest", "ruff")
    second = _round(tmp_path, "pytest", "ruff")
    third = _round(tmp_path, "pytest", "ruff")

    assert (first.verdict, first.stalled_rounds) == (policy.PROGRESSING, 0)
    assert (second.verdict, second.stalled_rounds) == (policy.STALLED, 1)
    assert (third.verdict, third.stalled_rounds) == (policy.STALLED, 2)


def test_a_grown_finding_set_is_diverging_and_names_what_joined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rework that adds a failure without fixing one is worse than no progress."""
    _install(monkeypatch, _FakeBr())
    _round(tmp_path, "pytest")

    grown = _round(tmp_path, "pytest", "ruff")

    assert grown.verdict == policy.DIVERGING and grown.stalled_rounds == 0
    # Only what joined is named as new; the finding that was already open is not.
    assert "grew to 2" in grown.detail and "ruff joined them" in grown.detail


def test_a_finding_set_that_traded_one_finding_for_another_is_progressing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control on both rules: same size, different members, so neither verdict fires.

    A round that fixed ``ruff`` and broke ``mypy`` did different work — it is not the
    previous round repeated and it is not the previous round plus more, so it keeps
    spending the ordinary bounded cap rather than escalating.
    """
    _install(monkeypatch, _FakeBr())
    _round(tmp_path, "pytest", "ruff")

    traded = _round(tmp_path, "pytest", "mypy")

    assert traded.verdict == policy.PROGRESSING and traded.detail == ""


def test_a_finding_set_that_returns_after_moving_is_not_a_consecutive_stall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A, then B, then A moved twice; only the *previous* round is the comparison."""
    _install(monkeypatch, _FakeBr())
    _round(tmp_path, "pytest")
    _round(tmp_path, "ruff")

    back = _round(tmp_path, "pytest")

    assert back.verdict == policy.PROGRESSING


def test_the_history_is_per_gate_and_never_crosses_a_name_that_extends_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two gates reporting one finding name must not read as one gate repeating itself."""
    _install(monkeypatch, _FakeBr())
    _round(tmp_path, "acceptance", gate="verify")

    assert _round(tmp_path, "acceptance", gate="rubric").verdict == policy.PROGRESSING
    assert _round(tmp_path, "acceptance", gate="verify-full").verdict == policy.PROGRESSING
    # ...while the gate that did repeat itself still reads as stalled.
    assert _round(tmp_path, "acceptance", gate="verify").verdict == policy.STALLED


def test_a_finding_carrying_a_separator_round_trips_out_of_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The comparison is only as good as the round trip, so members are stored as JSON.

    A finding is a check name the repo chose, and a space, a comma or an ``=`` in one
    must not split it into two members or merge two into one.
    """
    _install(monkeypatch, _FakeBr())
    awkward = ("pytest -q tests/a.py", "ruff check, formatted", "gate=x")
    _round(tmp_path, *awkward)

    repeat = _round(tmp_path, *awkward)

    assert repeat.verdict == policy.STALLED
    assert repeat.previous == policy.finding_signature(awkward)


def test_the_stored_finding_set_is_bounded_in_members_and_in_length(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A gate that reports hundreds of failures must not write a comment to match."""
    _install(monkeypatch, _FakeBr())
    flood = [f"check-{n:03d}-{'x' * 400}" for n in range(60)]

    recorded = _round(tmp_path, *flood)

    assert len(recorded.members) == policy.MAX_FINDING_SET_MEMBERS
    assert max(len(m) for m in recorded.members) == policy.MAX_FINDING_MEMBER_CHARS


def test_an_unreadable_finding_set_record_is_dropped_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A corrupt marker costs one round of history, never the rework path itself.

    This runs while a gate is *already* failing, so a second way to fall over here
    would turn a bounded rework attempt into a crashed advance.
    """
    fake = _FakeBr()
    fake.comments.append(f"{policy.FINDING_SET_MARKER} gate=verify verdict=stalled findings=[oops")
    _install(monkeypatch, fake)

    assert _round(tmp_path, "pytest").verdict == policy.PROGRESSING
    assert _round(tmp_path, "pytest").verdict == policy.STALLED


def test_a_finding_set_record_is_no_rework_credit_and_no_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fourth marker on the same bead, so no existing counter may read it as its own."""
    _install(monkeypatch, _FakeBr())
    _round(tmp_path, "pytest")

    assert policy.rework_attempts(tmp_path, "i", "verify") == 0
    assert policy.rework_recorded(tmp_path, "i") == 0
    assert policy.rework_allowances(tmp_path, "i", "verify") == 0
    assert policy.unreliable_gate_events(tmp_path, "i", "verify") == 0


def test_the_signature_is_free_of_the_order_and_the_repetition_a_gate_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two orderings of one finding set are one round, not two."""
    _install(monkeypatch, _FakeBr())
    _round(tmp_path, "ruff", "pytest")

    assert _round(tmp_path, "pytest", "ruff", "pytest", "  ", "").verdict == policy.STALLED


def test_the_finding_set_threshold_warns_once_and_then_stops_the_loop() -> None:
    """One stalled round is a warning; two consecutive rounds are not (the AC).

    A gate reports what it checks, so one repeat may hide a real change the gate
    cannot see. Two in a row cannot.
    """
    members = ("pytest",)
    warned = policy.Convergence(policy.STALLED, members, members, 1)
    stopped = policy.Convergence(policy.STALLED, members, members, 2)

    assert policy.finding_set_escalation(warned) is None
    assert warned.detail  # the warning itself is still available to the caller
    assert "not converging" in (policy.finding_set_escalation(stopped) or "")


def test_a_diverging_round_stops_the_loop_on_its_first_occurrence() -> None:
    """Divergence needs no second round: the previous findings are all still open."""
    diverging = policy.Convergence(policy.DIVERGING, ("pytest", "ruff"), ("pytest",), 0)

    assert "worse, not better" in (policy.finding_set_escalation(diverging) or "")


def test_a_progressing_round_never_stops_the_loop() -> None:
    """The control: the bounded cap stays the only thing that ends a converging loop."""
    assert policy.finding_set_escalation(policy.Convergence(policy.PROGRESSING, (), (), 0)) is None


def test_the_non_convergence_refund_is_spendable_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Forgiving every round would be the jr0l.41 livelock again, under a new name.

    No budget spent means no cap reached, so a node nobody answers would re-derive
    the same verdict forever while looking merely slow. One round is forgiven; after
    that the cap is what ends the loop.
    """
    _install(monkeypatch, _FakeBr())
    _to_cap(tmp_path)

    assert policy.spend_convergence_refund(tmp_path, "i", "verify") is True
    assert policy.rework_charged(tmp_path, "i", "verify") == CONFIG.max_rework - 1

    assert policy.spend_convergence_refund(tmp_path, "i", "verify") is False
    assert policy.rework_charged(tmp_path, "i", "verify") == CONFIG.max_rework - 1


def test_the_refund_is_scoped_to_its_gate_and_told_apart_from_an_answered_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two things authorise an attempt for different reasons, so each keeps its own marker."""
    _install(monkeypatch, _FakeBr())
    policy.spend_convergence_refund(tmp_path, "i", "verify")

    # The other gate is untouched, and the operator's own remedy is still available.
    assert policy.spend_convergence_refund(tmp_path, "i", "merge") is True
    assert policy.grant_rework_allowance(tmp_path, "i", "verify") == 0
    assert policy.rework_allowances(tmp_path, "i", "verify") == 2
    assert policy.rework_attempts(tmp_path, "i", "verify") == 0


# --- A shared-tracker gate belongs to the lane that declared it (basicly-qorx) --

# The gate's own output, captured by running the live ceiling gate against a ceiling
# the record contradicts. Observed, never composed — and the reason that matters here
# rather than as a principle: pytest **elides the middle** of a long assertion repr,
# so the `assert [...] == []` line carries a truncated id and neither signature
# substring, and the full violation only ever appears on the "Left contains one more
# item" line. A composed one-line fixture would have keyed the register on a shape
# the gate never emits (the defect basicly-vkh0.6's test records).
_CEILING_FAILURE = (
    "    def test_probe() -> None:\n"
    ">       assert T._ceiling_violations(T.REPO_ROOT, 72_000) == []\n"
    "E       AssertionError: assert ['basicly-tcm...east 128,000'] == []\n"
    "E         \n"
    "E         Left contains one more item: 'basicly-tcmy.5 completed at an estimate "
    "of 128,000, above working_set_max 72,000; raise it to at least 128,000'\n"
    "E         Use -v to get more diff\n"
)


def test_a_tracker_wide_gate_failure_is_attributed_to_the_lane_that_declared_it() -> None:
    """The reported defect: a sibling's landing failed on tcmy.5's finishing record.

    Every lane in a supervised pass shares one `.beads` through the redirect, so the
    ceiling asserts over tcmy.5's record inside tcmy.6's own landing. The culprit is
    read off the gate's output, which names it.
    """
    found = policy.shared_tracker_gate_failure(_CEILING_FAILURE, "basicly-tcmy.6")

    assert found is not None
    assert found.culprits == ("basicly-tcmy.5",)
    assert "shared tracker" in found.reason


def test_a_lane_named_by_the_gate_itself_still_owns_the_failure() -> None:
    """The control the acceptance criterion asks for: the declaration is still charged.

    tcmy.5 is the lane that widened its own `## Scope`, and its own landing hits the
    same assertion. Forgiving there would make the mechanism a way to launder any
    tracker-wide failure — it must only ever move a charge off a bystander.
    """
    assert policy.shared_tracker_gate_failure(_CEILING_FAILURE, "basicly-tcmy.5") is None


def test_output_the_register_does_not_recognise_is_the_lanes_own_failure() -> None:
    """A whitelist, in the direction that matters: an ordinary red test is not forgiven."""
    assert policy.shared_tracker_gate_failure("E   assert 3 == 4\n", "basicly-tcmy.6") is None
    # Names a sibling, but on a line no signature matches: naming a bead is not by
    # itself evidence that the assertion was tracker-wide.
    assert (
        policy.shared_tracker_gate_failure(
            "E   assert basicly-tcmy.5 in changed\n", "basicly-tcmy.6"
        )
        is None
    )


def test_only_ids_of_the_shared_tracker_are_read_as_culprits() -> None:
    """Prose on the failing line must not be mistaken for a bead id.

    The extraction is safe only because the gate asserts over one tracker: every id
    it can name carries the lane's own prefix. A hyphenated word (or another repo's
    id) on the same line is not a culprit.
    """
    line = (
        "E  AssertionError: assert ['other-tcmy.5 completed at an estimate of 9,000, "
        "above working_set_max 8,000; raise it to at least 16,000 read-capped'] == []\n"
    )
    assert policy.shared_tracker_gate_failure(line, "basicly-tcmy.6") is None


def test_the_attribution_lands_on_the_culprit_and_the_event_on_the_blocked_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both halves of the record, each on the bead it belongs to."""
    fake = _FakeBr()
    _install(monkeypatch, fake)

    events = policy.record_shared_gate_failure(
        tmp_path, "basicly-tcmy.6", "merge", ("basicly-tcmy.5",), "verify full failed on pytest"
    )

    assert events == 1
    assert policy.shared_gate_events(tmp_path, "basicly-tcmy.6", "merge") == 1
    blocked = [c for i, c in enumerate(fake.comments) if fake.owners[i] == "basicly-tcmy.6"]
    culprit = [c for i, c in enumerate(fake.comments) if fake.owners[i] == "basicly-tcmy.5"]
    assert len(blocked) == 1 and blocked[0].startswith(policy.SHARED_GATE_MARKER)
    assert "culprits=basicly-tcmy.5" in blocked[0]
    assert culprit == [f"{policy.GATE_INVALIDATED_MARKER} gate=merge lanes=basicly-tcmy.6"]


def test_the_blocked_lane_is_charged_no_rework_for_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect in one assertion: tcmy.6 was charged 1/2 for a defect in no diff of its."""
    _install(monkeypatch, _FakeBr())
    policy.record_shared_gate_failure(tmp_path, "basicly-tcmy.6", "merge", ("basicly-tcmy.5",))

    assert policy.rework_attempts(tmp_path, "basicly-tcmy.6", "merge") == 0
    assert policy.rework_charged(tmp_path, "basicly-tcmy.6", "merge") == 0
    # Nor is it counted as a flake: the two are cleared by opposite evidence.
    assert policy.unreliable_gate_events(tmp_path, "basicly-tcmy.6", "merge") == 0


def test_the_attribution_is_idempotent_while_the_event_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A landing is retried on every advance: the finding must not bury itself.

    The counts differ deliberately — the culprit's attribution is one finding however
    many landings hit it, while the blocked lane's events are what make a lane that
    keeps being blocked by other lanes visible instead of merely slow.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)

    for _ in range(3):
        policy.record_shared_gate_failure(tmp_path, "basicly-tcmy.6", "merge", ("basicly-tcmy.5",))

    assert policy.shared_gate_events(tmp_path, "basicly-tcmy.6", "merge") == 3
    culprit = [c for i, c in enumerate(fake.comments) if fake.owners[i] == "basicly-tcmy.5"]
    assert len(culprit) == 1


def test_the_shared_gate_event_is_scoped_to_its_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tracker-wide merge gate says nothing about the verify gate."""
    _install(monkeypatch, _FakeBr())
    policy.record_shared_gate_failure(tmp_path, "basicly-tcmy.6", "merge", ("basicly-tcmy.5",))
    assert policy.shared_gate_events(tmp_path, "basicly-tcmy.6", "verify") == 0


def test_all_three_gate_escalations_stay_distinguishable_in_one_queue() -> None:
    """They ride one decision kind, so only the wording tells them apart.

    A third question joins the two: wrong work, untrustworthy result, and another
    lane's record. A driver acting on one must never parse it as either other.
    """
    shared = policy.shared_gate_escalation_question("merge", ("basicly-tcmy.5",))
    unreliable = policy.unreliable_gate_escalation_question("merge")
    rework = policy.rework_escalation_question("merge")

    assert policy.gate_from_shared_gate_escalation(shared) == "merge"
    assert policy.gate_from_shared_gate_escalation(unreliable) is None
    assert policy.gate_from_shared_gate_escalation(rework) is None
    assert policy.gate_from_unreliable_escalation(shared) is None
    assert policy.gate_from_rework_escalation(shared) is None
    # The lane that has to be fixed is named in the question, because the queue
    # carries the wording and nothing else.
    assert "basicly-tcmy.5" in shared


def test_the_shared_gate_question_offers_no_remedy_the_engine_leaves_unimplemented() -> None:
    """basicly-4tjt's defect, not repeated: `land anyway` is not on offer here.

    The lane cannot fix a sibling's record or the constant it fails against, and the
    landing override is spent against the unreliable-gate wording alone — so offering
    it here would put a choice in the queue that nothing carries out.
    """
    shared = policy.shared_gate_escalation_question("merge", ("basicly-tcmy.5",))
    assert "land anyway" not in shared.lower()
    assert policy.answer_lands_anyway(shared) is False


# --- A budget meters the grant, not the session's lifetime (basicly-jr0l.17) ---


def _with_spend(monkeypatch: pytest.MonkeyPatch, total: int) -> None:
    """Pin the session's run-record spend total."""
    monkeypatch.setattr(policy, "session_spend", lambda *_a, **_k: policy.SpendMeter(total, 0, 0))


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
    monkeypatch.setattr(
        policy, "session_spend", lambda *_a, **_k: policy.SpendMeter(30_705_839, 0, 0)
    )
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
    monkeypatch.setattr(policy, "session_spend", lambda *_a, **_k: policy.SpendMeter(0, 0, 0))
    policy.issue_grant_guarded(tmp_path, "root", "L1", 5_000_000, L3_CONFIG, interactive=True)

    assert "baseline=" not in policy._write.comments[-1]  # type: ignore[attr-defined]


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
    for func in (policy.spend_status, policy.session_spend):
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        used = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Name | ast.Attribute)
        }
        leaked = used & banned
        assert not leaked, f"{func.__name__} reads a clock: {leaked}"


# --- An estimate is not a measurement (basicly-jr0l.35) ----------------------
#
# The numbers below are the ones the defect was measured with on a live copilot
# probe (2026-07-29): a dispatch that really consumed 24210 input tokens captured
# 5514 bytes of stdout, which the chars/4 fallback reads as 1378 — 17.6x under.
# Counted at face value that sample tells a 100000-token ceiling it has 98622 left.


def _dispatch(
    tmp_path: Path,
    tokens: int,
    *,
    estimated: bool,
    issue_id: str = "root",
    started: bool = True,
) -> None:
    """Record one dispatch's usage on *issue_id*, executed unless *started* is False."""
    run_record.record(
        tmp_path,
        issue_id,
        run_record.build_record(
            agent="t",
            handoff=False,
            started=started,
            returncode=0 if started else None,
            duration_s=1.0 if started else None,
            command=("t",),
            tokens=tokens,
            estimated=estimated,
        ),
    )


def test_an_unmeterable_dispatch_halts_instead_of_metering_its_estimate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reported defect: a chars/4 floor was counted as if it were spend."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100000")
    _dispatch(tmp_path, 24_210, estimated=False)
    _dispatch(tmp_path, 1_378, estimated=True)

    status = policy.spend_status(tmp_path, "root")

    assert status.halted is True
    assert status.unmetered_dispatches == 1
    # The floor is neither added into the total nor allowed to buy the remainder.
    assert status.spent_tokens == 24_210
    assert status.remaining_tokens == 0
    assert "no measurable usage" in status.detail
    assert "1378 estimated" in status.detail


def test_a_fully_measured_session_meters_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other half: with every sample measured, nothing about the ceiling moves."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100")
    _dispatch(tmp_path, 60, estimated=False)
    _dispatch(tmp_path, 30, estimated=False)

    under = policy.spend_status(tmp_path, "root")
    assert (under.spent_tokens, under.halted, under.unmetered_dispatches) == (90, False, 0)
    assert under.remaining_tokens == 10

    _dispatch(tmp_path, 10, estimated=False)
    at_budget = policy.spend_status(tmp_path, "root")
    assert (at_budget.spent_tokens, at_budget.halted) == (100, True)
    assert "token_budget spent (100/100 tokens" in at_budget.detail


def test_a_legacy_record_with_no_usage_provenance_still_meters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A record predating the ``estimated`` field keeps the behaviour it was written with.

    Every writer since sets the flag whenever tokens are present, so an absent one
    means an older basicly, not an unmeasured dispatch — reading it as unmeasurable
    would halt a session over history nothing can go back and measure.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L2 budget=100000")
    run_record.record(
        tmp_path,
        "root",
        run_record.build_record(
            agent="t", handoff=False, returncode=0, duration_s=1.0, command=("t",), tokens=90
        ),
    )

    status = policy.spend_status(tmp_path, "root")

    assert (status.spent_tokens, status.halted, status.unmetered_dispatches) == (90, False, 0)


def test_without_a_ceiling_an_unmeterable_dispatch_halts_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No grant, and an L1 grant with no budget, have no ceiling for this to protect."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _dispatch(tmp_path, 1_378, estimated=True)

    assert policy.spend_status(tmp_path, "root").halted is False

    fake.comments.append("[harness-policy] grant level=L1")
    assert policy.spend_status(tmp_path, "root").halted is False


def test_re_granting_answers_for_the_dispatches_already_unmetered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The halt is answerable: a new grant baselines the count, and the next one halts again.

    Without the baseline one unmeasurable dispatch would hold every future grant on
    this root halted forever, which is a wedge rather than a ceiling.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _dispatch(tmp_path, 1_378, estimated=True)

    policy.issue_grant_guarded(tmp_path, "root", "L1", 5_000_000, L3_CONFIG, interactive=True)
    assert "unmetered=1" in fake.comments[-1]

    grant = policy.active_grant(tmp_path, "root")
    assert grant is not None and grant.unmetered_at_issue == 1
    assert policy.spend_status(tmp_path, "root").halted is False

    _dispatch(tmp_path, 2_000, estimated=True)
    assert policy.spend_status(tmp_path, "root").halted is True


def test_a_session_with_nothing_unmetered_records_no_unmetered_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A measured session's marker stays exactly as it was before this change."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    _dispatch(tmp_path, 24_210, estimated=False)

    policy.issue_grant_guarded(tmp_path, "root", "L1", 5_000_000, L3_CONFIG, interactive=True)

    assert "unmetered=" not in fake.comments[-1]


# --- A dispatch that never started an agent (basicly-jr0l.64) ----------------
#
# The numbers are the 2026-08-02 basicly-tcmy pass: a lane died in its pre-flight
# tracker read, its captured error estimated at 182 tokens, and the grant halted
# with 16561474 of 60000000 spent — refusing a finished lane that was waiting for
# a slot and leaving 43438526 tokens unspent.


def test_a_dispatch_that_never_started_an_agent_does_not_halt_the_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No process ran, so no spend hides under the floor and the remainder is known."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=60000000")
    _dispatch(tmp_path, 16_561_474, estimated=False)
    _dispatch(tmp_path, 182, estimated=True, started=False)

    status = policy.spend_status(tmp_path, "root")

    assert status.halted is False
    assert status.unmetered_dispatches == 0
    # The floor is still not spend: only the measured half moves the remainder.
    assert status.spent_tokens == 16_561_474
    assert status.remaining_tokens == 43_438_526


def test_the_two_unmeasured_shapes_are_not_one_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The discriminator: identical floors, opposite verdicts, decided by the outcome.

    Collapsing either way fails here — treating an unstarted dispatch as an
    unmeterable run halts the first session, and treating an unmeterable run as
    unstarted stops the second halting, which is basicly-jr0l.35's whole point.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=60000000")
    unstarted = tmp_path / "unstarted"
    ran = tmp_path / "ran"
    for root in (unstarted, ran):
        root.mkdir()
    _dispatch(unstarted, 182, estimated=True, started=False)
    _dispatch(ran, 182, estimated=True, started=True)

    never_ran = policy.spend_status(unstarted, "root")
    unmeterable = policy.spend_status(ran, "root")

    assert (never_ran.halted, never_ran.unmetered_dispatches) == (False, 0)
    assert (unmeterable.halted, unmeterable.unmetered_dispatches) == (True, 1)


def test_the_forward_gate_calls_the_remainder_unknown_not_spent() -> None:
    """A pass refused for an unmeterable dispatch must not read as an exhausted budget."""
    status = policy.SpendStatus(
        grant=policy.Grant(level="L2", token_budget=100_000),
        spent_tokens=0,
        halted=True,
        unmetered_dispatches=1,
    )

    violation = policy.check_pass_spend(50_000, status)

    assert violation is not None
    assert "unknown remainder" in violation
    assert "re-scope" not in violation


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


# --- Declared file scope, checked at the landing (basicly-jr0l.44) -----------


def test_record_scope_violation_writes_one_marker_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The landing is retried per advance, so a per-attempt comment would bury it."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    assert policy.record_scope_violation(tmp_path, "i", ("src/a.py", "src/b.py"))
    assert not policy.record_scope_violation(tmp_path, "i", ("src/a.py", "src/b.py"))
    assert fake.comments == [f"{policy.SCOPE_VIOLATION_MARKER} paths=src/a.py,src/b.py"]


def test_record_scope_violation_names_the_lanes_that_declared_the_ground(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lonely overreach and a live-lane collision are different findings."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    policy.record_scope_violation(tmp_path, "i", ("src/a.py",), ("other-1", "other-2"))
    assert fake.comments == [
        f"{policy.SCOPE_VIOLATION_MARKER} paths=src/a.py collides=other-1,other-2"
    ]


def test_record_scope_violation_records_a_changed_finding_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane that reached further after rework has produced new evidence, not a repeat."""
    fake = _FakeBr()
    _install(monkeypatch, fake)
    policy.record_scope_violation(tmp_path, "i", ("src/a.py",))
    assert policy.record_scope_violation(tmp_path, "i", ("src/a.py", "src/b.py"))
    assert len(fake.comments) == 2


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


# --- Gate types and the pre-flight write ban (basicly-m4zv.6) ----------------
#
# `factory-loop.md` §5.1: the taxonomy and the read-only rule, absorbed there when
# `gates-and-rework-design.md` was deleted on 2026-08-08 — which is the section
# `policy.preflight_gate` already names for the same rule. The write the ban has to refuse
# is a *tracker* write: both incidents behind the rule, a hand-recorded verify gate and an
# approved ship checkpoint, were tracker writes that no command can undo.


def test_the_classified_gates_are_the_engine_s_own_gate_names() -> None:
    """Every key is a name some check really carries — none invented, none missed.

    Three keys are literals in ``policy``: the import contract forbids importing
    :mod:`basicly.verify` (a sibling) or :mod:`basicly.rubrics` (its senior). This
    test keeps them in step with the constants they copy — renaming a gate at its
    own call site fails here rather than leaving it silently unclassified.
    """
    assert set(policy.GATE_TYPE_BY_GATE) == {
        policy.DOR_GATE,
        policy.LINKED_WORKTREE_GATE,
        verify.DEFAULT_GATE,
        rubrics.RUBRIC_GATE,
        rubrics.RUBRIC_JUDGED_GATE,
        integrity.VALIDATE_GATE,
    }


def test_each_gate_type_classifies_at_least_one_real_gate() -> None:
    """A four-way taxonomy that only ever names three types is a three-way one.

    The abort row is the one that goes missing first: an abort gate records no
    verdict, so nothing in a bead's gate list ever shows it.
    """
    assert set(policy.GATE_TYPE_BY_GATE.values()) == {
        policy.PREFLIGHT,
        policy.REVISION,
        policy.ESCALATION,
        policy.ABORT,
    }


def test_the_declared_types_are_the_ones_the_design_decided() -> None:
    """The mapping `factory-loop.md` §5.1 states, asserted rather than left in prose.

    Two halves of the deleted design note landed in that section: the table of existing
    gates, and the split that types the deterministic rubric half pre-flight and the judged
    half escalation. The second has no document of its own — the section delegates the five
    named gates to `policy.GATE_TYPE_BY_GATE`, which is what these assertions read.
    """
    assert policy.gate_type(policy.DOR_GATE) == policy.PREFLIGHT
    assert policy.gate_type(verify.DEFAULT_GATE) == policy.REVISION
    assert policy.gate_type(rubrics.RUBRIC_GATE) == policy.PREFLIGHT
    assert policy.gate_type(rubrics.RUBRIC_JUDGED_GATE) == policy.ESCALATION
    assert policy.gate_type(policy.LINKED_WORKTREE_GATE) == policy.ABORT


def test_a_gate_the_engine_does_not_name_reads_as_a_revision_gate() -> None:
    """A consumer's own ``--gate`` name still gets an answer, and a safe one.

    Never pre-flight: that would promise a read-only check the engine cannot vouch
    for, which is the exact promise the ban exists to keep.
    """
    assert policy.gate_type("consumer-smoke") == policy.REVISION


def test_a_preflight_gate_refuses_a_tracker_write(tmp_path: Path) -> None:
    """The AC: a write attempted while a pre-flight gate runs is refused.

    Both funnels, because ``try_write`` is the soft one — soft about a store that cannot
    answer, never about writing when a gate promised not to. Nothing is stubbed, so a
    guard that failed to refuse would be caught reaching a ledger that is not there.
    """
    with policy.preflight_gate(policy.DOR_GATE):
        for write in (["comments", "add", "i", "text"], ["close", "i"], ["gate", "report", "i"]):
            with pytest.raises(tracker.TrackerWriteRefusedError):
                tracker.try_write(tmp_path, write)
            with pytest.raises(tracker.TrackerWriteRefusedError):
                tracker.write(tmp_path, write)


def test_a_preflight_gate_refuses_a_create(tmp_path: Path) -> None:
    """The third write funnel, and the one that mints an id nothing can hand back."""
    with policy.preflight_gate(policy.DOR_GATE), pytest.raises(tracker.TrackerWriteRefusedError):
        tracker.create_record(
            tmp_path, ["create", "a bead", "-t", "task", "--parent", "i", "--json"]
        )


def test_the_refusal_names_the_gate_and_the_write(tmp_path: Path) -> None:
    """A traceback has to say which gate must not have written, not only what it ran.

    And it must not send the reader to the classifier for a *known* write: the
    remedy for ``comments add`` is to stop calling it, never to file it as a read.
    """
    refused = pytest.raises(tracker.TrackerWriteRefusedError)
    with policy.preflight_gate(policy.DOR_GATE), refused as excinfo:
        tracker.try_write(tmp_path, ["comments", "add", "i", "text"])

    message = str(excinfo.value)
    assert policy.DOR_GATE in message
    assert "comments add" in message
    assert "tracker_usage" not in message


def test_the_refusal_survives_the_engine_s_own_tracker_error_handling(tmp_path: Path) -> None:
    """A guard swallowed by a caller's ``except`` is a guard that does not bind.

    Two dozen call sites wrap a tracker call in ``except RuntimeError, OSError,
    ValueError`` and answer None — ``tracker.read_record`` is one — so a refusal in that
    family would read as "the tracker had nothing to say" at exactly the places a
    leaked write would be hardest to see. The idiom is reproduced here rather than
    asserted against the class hierarchy, which is what the call sites actually do.
    """

    def soft_writer() -> None:
        try:
            tracker.write(tmp_path, ["comments", "add", "i", "text"])
        except RuntimeError, OSError, ValueError:
            return

    with policy.preflight_gate(policy.DOR_GATE), pytest.raises(tracker.TrackerWriteRefusedError):
        soft_writer()


def test_a_preflight_gate_still_permits_a_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control the ban must not fail: a gate that cannot read cannot decide.

    Reads do not pass the guard at all — it is installed on the write funnels — so this
    asserts the consequence rather than the mechanism: a record read inside the section
    returns its answer instead of raising.
    """
    _install(monkeypatch, _FakeBr(acceptance_criteria="given x then y"))
    with policy.preflight_gate(policy.DOR_GATE):
        record = tracker.read_record(tmp_path, "i")
        assert record is not None
        assert tracker.read_comments(tmp_path, "i") == []


def test_a_preflight_gate_refuses_an_unclassified_surface(tmp_path: Path) -> None:
    """Fail-closed: ``READ_SUBCOMMANDS`` is not exhaustive, so unknown is not read.

    A refusal is loud and fixed by classifying the surface — which is the one case
    where the message says so — while a leaked write is silent and, in an append-only
    log, permanent.
    """
    refused = pytest.raises(tracker.TrackerWriteRefusedError)
    with policy.preflight_gate(policy.DOR_GATE), refused as excinfo:
        tracker.try_write(tmp_path, ["frobnicate", "i"])

    assert "tracker_usage" in str(excinfo.value)


def test_the_write_ban_lifts_when_the_gate_exits(tmp_path: Path) -> None:
    """Including when the gate leaves by raising — a refused write must not wedge the process.

    Outside the section the write is attempted for real and fails on the absent store,
    which is a different exception from the refusal: reaching it is the evidence.
    """
    with contextlib.suppress(RuntimeError), policy.preflight_gate(policy.DOR_GATE):
        raise RuntimeError("the gate blew up")

    assert tracker.try_write(tmp_path, ["comments", "add", "i", "text"]) is False


def test_the_write_ban_is_scoped_to_the_gate_s_own_thread(tmp_path: Path) -> None:
    """A supervised pass runs its lanes in a thread pool, each writing its own bead.

    A process-global flag would let one lane's pre-flight gate refuse another
    lane's landing — so the guard is thread-scoped, and this is the assertion that
    says so. The other lane's write fails on the absent store rather than being
    refused, which is what discriminates the two.
    """
    with policy.preflight_gate(policy.DOR_GATE), ThreadPoolExecutor(max_workers=1) as pool:
        other_lane = pool.submit(tracker.try_write, tmp_path, ["comments", "add", "other", "text"])
        assert other_lane.result() is False


def test_preflight_gate_refuses_a_gate_that_is_not_preflight() -> None:
    """Asking for the ban on a revision gate is a category error, not a stricter setting.

    ``verify`` is *supposed* to record its verdict and charge its rework; honouring
    the request silently would break it at the first failure.
    """
    with pytest.raises(ValueError, match=policy.REVISION):
        policy.preflight_gate(verify.DEFAULT_GATE)


def test_definition_of_ready_runs_under_the_write_ban(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The rule binds a real pre-flight gate, not just an available context manager.

    The fake tracker attempts a comment while answering the gate's own record read,
    which is what a check that recorded state instead of blocking entry looks like
    from the inside. It goes through the write funnel, because that is where a real
    gate's write would go and where the guard is installed.
    """
    # The real funnel, bound before `_install` replaces it: the stand-in is what the
    # gate reads through, so a write it made through the stand-in's own seam would
    # never reach the guard this test is about.
    real_write = tracker.write

    class _WritingFake(_FakeBr):
        def __call__(self, repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
            if args[:1] == ["show"]:
                real_write(repo_root, ["comments", "add", "i", "recorded from a gate"])
            return super().__call__(repo_root, args, _check=_check)

    _install(monkeypatch, _WritingFake(acceptance_criteria="given x then y"))

    with pytest.raises(tracker.TrackerWriteRefusedError) as excinfo:
        policy.definition_of_ready(tmp_path, "i")

    # Named, so the failure cannot be confused with br simply being absent, which
    # is the other refusal this call site can raise.
    assert policy.DOR_GATE in str(excinfo.value)


def test_definition_of_ready_still_answers_under_the_ban(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control: the gate's own reads are not what the ban refuses.

    Without this, a ban that refused everything would pass the test above while
    making the Definition-of-Ready unanswerable.
    """
    _install(monkeypatch, _FakeBr(acceptance_criteria="given x then y"))
    assert policy.definition_of_ready(tmp_path, "i").ready is True

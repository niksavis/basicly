"""Tests for the gate & checkpoint policy engine (onb.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import policy, run_record
from basicly.config import PolicyConfig, SizingConfig


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


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
        dependents: list[dict] | None = None,
        status: str = "open",
        records: dict[str, dict] | None = None,
    ):
        self.lint_missing = lint_missing or []
        self.gates = gates or []
        self.acceptance_criteria = acceptance_criteria
        self.dependents = dependents or []
        self.status = status
        self.records = records or {}
        self.comments: list[str] = []

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["lint"]:
            return _Proc(json.dumps({"results": [{"missing": self.lint_missing}]}))
        if args[:1] == ["show"]:
            if args[1] in self.records:
                return _Proc(json.dumps([self.records[args[1]]]))
            record = {
                "acceptance_criteria": self.acceptance_criteria,
                "dependents": self.dependents,
                "status": self.status,
            }
            return _Proc(json.dumps([record]))
        if args[:2] == ["gate", "list"]:
            return _Proc(json.dumps({"results": self.gates}))
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": t} for t in self.comments]))
        if args[:2] == ["comments", "add"]:
            # br comments add <id> <text> — the marker text is the last arg.
            self.comments.append(args[-1])
            return _Proc("")
        raise AssertionError(f"unexpected br call: {args}")


CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(policy, "_run_br", fake)


def test_definition_of_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """DoR is ready only when br lint reports no missing sections."""
    _install(monkeypatch, _FakeBr(lint_missing=[]))
    assert policy.definition_of_ready(tmp_path, "i").ready is True

    _install(monkeypatch, _FakeBr(lint_missing=["## Acceptance Criteria"]))
    result = policy.definition_of_ready(tmp_path, "i")
    assert result.ready is False
    assert result.missing == ("## Acceptance Criteria",)


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


def test_gate_status_advances_when_required_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A passing required gate advances; an advisory gate never blocks."""
    _install(
        monkeypatch,
        _FakeBr(
            gates=[
                {"gate": "verify", "provider": "ci", "passed": True},
                {"gate": "review", "provider": "ai", "passed": False},
            ]
        ),
    )
    status = policy.gate_status(tmp_path, "i", CONFIG)
    assert status.can_advance is True
    assert status.required_passed == ("verify",)
    assert [(v.gate, v.passed) for v in status.advisory] == [("review", False)]


def test_gate_status_blocks_on_failed_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed required gate blocks advancement."""
    _install(monkeypatch, _FakeBr(gates=[{"gate": "verify", "provider": "ci", "passed": False}]))
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
_VERIFY_GREEN = [{"gate": "verify", "provider": "t", "passed": True}]


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

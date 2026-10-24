from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import cli, decisions, policy, tracker
from basicly.config import VERIFY_GATE_PROVIDER, PolicyConfig
from tests import fake_tracker, flipped_tracker
from tests.plan_fixtures import install_kit
from tests.tracker_process_fixture import recorded_review


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeBr:
    def __init__(self, *, gates: bool = True) -> None:
        self.comments: list[str] = []
        self.gates = gates

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": t} for t in self.comments]))
        if args[:2] == ["comments", "add"]:
            self.comments.append(args[-1])
            return _Proc("")
        if args[:1] == ["show"]:
            return _Proc(json.dumps([{"status": "open", "dependents": []}]))
        if args[:1] == ["update"]:
            return _Proc("")
        if args[:2] == ["gate", "list"]:
            results = (
                [{"gate": "verify", "provider": VERIFY_GATE_PROVIDER, "passed": True}]
                if self.gates
                else []
            )
            return _Proc(json.dumps({"results": results}))
        raise AssertionError(f"unexpected br call: {args}")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install_kit(tmp_path)
    monkeypatch.chdir(tmp_path)
    fake = _FakeBr()
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)


def _no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)


def test_checkpoint_non_interactive_records_task_authorization_without_a_challenge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tty(monkeypatch)
    rc = cli.main(["policy", "checkpoint", "basicly-x", "classify", "--approve"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "APPROVED" in captured.out
    assert "CONFIRMATION REQUIRED" not in captured.err


def test_missing_verification_is_refused_instead_of_requesting_an_approval(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tty(monkeypatch)
    fake = _FakeBr(gates=False)
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err and "verify" in err
    assert "CONFIRMATION REQUIRED" not in err
    assert "--confirm" not in err


def test_a_verified_ship_checkpoint_needs_no_additional_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tty(monkeypatch)
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 0
    captured = capsys.readouterr()
    assert "checkpoint ship: APPROVED" in captured.out
    assert not captured.err


class _GrantedBr(_FakeBr):
    def __call__(self, repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["gate", "list"]:
            return _Proc(json.dumps({"results": []}))
        return super().__call__(repo_root, args, _check=_check)


def test_a_legacy_grant_does_not_override_missing_verification(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tty(monkeypatch)
    fake = _FakeBr(gates=False)
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "required gates not green on basicly-x: verify" in err
    assert "--confirm" not in err


def test_no_grant_is_needed_for_a_green_ship_checkpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_tty(monkeypatch)
    assert policy.active_grant(tmp_path, "basicly-x") is None
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 0
    assert "APPROVED" in capsys.readouterr().out
    assert policy.active_grant(tmp_path, "basicly-x") is None


def test_classify_and_decompose_record_the_authorized_phase_without_issuing_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(
        policy, "_new_code", lambda: pytest.fail("task phases need no confirm code")
    )
    for checkpoint in ("classify", "decompose"):
        assert cli.main(["policy", "checkpoint", "basicly-x", checkpoint, "--approve"]) == 0
        assert f"checkpoint {checkpoint}: APPROVED" in capsys.readouterr().out


def test_legacy_grant_command_sets_only_the_explicit_budget_without_a_challenge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_tty(monkeypatch)
    assert (
        cli.main(["policy", "grant", "basicly-x", "--level", "L2", "--token-budget", "5000"]) == 0
    )
    captured = capsys.readouterr()
    assert "no level grant issued" in captured.out
    assert "CONFIRMATION REQUIRED" not in captured.err
    assert policy.active_grant(tmp_path, "basicly-x") is None
    limit = policy.active_token_limit(tmp_path, "basicly-x")
    assert limit is not None and limit.token_budget == 5000


def test_a_valid_legacy_checkpoint_code_remains_usable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_tty(monkeypatch)
    code = policy._issue_confirm_code(tmp_path, "basicly-x", "ship")
    rc = cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve", "--confirm", code])
    assert rc == 0
    assert "APPROVED" in capsys.readouterr().out


def test_optional_budget_round_trips_and_clears_without_permission_levels(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_tty(monkeypatch)
    assert cli.main(["policy", "budget", "root", "--tokens", "5000"]) == 0
    assert "5000 tokens" in capsys.readouterr().out
    assert cli.main(["policy", "budget", "root"]) == 0
    assert "5000 tokens" in capsys.readouterr().out
    assert policy.active_grant(tmp_path, "root") is None
    assert cli.main(["policy", "budget", "root", "--clear"]) == 0
    assert "no explicit limit" in capsys.readouterr().out
    assert policy.active_token_limit(tmp_path, "root") is None


def test_legacy_grant_invocation_never_creates_a_level_grant_for_any_coverage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_tty(monkeypatch)
    request = ["policy", "grant", "root", "--level", "L2", "--token-budget", "5000"]
    for count in (1, 24):
        monkeypatch.setattr(policy, "session_coverage", lambda _r, _i, value=count: value)
        assert cli.main(request) == 0
        assert "no level grant issued" in capsys.readouterr().out
        assert policy.active_grant(tmp_path, "root") is None


def test_historical_grants_remain_readable_without_controlling_permissions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _FakeBr()
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)
    for marker, level in [
        ("[harness-policy] grant level=L1", "L1"),
        ("[harness-policy] grant level=L3 budget=5000", "L3"),
    ]:
        fake.comments.append(marker)
        before = list(fake.comments)
        assert cli.main(["policy", "grant", "root"]) == 0
        out = capsys.readouterr().out
        assert f"historical {level}" in out
        assert "no longer control task authorization" in out
        assert fake.comments == before


def test_no_budget_is_required_for_a_legacy_noninteractive_invocation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_tty(monkeypatch)
    assert cli.main(["policy", "grant", "root", "--level", "L1"]) == 0
    captured = capsys.readouterr()
    assert "no level grant issued" in captured.out
    assert not captured.err
    assert policy.active_grant(tmp_path, "root") is None
    assert policy.active_token_limit(tmp_path, "root") is None


def test_default_autonomy_configuration_requires_no_opt_in_level(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _no_tty(monkeypatch)
    assert cli.main(["policy", "checkpoint", "root", "classify", "--approve"]) == 0
    assert "APPROVED" in capsys.readouterr().out
    assert policy.active_grant(tmp_path, "root") is None
    assert policy.active_token_limit(tmp_path, "root") is None


def test_loop_decisions_and_answer_round_trip(capsys: pytest.CaptureFixture[str]) -> None:
    item = decisions.enqueue(Path(), "basicly-x", "needs-input", "which db?")

    assert cli.main(["loop", "decisions", "basicly-x"]) == 1
    out = capsys.readouterr().out
    assert item.decision_id in out and "which db?" in out

    assert cli.main(["loop", "answer", item.decision_id, "postgres", "--by", "niksa"]) == 0
    capsys.readouterr()
    assert cli.main(["loop", "decisions", "basicly-x"]) == 0
    assert "none pending" in capsys.readouterr().out


def test_loop_answer_refuses_unknown_id(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["loop", "answer", "basicly-x#abcdef", "yes"]) == 1
    assert "refused" in capsys.readouterr().err


_CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def _escalate(gate: str = "merge") -> decisions.DecisionItem:
    for _ in range(_CONFIG.max_rework):
        policy.record_rework(Path(), "basicly-x", gate)
    return decisions.enqueue(
        Path(),
        "basicly-x",
        policy.REWORK_ESCALATION_KIND,
        policy.rework_escalation_question(gate),
    )


def test_answering_a_rework_escalation_with_retry_permits_one_more_attempt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = _escalate()
    assert policy.rework_allowances(Path(), "basicly-x", "merge") == 0

    assert cli.main(["loop", "answer", item.decision_id, "retry", "--by", "niksa"]) == 0
    assert "granted one further attempt on gate 'merge'" in capsys.readouterr().out
    assert policy.rework_allowances(Path(), "basicly-x", "merge") == 1


def test_a_retry_answer_may_carry_a_rationale(capsys: pytest.CaptureFixture[str]) -> None:
    item = _escalate()
    answer = "retry - the gate failed on the br clock defect, not on this lane"
    assert cli.main(["loop", "answer", item.decision_id, answer, "--by", "niksa"]) == 0
    assert "granted one further attempt" in capsys.readouterr().out


def test_answering_with_park_grants_nothing(capsys: pytest.CaptureFixture[str]) -> None:

    item = _escalate()
    assert cli.main(["loop", "answer", item.decision_id, "park", "--by", "niksa"]) == 0
    assert "granted" not in capsys.readouterr().out
    assert policy.rework_allowances(Path(), "basicly-x", "merge") == 0


def test_answering_with_re_dispatch_is_not_read_as_retry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = _escalate()
    assert cli.main(["loop", "answer", item.decision_id, "re-dispatch", "--by", "niksa"]) == 0
    assert "granted" not in capsys.readouterr().out


def test_a_decider_answer_does_not_extend_its_own_rework_budget(
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = _escalate()
    by = f"{decisions.DECIDER_BY_PREFIX}claude"
    assert cli.main(["loop", "answer", item.decision_id, "retry", "--by", by]) == 0
    assert "granted" not in capsys.readouterr().out
    assert policy.rework_allowances(Path(), "basicly-x", "merge") == 0


def test_a_retry_on_a_non_rework_decision_grants_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = decisions.enqueue(Path(), "basicly-x", "needs-input", "retry which db?")
    assert cli.main(["loop", "answer", item.decision_id, "retry", "--by", "niksa"]) == 0
    assert "granted" not in capsys.readouterr().out


def test_policy_rework_allow_retry_is_the_operators_direct_lever(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _escalate("verify")
    assert cli.main(["policy", "rework", "basicly-x", "--gate", "verify", "--allow-retry"]) == 0
    out = capsys.readouterr().out
    assert "Granted one further attempt" in out
    assert "may retry" in out
    assert "forgiven" in out


def _escalate_unreliable(gate: str = "merge") -> decisions.DecisionItem:
    return decisions.enqueue(
        Path(),
        "basicly-x",
        policy.REWORK_ESCALATION_KIND,
        policy.unreliable_gate_escalation_question(gate),
    )


def test_answering_land_anyway_says_what_the_next_landing_will_do(
    capsys: pytest.CaptureFixture[str],
) -> None:

    item = _escalate_unreliable()

    assert cli.main(["loop", "answer", item.decision_id, "land anyway", "--by", "niksa"]) == 0
    assert "will skip gate 'merge', once" in capsys.readouterr().out


def test_answering_fix_the_flake_promises_no_override(capsys: pytest.CaptureFixture[str]) -> None:
    item = _escalate_unreliable()

    assert cli.main(["loop", "answer", item.decision_id, "fix the flake", "--by", "niksa"]) == 0
    assert "skip gate" not in capsys.readouterr().out


def test_a_delegated_land_anyway_is_told_it_authorises_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = _escalate_unreliable()
    by = f"{decisions.DECIDER_BY_PREFIX}claude"

    assert cli.main(["loop", "answer", item.decision_id, "land anyway", "--by", by]) == 0
    out = capsys.readouterr().out
    assert "does not override gate 'merge'" in out
    assert "will skip gate" not in out


def test_land_anyway_on_the_rework_escalation_promises_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:

    item = _escalate()

    assert cli.main(["loop", "answer", item.decision_id, "land anyway", "--by", "niksa"]) == 0
    out = capsys.readouterr().out
    assert "skip gate" not in out
    assert "granted" not in out


def test_policy_rework_refuses_record_and_allow_retry_together(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["policy", "rework", "basicly-x", "--record", "--allow-retry"]) == 1
    assert "opposites" in capsys.readouterr().err


def test_policy_scaffold_prints_the_body_for_the_work_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["policy", "scaffold", "--type", "bug"]) == 0
    captured = capsys.readouterr()
    assert captured.out == policy.scaffold_body("bug")
    assert "## Steps to Reproduce" in captured.out
    assert "## Acceptance Criteria" not in captured.out
    assert "## Scope" in captured.out
    assert "--acceptance" in captured.err
    assert "--requirements" in captured.err


def test_policy_scaffold_rejects_a_type_outside_the_br_taxonomy() -> None:
    with pytest.raises(SystemExit):
        cli.main(["policy", "scaffold", "--type", "nonsense"])


def test_dor_refusal_names_the_scaffold_command_for_the_issues_own_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    bug = {"issue_type": "bug", "description": "## Acceptance Criteria\n\nx"}
    fake_tracker.install(monkeypatch, lambda _root, _args: _Proc(json.dumps([bug])))

    assert cli.main(["policy", "dor", "basicly-x"]) == 1
    err = capsys.readouterr().err
    assert "## Steps to Reproduce" in err
    assert "basicly policy scaffold --type bug" in err


_TRIGGER = "## Trigger\n\nWhen gated, I want a trigger, so I can validate it.\n\n"


def _reviewed(fields: dict[str, str]) -> dict[str, object]:

    repo = Path.cwd()
    flipped_tracker.seed(repo, "basicly-x", **fields)
    commands = tracker.kit(repo, "commands")
    ledger = tracker.ledger_dir(repo)
    recorded_review(commands, ledger, "basicly-x")
    folded = commands.events.fold(commands.events.read_events(ledger)[0])
    return {**folded.records["basicly-x"].fields, "id": "basicly-x"}


def test_dor_warns_about_a_scope_that_parsed_to_nothing_without_changing_the_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    body = _TRIGGER + "## Scope\n\n- src/a.py\n"
    fields = {
        "issue_type": "task",
        "description": body,
        "acceptance_criteria": "- x",
        "requirements": "- r",
    }
    record = _Proc(json.dumps([_reviewed(fields)]))
    fake_tracker.install(monkeypatch, lambda _root, _args: record)

    assert cli.main(["policy", "dor", "basicly-x"]) == 0
    captured = capsys.readouterr()
    assert "DoR: READY" in captured.out
    assert "parsed to no globs" in captured.err


def test_dor_stays_quiet_when_the_scope_parsed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = _TRIGGER + f"## Scope\n\n{policy.SCOPE_LINE_EXAMPLE}\n"
    fields = {
        "issue_type": "task",
        "description": body,
        "acceptance_criteria": "- x",
        "requirements": "- r",
    }
    record = _Proc(json.dumps([_reviewed(fields)]))
    fake_tracker.install(monkeypatch, lambda _root, _args: record)

    assert cli.main(["policy", "dor", "basicly-x"]) == 0
    assert "parsed to no globs" not in capsys.readouterr().err


def test_dor_refusal_still_offers_the_scaffold_when_the_type_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_tracker.install(monkeypatch, lambda _root, _args: _Proc(""))

    assert cli.main(["policy", "dor", "basicly-x"]) == 1
    assert "basicly policy scaffold --type <work-type>" in capsys.readouterr().err

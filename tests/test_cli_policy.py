from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import cli, decisions, policy
from basicly.config import PolicyConfig
from tests import fake_tracker


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeBr:
    def __init__(self) -> None:
        self.comments: list[str] = []

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
        raise AssertionError(f"unexpected br call: {args}")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    fake = _FakeBr()
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)


def _no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)


def test_checkpoint_approve_non_interactive_challenges(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    rc = cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "CONFIRMATION REQUIRED" in err
    assert "--confirm cafe1234" in err


def test_challenge_says_the_caller_may_run_it_once_a_human_approves(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1
    err = capsys.readouterr().err
    assert "A human must approve this decision" in err
    assert "may run the command themselves" in err
    assert "get an explicit yes" in err
    assert f"expires in {policy.CONFIRM_TTL_SECONDS // 60} minutes" in err
    assert "must re-run" not in err


def test_ship_challenge_says_the_merge_already_happened_and_nothing_is_published(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1
    err = capsys.readouterr().err
    assert "ALREADY happened" in err
    assert "build->verify landing" in err
    assert "tears down the worktree and closes the bead" in err
    assert "publishes nothing" in err
    assert "no tag or release" in err
    assert "'[merged]'" in err
    assert "no un-approve" in err


class _GrantedBr(_FakeBr):
    def __call__(self, repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["gate", "list"]:
            return _Proc(json.dumps({"results": []}))
        return super().__call__(repo_root, args, _check=_check)


def test_ship_challenge_names_the_precondition_the_grant_declined_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    fake = _GrantedBr()
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")

    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1

    err = capsys.readouterr().err
    assert "CONFIRMATION REQUIRED" in err
    assert "the active L3 grant covers ship but declined it" in err
    assert "required gates not green on basicly-x: verify" in err
    assert "--confirm cafe1234" in err


def test_a_challenge_with_no_grant_prints_no_reason_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")

    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1

    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == "checkpoint ship: CONFIRMATION REQUIRED (basicly-x)"
    assert lines[1].startswith("  The merge to the base branch has ALREADY happened")


def test_classify_and_decompose_challenges_state_their_own_effect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    assert cli.main(["policy", "checkpoint", "basicly-x", "classify", "--approve"]) == 1
    err = capsys.readouterr().err
    assert "provisions a worktree" in err
    assert "No code changes yet" in err

    assert cli.main(["policy", "checkpoint", "basicly-x", "decompose", "--approve"]) == 1
    err = capsys.readouterr().err
    assert "fans out the child beads" in err
    assert "Nothing merges" in err


def test_grant_challenge_carries_no_checkpoint_meaning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _no_tty(monkeypatch)
    _allow_autonomy(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    grant = ["policy", "grant", "basicly-x", "--level", "L2", "--token-budget", "5000"]
    assert cli.main(grant) == 1
    err = capsys.readouterr().err
    assert "grant: CONFIRMATION REQUIRED" in err
    assert "A human must approve this decision" in err
    assert "ALREADY happened" not in err
    assert "publishes nothing" not in err


def test_checkpoint_approve_with_valid_code_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1
    capsys.readouterr()
    rc = cli.main([
        "policy",
        "checkpoint",
        "basicly-x",
        "ship",
        "--approve",
        "--confirm",
        "cafe1234",
    ])
    assert rc == 0
    assert "APPROVED" in capsys.readouterr().out


def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)


def _allow_autonomy(monkeypatch: pytest.MonkeyPatch, level: str = "L3") -> None:
    config = PolicyConfig(required_gates=("verify",), max_rework=2, autonomy=level)
    monkeypatch.setattr(cli, "load_policy_config", lambda _r: config)


def test_grant_issue_interactive_then_show_and_revoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tty(monkeypatch)
    _allow_autonomy(monkeypatch)
    assert cli.main(["policy", "grant", "root", "--level", "L2", "--token-budget", "5000"]) == 0
    assert "ISSUED L2" in capsys.readouterr().out

    assert cli.main(["policy", "grant", "root"]) == 0
    out = capsys.readouterr().out
    assert "grant: L2" in out and "token budget 5000" in out

    assert cli.main(["policy", "grant", "root", "--revoke"]) == 0
    capsys.readouterr()
    assert cli.main(["policy", "grant", "root"]) == 1
    assert "grant: NONE" in capsys.readouterr().out


def test_grant_issuance_states_how_many_beads_it_covers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _tty(monkeypatch)
    _allow_autonomy(monkeypatch)
    grant = ["policy", "grant", "root", "--level", "L2", "--token-budget", "5000"]

    assert cli.main(grant) == 0
    assert "covers 1 bead (this issue only)" in capsys.readouterr().out

    monkeypatch.setattr(policy, "session_coverage", lambda _r, _i: 24)
    assert cli.main(grant) == 0
    assert "covers 24 beads" in capsys.readouterr().out

    assert cli.main(["policy", "grant", "root"]) == 0
    assert "covers 24 beads" in capsys.readouterr().out


def test_the_ledger_tells_approving_a_checkpoint_from_originating_a_proposal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    _tty(monkeypatch)
    _allow_autonomy(monkeypatch, "L3")

    assert cli.main(["policy", "grant", "root", "--level", "L1"]) == 0
    capsys.readouterr()
    assert cli.main(["policy", "grant", "root"]) == 0
    out = capsys.readouterr().out
    assert "approves checkpoints: decompose" in out
    assert "originates proposals: (nothing)" in out

    assert cli.main(["policy", "grant", "root", "--level", "L3", "--token-budget", "5000"]) == 0
    capsys.readouterr()
    assert cli.main(["policy", "grant", "root"]) == 0
    out = capsys.readouterr().out
    assert "approves checkpoints: classify, decompose, ship" in out
    assert "originates proposals: work_type, children" in out


def test_grant_issue_non_interactive_challenges(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_tty(monkeypatch)
    _allow_autonomy(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "feed5678")
    rc = cli.main(["policy", "grant", "root", "--level", "L1"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "CONFIRMATION REQUIRED" in err
    assert "--confirm feed5678" in err
    assert "may run the command themselves" in err


def test_grant_issue_refused_at_default_ceiling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _tty(monkeypatch)
    rc = cli.main(["policy", "grant", "root", "--level", "L1"])
    assert rc == 1
    assert "autonomy ceiling" in capsys.readouterr().err


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
    out = capsys.readouterr().out
    assert out == policy.scaffold_body("bug")
    assert "## Steps to Reproduce" in out and "## Acceptance Criteria" in out
    assert "## Scope" in out


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


def test_dor_warns_about_a_scope_that_parsed_to_nothing_without_changing_the_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    body = _TRIGGER + "## Acceptance Criteria\n\n- x\n\n## Scope\n\n- src/a.py\n"
    record = _Proc(json.dumps([{"issue_type": "task", "description": body}]))
    fake_tracker.install(monkeypatch, lambda _root, _args: record)

    assert cli.main(["policy", "dor", "basicly-x"]) == 0
    captured = capsys.readouterr()
    assert "DoR: READY" in captured.out
    assert "parsed to no globs" in captured.err


def test_dor_stays_quiet_when_the_scope_parsed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = _TRIGGER + f"## Acceptance Criteria\n\n- x\n\n## Scope\n\n{policy.SCOPE_LINE_EXAMPLE}\n"
    record = _Proc(json.dumps([{"issue_type": "task", "description": body}]))
    fake_tracker.install(monkeypatch, lambda _root, _args: record)

    assert cli.main(["policy", "dor", "basicly-x"]) == 0
    assert "parsed to no globs" not in capsys.readouterr().err


def test_dor_refusal_still_offers_the_scaffold_when_the_type_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_tracker.install(monkeypatch, lambda _root, _args: _Proc(""))

    assert cli.main(["policy", "dor", "basicly-x"]) == 1
    assert "basicly policy scaffold --type <work-type>" in capsys.readouterr().err

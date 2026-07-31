"""Tests for the ``basicly policy checkpoint`` CLI wiring (basicly-shgo).

The command gates ``--approve`` on an interactive TTY or a one-time confirm
code. These tests fake the tracker and stdin so they assert only that wiring:
a non-interactive approve challenges (exit 1) and a matching code approves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import cli, decisions, loop_state, policy
from basicly.config import PolicyConfig


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeBr:
    """Stateful br stand-in whose comment writes are visible to later reads."""

    def __init__(self) -> None:
        self.comments: list[str] = []

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": t} for t in self.comments]))
        if args[:2] == ["comments", "add"]:
            self.comments.append(args[-1])
            return _Proc("")
        if args[:1] == ["show"]:
            # An open, childless session root — enough for active_grant's
            # expiry check and the grant-approval session walk.
            return _Proc(json.dumps([{"status": "open", "dependents": []}]))
        raise AssertionError(f"unexpected br call: {args}")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(policy, "_run_br", _FakeBr())


def _no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)


def test_checkpoint_approve_non_interactive_challenges(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a TTY and without a code, approve refuses and prints a re-run line."""
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
    """The challenge must not read as "hand this over and wait" (basicly-kjc5.34).

    The gate forces a human *decision*; it never cared whose fingers type the
    command. The old wording said "a human must re-run with the one-time code",
    so an agent handed the command over and waited — a wasted round trip that
    raced the code's TTL, and a ship code did expire mid-ask.
    """
    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1
    err = capsys.readouterr().err
    assert "A human must approve this decision" in err
    assert "may run the command themselves" in err
    # The protocol, so an agent knows what "approval" has to look like.
    assert "get an explicit yes" in err
    # The deadline, since queueing the ask behind other work is how a code expires.
    assert f"expires in {policy.CONFIRM_TTL_SECONDS // 60} minutes" in err
    # The regression pin: the phrasing that caused the hand-off is gone.
    assert "must re-run" not in err


def test_ship_challenge_says_the_merge_already_happened_and_nothing_is_published(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ship prompt must state what approving does and does not do (basicly-jr0l.39).

    The name reads as *release* or *publish*, and it sits after the merge it
    sounds like it performs — the owner of this harness misread it off a live
    prompt, and a consumer has strictly less context. The name is not changed
    (that is deferred to basicly-kjc5.45); the prompt has to carry the meaning,
    because the approval protocol asks the driver to say what approving does and
    a bare phase name cannot answer that.
    """
    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1
    err = capsys.readouterr().err
    # The merge is past, not what this approval performs.
    assert "ALREADY happened" in err
    assert "build->verify landing" in err
    # What it does, and the three things it does not do.
    assert "tears down the worktree and closes the bead" in err
    assert "publishes nothing" in err
    assert "no tag or release" in err
    # The recorded incident: approving before `[merged]` wedges an unmerged node.
    assert "'[merged]'" in err
    assert "no un-approve" in err


class _GrantedBr(_FakeBr):
    """The base fake plus ``gate list``, so a real grant decision can be reached."""

    def __call__(self, repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["gate", "list"]:
            return _Proc(json.dumps({"results": []}))  # verify missing: a real wrinkle
        return super().__call__(repo_root, args, _check=_check)


def test_ship_challenge_names_the_precondition_the_grant_declined_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A grant that covered ship and declined must say so on the operator's surface.

    The measured incident (basicly-5ltn): the operator saw only CONFIRMATION
    REQUIRED, with nothing to distinguish "no grant" from "a covering grant
    refused because a lights-out precondition is violated".
    """
    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    fake = _GrantedBr()
    monkeypatch.setattr(policy, "_run_br", fake)
    fake.comments.append("[harness-policy] grant level=L3 budget=1000000")

    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1

    err = capsys.readouterr().err
    assert "CONFIRMATION REQUIRED" in err
    assert "the active L3 grant covers ship but declined it" in err
    assert "required gates not green on basicly-x: verify" in err
    # The code still has to come back: the message is new, the gate is not.
    assert "--confirm cafe1234" in err


def test_a_challenge_with_no_grant_prints_no_reason_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given no grant the output is unchanged: nothing new between header and meaning."""
    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")

    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1

    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == "checkpoint ship: CONFIRMATION REQUIRED (basicly-x)"
    assert lines[1].startswith("  The merge to the base branch has ALREADY happened")


def test_classify_and_decompose_challenges_state_their_own_effect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every named checkpoint says what approving it does, not just ship.

    Same defect class: "CONFIRMATION REQUIRED" plus a phase name leaves the
    driver unable to satisfy the protocol's "say what approving it does".
    """
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
    """The grant challenge keeps the generic block: it approves no checkpoint.

    It is the one caller with no checkpoint name to look up, so the lookup must
    degrade to nothing rather than mislabel what is being approved.
    """
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
    """Re-running with the issued code records approval and exits 0."""
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


# --- basicly policy grant (basicly-kjc5.3, design D3) --------------------------


def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)


def _allow_autonomy(monkeypatch: pytest.MonkeyPatch, level: str = "L3") -> None:
    config = PolicyConfig(required_gates=("verify",), max_rework=2, autonomy=level)
    monkeypatch.setattr(cli, "load_policy_config", lambda _r: config)


def test_grant_issue_interactive_then_show_and_revoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A TTY caller issues under the ceiling; show reports it; revoke clears it."""
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


def test_grant_issue_non_interactive_challenges(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An agent without a TTY cannot self-issue: it gets a relay code and exit 1."""
    _no_tty(monkeypatch)
    _allow_autonomy(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "feed5678")
    rc = cli.main(["policy", "grant", "root", "--level", "L1"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "CONFIRMATION REQUIRED" in err
    assert "--confirm feed5678" in err
    # Grant issuance shares the challenge wording with checkpoint approval.
    assert "may run the command themselves" in err


def test_grant_issue_refused_at_default_ceiling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With the default [policy] autonomy = L0 every issuance is refused."""
    _tty(monkeypatch)
    rc = cli.main(["policy", "grant", "root", "--level", "L1"])
    assert rc == 1
    assert "autonomy ceiling" in capsys.readouterr().err


# --- basicly loop decisions / answer (basicly-kjc5.4) ---------------------------


def _install_decisions_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reuse the fixture-installed checkpoint fake: it serves comments and show.
    monkeypatch.setattr(decisions, "_run_br", policy._run_br)
    monkeypatch.setattr(loop_state, "_run_br", policy._run_br)


def test_loop_decisions_and_answer_round_trip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An enqueued item is listed, answerable with attribution, then gone."""
    _install_decisions_fake(monkeypatch)
    item = decisions.enqueue(Path(), "basicly-x", "needs-input", "which db?")

    assert cli.main(["loop", "decisions", "basicly-x"]) == 1
    out = capsys.readouterr().out
    assert item.decision_id in out and "which db?" in out

    assert cli.main(["loop", "answer", item.decision_id, "postgres", "--by", "niksa"]) == 0
    capsys.readouterr()
    assert cli.main(["loop", "decisions", "basicly-x"]) == 0
    assert "none pending" in capsys.readouterr().out


def test_loop_answer_refuses_unknown_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Answering a decision that was never asked is an error, not a silent write."""
    _install_decisions_fake(monkeypatch)
    assert cli.main(["loop", "answer", "basicly-x#abcdef", "yes"]) == 1
    assert "refused" in capsys.readouterr().err


# --- An answered `retry` is carried out, not just recorded (basicly-4tjt) -----


_CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


def _escalate(gate: str = "merge") -> decisions.DecisionItem:
    """Spend the budget on *gate* and enqueue the escalation the loop would."""
    for _ in range(_CONFIG.max_rework):
        policy.record_rework(Path(), "basicly-x", gate)
    return decisions.enqueue(
        Path(),
        "basicly-x",
        policy.REWORK_ESCALATION_KIND,
        policy.rework_escalation_question(gate),
    )


def test_answering_a_rework_escalation_with_retry_permits_one_more_attempt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reported defect: the answer was recorded and the lane still could not move."""
    _install_decisions_fake(monkeypatch)
    item = _escalate()
    assert policy.should_escalate(Path(), "basicly-x", "merge", _CONFIG) is True

    assert cli.main(["loop", "answer", item.decision_id, "retry", "--by", "niksa"]) == 0
    assert "granted one further attempt on gate 'merge'" in capsys.readouterr().out
    assert policy.should_escalate(Path(), "basicly-x", "merge", _CONFIG) is False


def test_a_retry_answer_may_carry_a_rationale(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Operators explain themselves; the leading token is what decides."""
    _install_decisions_fake(monkeypatch)
    item = _escalate()
    answer = "retry - the gate failed on the br clock defect, not on this lane"
    assert cli.main(["loop", "answer", item.decision_id, answer, "--by", "niksa"]) == 0
    assert "granted one further attempt" in capsys.readouterr().out


def test_answering_with_park_grants_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only one of the three offered choices extends the budget."""
    _install_decisions_fake(monkeypatch)
    item = _escalate()
    assert cli.main(["loop", "answer", item.decision_id, "park", "--by", "niksa"]) == 0
    assert "granted" not in capsys.readouterr().out
    assert policy.should_escalate(Path(), "basicly-x", "merge", _CONFIG) is True


def test_answering_with_re_dispatch_is_not_read_as_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`re-dispatch` shares a prefix with nothing, but the guard must be explicit."""
    _install_decisions_fake(monkeypatch)
    item = _escalate()
    assert cli.main(["loop", "answer", item.decision_id, "re-dispatch", "--by", "niksa"]) == 0
    assert "granted" not in capsys.readouterr().out


def test_a_decider_answer_does_not_extend_its_own_rework_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An autonomy grant may dispose of the question; the engine still holds the budget."""
    _install_decisions_fake(monkeypatch)
    item = _escalate()
    by = f"{decisions.DECIDER_BY_PREFIX}claude"
    assert cli.main(["loop", "answer", item.decision_id, "retry", "--by", by]) == 0
    assert "granted" not in capsys.readouterr().out
    assert policy.should_escalate(Path(), "basicly-x", "merge", _CONFIG) is True


def test_a_retry_on_a_non_rework_decision_grants_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only a rework escalation carries a gate to forgive."""
    _install_decisions_fake(monkeypatch)
    item = decisions.enqueue(Path(), "basicly-x", "needs-input", "retry which db?")
    assert cli.main(["loop", "answer", item.decision_id, "retry", "--by", "niksa"]) == 0
    assert "granted" not in capsys.readouterr().out


def test_policy_rework_allow_retry_is_the_operators_direct_lever(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The escalation is answerable out of band too, without touching max_rework."""
    _install_decisions_fake(monkeypatch)
    _escalate("verify")
    assert cli.main(["policy", "rework", "basicly-x", "--gate", "verify", "--allow-retry"]) == 0
    out = capsys.readouterr().out
    assert "Granted one further attempt" in out
    assert "may retry" in out
    assert "forgiven" in out


def test_policy_rework_refuses_record_and_allow_retry_together(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Charging and forgiving in one call is a contradiction, not a no-op."""
    _install_decisions_fake(monkeypatch)
    assert cli.main(["policy", "rework", "basicly-x", "--record", "--allow-retry"]) == 1
    assert "opposites" in capsys.readouterr().err


# --- policy scaffold / the DoR refusal's own remedy (basicly-kjc5.44) --------


def test_policy_scaffold_prints_the_body_for_the_work_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command emits the DoR structure on stdout, ready to pipe into br create."""
    assert cli.main(["policy", "scaffold", "--type", "bug"]) == 0
    out = capsys.readouterr().out
    assert out == policy.compose_body("bug")
    assert "## Steps to Reproduce" in out and "## Acceptance Criteria" in out


def test_policy_scaffold_rejects_a_type_outside_the_br_taxonomy() -> None:
    """An unknown type is a parser error, not a body missing its template sections."""
    with pytest.raises(SystemExit):
        cli.main(["policy", "scaffold", "--type", "nonsense"])


def test_dor_refusal_names_the_scaffold_command_for_the_issues_own_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refusal must hand back the fix, typed for the bead (basicly-kjc5.44).

    Learning the required sections by being refused cost a read, an edit and a
    re-check twice in one run; the refusal now prints the command that emits them.
    """
    monkeypatch.setattr(
        policy,
        "_run_br",
        lambda _root, args, **_kw: _Proc(
            json.dumps({"results": [{"missing": ["## Steps to Reproduce"]}]})
            if args[:1] == ["lint"]
            else json.dumps([{"description": "## Acceptance Criteria\n\nx"}])
        ),
    )
    bug = _Proc(json.dumps([{"type": "bug"}]))
    monkeypatch.setattr(cli.br, "try_run_br", lambda _root, _args: bug)

    assert cli.main(["policy", "dor", "basicly-x"]) == 1
    err = capsys.readouterr().err
    assert "## Steps to Reproduce" in err
    assert "basicly policy scaffold --type bug" in err


def test_dor_refusal_still_offers_the_scaffold_when_the_type_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tracker read that fails must not swallow the remedy — it degrades to a placeholder."""
    monkeypatch.setattr(
        policy,
        "_run_br",
        lambda _root, args, **_kw: _Proc(
            json.dumps({"results": [{"missing": []}]}) if args[:1] == ["lint"] else json.dumps([{}])
        ),
    )
    monkeypatch.setattr(cli.br, "try_run_br", lambda _root, _args: None)

    assert cli.main(["policy", "dor", "basicly-x"]) == 1
    assert "basicly policy scaffold --type <work-type>" in capsys.readouterr().err

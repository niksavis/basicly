from __future__ import annotations

from pathlib import Path

import pytest

from basicly import decompose, policy, release, run_record, tracker
from basicly.config import VERIFY_GATE_PROVIDER, PolicyConfig
from tests import flipped_tracker
from tests.kit_deployment_helpers import CLOCK, LEDGER_RELATIVE, events, git, git_env, make_host

RECORD = "basicly-requested"


@pytest.fixture
def task_repo(tmp_path: Path) -> Path:
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, RECORD, title="requested task", issue_type="task")
    return repo


def _verify(repo: Path, *, passed: bool = True, provider: str = VERIFY_GATE_PROVIDER) -> None:
    kit = tracker.kit(repo)
    gates = tracker.kit(repo, "gates")
    kit.events.append(
        tracker.ledger_dir(repo),
        [gates.gate_draft(RECORD, "verify", provider=provider, passed=passed)],
    )


def _lines(repo: Path) -> set[bytes]:
    return {
        line
        for pattern in ("events-*.jsonl", "pending-*.jsonl")
        for path in tracker.ledger_dir(repo).glob(pattern)
        for line in path.read_bytes().splitlines()
        if line.strip()
    }


def _spend(repo: Path, tokens: int) -> None:
    run_record.record(
        repo,
        RECORD,
        run_record.build_record(
            agent="fixture",
            handoff=False,
            returncode=0,
            duration_s=1.0,
            command=("fixture",),
            tokens=tokens,
        ),
    )


@pytest.mark.parametrize("checkpoint", ["classify", "decompose", "ship"])
def test_requested_task_advances_without_a_grant_budget_or_confirm_code(
    task_repo: Path, checkpoint: str
) -> None:
    _verify(task_repo)
    result = policy.approve_checkpoint_guarded(task_repo, RECORD, checkpoint, interactive=False)

    assert result.status == "approved", result.detail
    assert result.code is None
    assert policy.checkpoint_approved(task_repo, RECORD, checkpoint)
    assert policy.active_grant(task_repo, RECORD) is None
    assert all(
        "grant level=" not in row["text"] for row in tracker.read_comments(task_repo, RECORD)
    )


@pytest.mark.parametrize("state", ["missing", "failed", "foreign"])
def test_task_authorization_does_not_replace_required_engine_verification(
    task_repo: Path, state: str
) -> None:
    if state != "missing":
        _verify(
            task_repo,
            passed=state != "failed",
            provider="fixture" if state == "foreign" else VERIFY_GATE_PROVIDER,
        )
    result = policy.approve_checkpoint_guarded(task_repo, RECORD, "ship", interactive=False)

    assert result.status == "rejected"
    assert "verify" in result.detail
    assert not policy.checkpoint_approved(task_repo, RECORD, "ship")


def test_a_phase_cannot_authorize_a_record_outside_the_requested_task(task_repo: Path) -> None:
    flipped_tracker.seed(task_repo, "basicly-other", title="unrelated task")
    result = policy.approve_checkpoint_guarded(
        task_repo, "basicly-other", "classify", interactive=False, grant_root=RECORD
    )

    assert result.status == "rejected"
    assert "basicly-other" in result.detail
    assert not policy.checkpoint_approved(task_repo, "basicly-other", "classify")


@pytest.mark.parametrize("blocker", ["fact", "rework"])
def test_ship_still_refuses_an_unresolved_fact_or_rework_escalation(
    task_repo: Path, blocker: str
) -> None:
    _verify(task_repo)
    if blocker == "fact":
        policy.record_needs_input(task_repo, RECORD, "The required deployment target is unknown")
    else:
        policy.record_rework(task_repo, RECORD, "verify")
        policy.record_rework(task_repo, RECORD, "verify")
        assert policy.rework_charged(task_repo, RECORD, "verify") == 2
    result = policy.approve_checkpoint_guarded(task_repo, RECORD, "ship", interactive=False)

    assert result.status == "rejected"
    assert not policy.checkpoint_approved(task_repo, RECORD, "ship")


def test_each_repair_attempt_and_explicit_retry_is_counted(task_repo: Path) -> None:
    assert policy.record_rework(task_repo, RECORD, "verify") == 1
    assert policy.record_rework(task_repo, RECORD, "verify") == 2
    assert policy.grant_rework_allowance(task_repo, RECORD, "verify") == 1
    assert policy.grant_rework_allowance(task_repo, RECORD, "verify") == 0
    assert policy.rework_attempts(task_repo, RECORD, "verify") == 2
    assert policy.rework_allowances(task_repo, RECORD, "verify") == 2


def test_task_authorization_does_not_authorize_destructive_cancellation(task_repo: Path) -> None:
    approval = policy.approve_checkpoint_guarded(task_repo, RECORD, "classify", interactive=False)
    assert approval.status == "approved"

    cancellation = policy.authorize_kill(task_repo, RECORD)
    assert cancellation.status == "challenge"
    assert cancellation.code
    record = tracker.read_record(task_repo, RECORD)
    assert record is not None and record["status"] == "open"


def test_plans_do_not_require_a_user_budget_or_permission_level() -> None:
    (child,) = decompose.parse_children({
        "children": [
            {
                "title": "requested change",
                "acceptance": ["When run, the changed command shall return the requested result."],
                "scope": ["src/basicly/loop.py"],
                "depends_on": [],
                "demonstration": "`uv run pytest tests/test_loop.py -q` exercises task execution",
            }
        ]
    })

    assert child.budget_tokens is None
    assert child.integrity is None


def test_absent_budget_does_not_halt_recorded_spend(task_repo: Path) -> None:
    _spend(task_repo, 250)
    status = policy.spend_status(task_repo, RECORD)

    assert status.spent_tokens == 250
    assert not status.halted
    assert status.remaining_tokens is None


def test_an_explicit_optional_limit_is_enforced_and_can_be_cleared(task_repo: Path) -> None:
    policy.set_token_limit(task_repo, RECORD, 100)
    _spend(task_repo, 99)
    assert not policy.spend_status(task_repo, RECORD).halted
    _spend(task_repo, 1)
    assert policy.spend_status(task_repo, RECORD).halted
    assert policy.active_grant(task_repo, RECORD) is None

    policy.set_token_limit(task_repo, RECORD, None)
    status = policy.spend_status(task_repo, RECORD)
    assert not status.halted
    assert status.spent_tokens == 100
    assert status.remaining_tokens is None


@pytest.mark.parametrize("value", [0, -1, True])
def test_an_invalid_explicit_limit_is_refused_without_a_ledger_write(
    task_repo: Path, value: int
) -> None:
    before = _lines(task_repo)
    with pytest.raises(ValueError):
        policy.set_token_limit(task_repo, RECORD, value)
    assert _lines(task_repo) == before


def test_an_explicit_legacy_budget_remains_enforced_without_rewriting_history(
    task_repo: Path,
) -> None:
    tracker.add_comment(task_repo, RECORD, "[harness-policy] grant level=L3 budget=100")
    before = _lines(task_repo)
    _spend(task_repo, 100)
    status = policy.spend_status(task_repo, RECORD)

    assert status.halted
    assert status.remaining_tokens == 0
    assert before <= _lines(task_repo)


def test_clearing_a_legacy_limit_does_not_resurrect_its_old_remainder(task_repo: Path) -> None:
    tracker.add_comment(task_repo, RECORD, "[harness-policy] grant level=L3 budget=100")
    _spend(task_repo, 100)
    assert policy.spend_status(task_repo, RECORD).halted
    historical = _lines(task_repo)

    policy.set_token_limit(task_repo, RECORD, None)

    status = policy.spend_status(task_repo, RECORD)
    assert not status.halted
    assert status.remaining_tokens is None
    assert status.spent_tokens == 100
    assert historical <= _lines(task_repo)


def test_a_release_requires_verification_but_no_permission_grant(task_repo: Path) -> None:
    config = PolicyConfig(required_gates=("verify",), max_rework=2)
    assert "verify" in (release.autonomy_refusal(task_repo, RECORD, config) or "")

    _verify(task_repo)
    assert release.autonomy_refusal(task_repo, RECORD, config) is None
    assert policy.active_grant(task_repo, RECORD) is None


@pytest.mark.parametrize("first,second", [("one", "two"), ("two", "one")])
def test_concurrent_task_checkpoints_merge_without_updating_the_open_branch(
    tmp_path: Path, first: str, second: str
) -> None:
    environment = git_env(tmp_path)
    repo = make_host(tmp_path / "host", environment)
    flipped_tracker.flipped_repo(repo)
    ledger = repo / LEDGER_RELATIVE
    events.append(
        ledger,
        [events.Draft(RECORD, events.KIND_CREATED, {"title": "requested task"})],
        clock=lambda: CLOCK,
    )
    tracker.add_comment(repo, RECORD, "[harness-policy] grant level=L3 budget=100")
    tracker.add_comment(repo, RECORD, "[harness-policy] grant revoked")
    historical = _lines(repo)
    git(repo, environment, "add", "-A")
    git(repo, environment, "commit", "-qm", "base")
    heads = {}
    touched = {}
    for branch, checkpoint in [("one", "classify"), ("two", "decompose")]:
        git(repo, environment, "checkout", "-qb", branch, "main")
        approved = policy.approve_checkpoint_guarded(repo, RECORD, checkpoint, interactive=False)
        assert approved.status == "approved", approved.detail
        git(repo, environment, "add", "-A")
        git(repo, environment, "commit", "-qm", f"record {checkpoint}")
        heads[branch] = git(repo, environment, "rev-parse", "HEAD").stdout.strip()
        touched[branch] = set(
            git(repo, environment, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
        )
    assert touched["one"] and touched["two"]
    assert touched["one"].isdisjoint(touched["two"])
    git(repo, environment, "checkout", "-q", "main")
    git(repo, environment, "merge", "-q", "--no-edit", first)
    assert git(repo, environment, "rev-parse", second).stdout.strip() == heads[second]

    git(repo, environment, "merge", "-q", "--no-edit", second)

    assert historical <= _lines(repo)
    assert policy.checkpoint_approved(repo, RECORD, "classify")
    assert policy.checkpoint_approved(repo, RECORD, "decompose")
    assert policy.active_grant(repo, RECORD) is None
    assert git(repo, environment, "status", "--porcelain").stdout.strip() == ""


def test_clearing_and_reapplying_the_same_limit_preserves_each_request(task_repo: Path) -> None:
    for _ in range(2):
        policy.set_token_limit(task_repo, RECORD, 100)
        assert policy.spend_status(task_repo, RECORD).remaining_tokens == 100
        policy.set_token_limit(task_repo, RECORD, None)
        assert policy.spend_status(task_repo, RECORD).remaining_tokens is None

from __future__ import annotations

import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

from basicly import merge, policy, rebase, run_record, verify
from basicly.config import PolicyConfig
from basicly.worktree import Session
from tests import fake_tracker, flipped_tracker


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeGit:
    def __init__(self, responses: dict[str, _Proc]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        for key in (" ".join(args), args[0]):
            if key in self.responses:
                return self.responses[key]
        raise AssertionError(f"unstubbed git subcommand {args[0]!r}: git {' '.join(args)}")

    def ran(self, subcommand: str) -> bool:
        return any(call[0] == subcommand for call in self.calls)


def test_an_unstubbed_git_subcommand_fails_the_test_naming_itself() -> None:

    with pytest.raises(AssertionError, match=r"unstubbed git subcommand 'bisect': git bisect"):
        _FakeGit({})(["bisect", "start"])


def _session() -> Session:
    return Session(
        name="feat",
        branch="harness/feat",
        base="main",
        base_head="abc123",
        worktree_path="/tmp/repo.worktrees/feat",
        created_at="2026-07-14T00:00:00Z",
    )


_OWN_COMMITS = "rev-list --count abc123..harness/feat"

_AHEAD_OF_BASE = "rev-list --count main..harness/feat"
_HAS_WORK = {_AHEAD_OF_BASE: _Proc(0, "1")}

_REPLAY_CLEAN = {
    "rev-list --merges main..harness/feat": _Proc(0, ""),
    "rev-parse harness/feat": _Proc(0, ""),
    "ls-tree": _Proc(0, ""),
}


def _patch_git[T](monkeypatch: pytest.MonkeyPatch, fake: T) -> T:

    if isinstance(fake, _FakeGit):
        fake.responses = {**_REPLAY_CLEAN, **fake.responses}
    monkeypatch.setattr(merge, "git", fake)
    monkeypatch.setattr(rebase, "git", fake)
    return fake


@pytest.fixture
def base_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")


def test_probe_merge_safe_and_conflicts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_git(monkeypatch, _FakeGit({"merge-tree": _Proc(0)}))
    assert merge.probe_merge(tmp_path, "main", "harness/feat").safe is True

    _patch_git(monkeypatch, _FakeGit({"merge-tree": _Proc(1, "treeoid\nsrc/a.py\nsrc/b.py")}))
    probe = merge.probe_merge(tmp_path, "main", "harness/feat")
    assert probe.safe is False
    assert probe.conflicts == ("src/a.py", "src/b.py")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeGit({
        **_HAS_WORK,
        "status": _Proc(0, ""),
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(0),
        "rev-parse": _Proc(0, "def456"),
        "merge-base": _Proc(0),
    })
    _patch_git(monkeypatch, fake)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.merged is True
    merge_calls = [c for c in fake.calls if c[0] == "merge"]
    assert merge_calls and merge_calls[0][:3] == ["merge", "--no-ff", "harness/feat"]


def _record(agent: str, model: str | None = None) -> run_record.RunRecord:
    return run_record.build_record(
        agent=agent,
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=(agent, "-p", run_record.REDACTED_PROMPT),
        model=model,
    )


def test_merge_message_stamps_runner_trailers() -> None:
    msg = merge._merge_message(
        "feat", "harness/feat", "main", "basicly-x", _record("claude", "opus")
    )
    assert "Harness-Runner: claude" in msg
    assert "Harness-Model: opus" in msg
    assert "basicly-x" in msg


def test_merge_message_runner_without_a_model_omits_the_model_trailer() -> None:
    msg = merge._merge_message("feat", "harness/feat", "main", "basicly-x", _record("manual"))
    assert "Harness-Runner: manual" in msg
    assert "Harness-Model" not in msg


def test_merge_message_unchanged_without_a_record() -> None:
    msg = merge._merge_message("feat", "harness/feat", "main", "basicly-x")
    assert "Harness-Runner" not in msg
    assert msg.rstrip().endswith("basicly-x")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_stamps_attribution_from_the_run_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_record.record(
        tmp_path,
        "basicly-onb.5",
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=1.0,
            command=("claude", "-p", run_record.REDACTED_PROMPT),
            model="opus",
        ),
    )
    fake = _FakeGit({
        **_HAS_WORK,
        "status": _Proc(0, ""),
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(0),
        "rev-parse": _Proc(0, "def456"),
        "merge-base": _Proc(0),
    })
    _patch_git(monkeypatch, fake)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.merged is True
    merge_call = next(c for c in fake.calls if c[0] == "merge")
    message = merge_call[merge_call.index("-m") + 1]
    assert "Harness-Runner: claude" in message
    assert "Harness-Model: opus" in message


def test_commit_tracker_state_commits_beads_only_dirt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGit({
        "status": _Proc(
            0, " M .basicly/ledger/events-0001.jsonl\n?? .basicly/ledger/events-0002.jsonl\n"
        ),
        "add": _Proc(0),
        "commit": _Proc(0),
    })
    _patch_git(monkeypatch, fake)
    flipped_tracker.refuse_spawn(monkeypatch)

    assert merge.commit_tracker_state(tmp_path, "basicly-x") is True
    assert ["add", ".basicly/ledger"] in fake.calls
    commit = next(call for call in fake.calls if call[0] == "commit")
    assert "(basicly-x)" in commit[-1] and commit[-1].startswith("chore(beads):")


def test_commit_tracker_state_refuses_mixed_dirt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGit({"status": _Proc(0, " M src/app.py\n M .basicly/ledger/events-0001.jsonl\n")})
    _patch_git(monkeypatch, fake)

    assert merge.commit_tracker_state(tmp_path, "basicly-x") is False
    assert not fake.ran("commit")

    fake_clean = _FakeGit({"status": _Proc(0, "")})
    _patch_git(monkeypatch, fake_clean)
    assert merge.commit_tracker_state(tmp_path, "basicly-x") is False


def test_no_engine_module_builds_a_tracker_sync() -> None:

    def argv_sites(surface: str) -> list[str]:
        return sorted(
            path.name
            for path in sorted(Path(merge.__file__).parent.glob("*.py"))
            if f'["{surface}"' in path.read_text(encoding="utf-8")
        )

    assert argv_sites("close") != []
    assert argv_sites("sync") == []


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_rolls_up_tracker_dirt_before_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status_results = iter([
        _Proc(0, ""),
        _Proc(0, " M .basicly/ledger/events-0001.jsonl\n"),
        _Proc(0, ""),
    ])
    responses = {
        "rev-list": _Proc(0, "1"),
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(0),
        "rev-parse": _Proc(0, "def456"),
        "ls-tree": _Proc(0, ""),
        "add": _Proc(0),
        "commit": _Proc(0),
        "merge-base": _Proc(0),
    }
    calls: list[list[str]] = []

    def fake_git(args, **_kwargs):
        calls.append(args)
        if args[0] == "status":
            return next(status_results)
        if args[:2] == ["rev-list", "--merges"]:
            return _Proc(0, "")
        if args[0] not in responses:
            raise AssertionError(f"unstubbed git subcommand {args[0]!r}: git {' '.join(args)}")
        return responses[args[0]]

    _patch_git(monkeypatch, fake_git)
    fake_tracker.install(monkeypatch, lambda *_a, **_k: None)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.merged is True
    assert any(call[0] == "commit" for call in calls)
    assert any(call[0] == "merge" for call in calls)


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_aborts_on_rebase_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGit({
        **_HAS_WORK,
        "status": _Proc(0, ""),
        "rebase": _Proc(1, "CONFLICT"),
        "diff": _Proc(0, ""),
    })
    _patch_git(monkeypatch, fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "rebase-conflicts"
    assert ["rebase", "--abort"] in fake.calls
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_reads_conflict_paths_before_aborting_the_rebase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGit({
        **_HAS_WORK,
        "status": _Proc(0, ""),
        "rebase": _Proc(1, "CONFLICT"),
        "diff": _Proc(0, "src/shared.py\n"),
    })
    _patch_git(monkeypatch, fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.conflicts == ("src/shared.py",) and result.conflicted
    assert "src/shared.py" in result.detail
    unmerged = next(i for i, call in enumerate(fake.calls) if call[0] == "diff")
    aborted = next(i for i, call in enumerate(fake.calls) if call[:2] == ["rebase", "--abort"])
    assert unmerged < aborted


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_carries_probe_conflict_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(
        monkeypatch,
        _FakeGit({
            **_HAS_WORK,
            "status": _Proc(0, ""),
            "rebase": _Proc(0),
            "merge-tree": _Proc(1, "oid\nsrc/a.py\nsrc/b.py"),
        }),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.conflicts == ("src/a.py", "src/b.py") and result.conflicted


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_blocks_on_failed_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda *_a, **_k: verify.VerifyReport("full", (verify.CheckResult("ruff", "fail", 1),)),
    )
    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "verify-failed"


@pytest.mark.usefixtures("base_ready")
def test_override_gate_lands_without_running_the_gate_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _patch_git(
        monkeypatch,
        _FakeGit({
            "status": _Proc(0, ""),
            "rebase": _Proc(0),
            "merge-tree": _Proc(0),
            "merge": _Proc(0),
            "rev-parse": _Proc(0, "def456"),
            "rev-list": _Proc(0, "1"),
            "merge-base": _Proc(0),
        }),
    )
    runs: list[str] = []

    def _run_verify(_root, mode, *_a, **_k):
        runs.append(mode)
        return verify.VerifyReport(mode, (verify.CheckResult("ruff", "fail", 1),))

    monkeypatch.setattr(verify, "run_verify", _run_verify)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", override_gate=True)

    assert result.merged
    assert runs == []


def test_only_the_statuses_reached_before_the_gate_report_it_unreached() -> None:

    unreached = {
        merge.MergeResult("f", status, "").reached_gate for status in merge.PRE_GATE_STATUSES
    }
    reached = {
        merge.MergeResult("f", status, "").reached_gate
        for status in ("merged", "merge-conflicts", "merge-failed", merge.MERGE_UNPROVEN)
    }

    assert unreached == {False}
    assert reached == {True}
    assert merge.MergeResult("f", merge.VERIFY_UNRELIABLE, "").reached_gate is True
    assert merge.MergeResult("f", "verify-failed", "").reached_gate is True


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_blocks_on_probe_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(
        monkeypatch,
        _FakeGit({
            **_HAS_WORK,
            "status": _Proc(0, ""),
            "rebase": _Proc(0),
            "merge-tree": _Proc(1, "oid\nx.py"),
        }),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "merge-conflicts"


def test_merge_worktree_requires_bead(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="bead id"):
        merge.merge_worktree(tmp_path, "feat", bead="")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_not_ready_when_work_uncommitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeGit({"status": _Proc(0, " M src/app.py\n")})
    _patch_git(monkeypatch, fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "not-ready"
    assert "commit the work" in result.detail
    assert not fake.ran("rebase")
    assert not fake.ran("commit")
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_not_ready_when_branch_has_no_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rev-list": _Proc(0, "0"),
        "merge-base": _Proc(1),
    })
    _patch_git(monkeypatch, fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "not-ready"
    assert "no committed work" in result.detail
    assert not fake.ran("rebase")


@pytest.mark.usefixtures("base_ready")
def test_a_branch_already_merged_with_no_gate_is_recognised_not_called_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rev-list": _Proc(0, "0"),
        "merge-base": _Proc(0),
        _OWN_COMMITS: _Proc(0, "3"),
    })
    _patch_git(monkeypatch, fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == merge.ALREADY_LANDED
    assert "already an ancestor" in result.detail
    assert not fake.ran("rebase")
    assert not fake.ran("merge")
    assert result.conflicted is False


@pytest.mark.usefixtures("base_ready")
def test_a_half_landed_branch_is_not_refused_as_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _patch_git(
        monkeypatch,
        _FakeGit({
            "status": _Proc(0, ""),
            "rev-list": _Proc(0, "0"),
            "merge-base": _Proc(0),
            "rev-parse": _Proc(0, "movedsincequeued"),
            _OWN_COMMITS: _Proc(0, "3"),
        }),
    )

    result = merge.merge_worktree(
        tmp_path, "feat", bead="basicly-onb.5", expected_head="whatthequeuesaw"
    )

    assert result.status == merge.ALREADY_LANDED


@pytest.mark.usefixtures("base_ready")
def test_a_branch_that_never_received_a_commit_does_not_land_as_already_landed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rev-list": _Proc(0, "0"),
        "merge-base": _Proc(0),
        _OWN_COMMITS: _Proc(0, "0"),
    })
    _patch_git(monkeypatch, fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "not-ready"
    assert "no committed work" in result.detail
    assert result.merged is False
    assert not fake.ran("rebase")
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_an_unreadable_creation_commit_blocks_rather_than_claiming_a_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rev-list": _Proc(0, "0"),
        "merge-base": _Proc(0),
        _OWN_COMMITS: _Proc(128, ""),
    })
    _patch_git(monkeypatch, fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "not-ready"
    assert "abc123" in result.detail
    assert not fake.ran("rebase")


def test_the_queue_finishes_a_half_landed_lane_without_charging_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", merge.ALREADY_LANDED, "harness/a is already an ancestor"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    recorded: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: recorded.append(a) or 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert [q.result.name for q in results] == ["a", "b"]
    assert results[0].attempts == 0 and results[0].escalate is False
    assert results[1].result.merged is True
    assert recorded == []


def _landing_git(**overrides: _Proc) -> _FakeGit:
    responses = {
        **_HAS_WORK,
        "status": _Proc(0, ""),
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(0),
        "rev-parse": _Proc(0, "def456"),
        "merge-base": _Proc(0),
    }
    return _FakeGit({**responses, **overrides})


@pytest.mark.usefixtures("base_ready")
def test_a_merge_git_calls_successful_is_not_merged_until_it_is_proved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _patch_git(monkeypatch, _landing_git(**{"merge-base": _Proc(1)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.merged is False
    assert result.status == merge.MERGE_UNPROVEN
    assert "not reachable" in result.detail
    assert result.conflicted is False


@pytest.mark.usefixtures("base_ready")
def test_a_merge_whose_branch_ref_will_not_resolve_is_not_proved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _landing_git(**{"rev-parse": _Proc(128, "")}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == merge.MERGE_UNPROVEN
    assert "unresolvable" in result.detail


@pytest.mark.usefixtures("base_ready")
def test_a_landing_names_the_tip_it_took_and_the_commits_it_carried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _patch_git(
        monkeypatch,
        _landing_git(**{
            _AHEAD_OF_BASE: _Proc(0, "3"),
            "rev-parse --verify refs/heads/harness/feat": _Proc(0, "1a2b3c4d5e6f7890\n"),
        }),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.merged is True
    assert "harness/feat @ 1a2b3c4d5e6f" in result.detail
    assert "(3 commit(s))" in result.detail
    assert "into main @ def456" in result.detail


def test_a_count_git_cannot_answer_is_reported_as_uncounted_not_as_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _patch_git(monkeypatch, _FakeGit({"rev-list": _Proc(0, "4")}))
    assert merge.carried_commits(tmp_path, "main", "harness/feat") == 4

    for proc in (_Proc(128, ""), _Proc(0, "not-a-number"), _Proc(1, "4")):
        _patch_git(monkeypatch, _FakeGit({"rev-list": proc}))
        assert merge.carried_commits(tmp_path, "main", "harness/feat") is None


@pytest.mark.usefixtures("base_ready")
def test_a_lane_whose_branch_moved_after_queueing_is_refused_before_base_is_touched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _landing_git()
    _patch_git(monkeypatch, fake)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(
        tmp_path, "feat", bead="basicly-onb.5", expected_head="0000000queued"
    )

    assert result.status == merge.STALE_BRANCH
    assert "moved since it was queued" in result.detail
    assert not fake.ran("rebase")
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_a_lane_whose_branch_is_unchanged_since_queueing_still_lands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _landing_git())
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", expected_head="def456")

    assert result.merged is True


def test_is_ancestor_reads_any_git_failure_as_not_proved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    for code in (1, 128, 2):
        _patch_git(monkeypatch, _FakeGit({"merge-base": _Proc(code)}))
        assert merge.is_ancestor(tmp_path, "harness/feat", "main") is False
    _patch_git(monkeypatch, _FakeGit({"merge-base": _Proc(0)}))
    assert merge.is_ancestor(tmp_path, "harness/feat", "main") is True


def test_branch_head_is_none_for_a_ref_that_does_not_resolve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _FakeGit({"rev-parse": _Proc(128, "")}))
    assert merge.branch_head(tmp_path, "harness/gone") is None
    _patch_git(monkeypatch, _FakeGit({"rev-parse": _Proc(0, "abc123\n")}))
    assert merge.branch_head(tmp_path, "harness/feat") == "abc123"


def test_the_queue_snapshots_each_branch_head_when_the_queue_is_formed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(merge, "_session_branch_head", lambda _r, name: f"head-{name}")
    seen: dict[str, str | None] = {}

    def fake_merge(_r, name, **kwargs):
        seen[name] = kwargs.get("expected_head")
        return merge.MergeResult(name, "merged", "ok")

    monkeypatch.setattr(merge, "merge_worktree", fake_merge)

    merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert seen == {"a": "head-a", "b": "head-b"}


def test_the_queue_leaves_a_moved_lane_queued_and_spends_no_rework(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", merge.STALE_BRANCH, "harness/a moved since it was queued"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    recorded: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: recorded.append(a) or 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert [q.result.name for q in results] == ["a", "b"]
    assert results[0].deferred and results[0].attempts == 0 and results[0].escalate is False
    assert results[1].result.merged is True
    assert recorded == []


def test_the_queue_stops_on_an_unproved_merge_without_charging_the_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", merge.MERGE_UNPROVEN, "not reachable from main"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    recorded: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: recorded.append(a) or 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert [q.result.name for q in results] == ["a"]
    assert results[0].attempts == 0 and results[0].escalate is False
    assert recorded == []


def test_merge_queue_defers_a_not_ready_lane_and_keeps_going(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", "not-ready", "commit first"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    recorded: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: recorded.append(a) or 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert [q.result.name for q in results] == ["a", "b"]
    assert results[0].deferred and results[0].attempts == 0 and results[0].escalate is False
    assert results[1].result.merged is True
    assert recorded == []


def test_merge_queue_bounces_a_conflict_and_lands_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", "merged", "ok"),
        "b": merge.MergeResult("b", "merge-conflicts", "conflicts in: x.py", conflicts=("x.py",)),
        "c": merge.MergeResult("c", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda _r, _bead, _gate: 2)

    config = PolicyConfig(required_gates=("verify",), max_rework=2)
    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2"), ("c", "b3")], config=config)

    assert [q.result.name for q in results] == ["a", "b", "c"]
    assert results[1].bounced is True
    assert results[1].attempts == 2 and results[1].escalate is True
    assert results[2].result.merged is True


def test_merge_queue_stops_on_a_failed_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", "verify-failed", "verify full failed: pytest"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda _r, _bead, _gate: 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert [q.result.name for q in results] == ["a"]
    assert results[0].bounced is False and results[0].attempts == 1


def test_merge_queue_records_the_missed_coupling_as_a_dependency_edge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", "merged", "ok"),
        "b": merge.MergeResult("b", "merged", "ok"),
        "c": merge.MergeResult(
            "c", "rebase-conflicts", "conflicts in: src/shared.py", conflicts=("src/shared.py",)
        ),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    scopes = {"ba": ("docs/**",), "bb": ("src/shared.py",), "bc": ("src/shared.py",)}
    monkeypatch.setattr(
        merge.decompose, "bead_class_and_scope", lambda _r, bead: ("task", scopes[bead])
    )
    calls: list[list[str]] = []
    fake_tracker.install(monkeypatch, lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb"), ("c", "bc")])

    assert results[2].bounced and results[2].couplings == ("bb",)
    assert ["dep", "add", "bb", "bc", "-t", "related"] in calls
    assert not any(call[:2] == ["dep", "add"] and call[-1] == "blocks" for call in calls)


def test_merge_queue_attributes_a_bounce_against_a_later_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult(
            "a", "merge-conflicts", "conflicts in: src/shared.py", conflicts=("src/shared.py",)
        ),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    scopes = {"ba": ("src/shared.py",), "bb": ("src/*.py",)}
    monkeypatch.setattr(
        merge.decompose, "bead_class_and_scope", lambda _r, bead: ("task", scopes[bead])
    )
    calls: list[list[str]] = []
    fake_tracker.install(monkeypatch, lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb")])

    assert results[0].bounced and results[0].couplings == ("bb",)
    assert ["dep", "add", "ba", "bb", "-t", "related"] in calls


def test_merge_queue_records_no_coupling_outside_the_conflicting_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", "merged", "ok"),
        "b": merge.MergeResult(
            "b", "rebase-conflicts", "conflicts in: src/shared.py", conflicts=("src/shared.py",)
        ),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    scopes = {"ba": ("docs/**",), "bb": ("src/shared.py",)}
    monkeypatch.setattr(
        merge.decompose, "bead_class_and_scope", lambda _r, bead: ("task", scopes[bead])
    )
    calls: list[list[str]] = []
    fake_tracker.install(monkeypatch, lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb")])

    assert results[1].bounced and results[1].couplings == ()
    assert not any(call[:2] == ["dep", "add"] for call in calls)


def test_merge_queue_records_no_coupling_when_the_scope_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    outcomes = {
        "a": merge.MergeResult("a", "merged", "ok"),
        "b": merge.MergeResult(
            "b", "rebase-conflicts", "conflicts in: src/shared.py", conflicts=("src/shared.py",)
        ),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    monkeypatch.setattr(merge.decompose, "bead_class_and_scope", lambda _r, _bead: None)
    calls: list[list[str]] = []
    fake_tracker.install(monkeypatch, lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb")])

    assert results[1].bounced and results[1].couplings == ()
    assert not any(call[:2] == ["dep", "add"] for call in calls)


def test_merge_queue_bounce_records_no_coupling_without_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", "merged", "ok"),
        "b": merge.MergeResult("b", "merge-conflicts", "conflicts in: "),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    calls: list[list[str]] = []
    fake_tracker.install(monkeypatch, lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb")])

    assert results[1].bounced and results[1].couplings == ()
    assert not any(call[:2] == ["dep", "add"] for call in calls)


def test_merge_queue_bounce_never_resolves_the_conflict_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda _r, name, **_kwargs: merge.MergeResult(
            name, "merge-conflicts", "conflicts in: x.py", conflicts=("x.py",)
        ),
    )
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    fake = _FakeGit({"rev-parse": _Proc(0, "sha")})
    _patch_git(monkeypatch, fake)
    fake_tracker.install(monkeypatch, lambda *_a, **_k: _Proc(0))

    merge.merge_queue(tmp_path, [("a", "ba")])

    for forbidden in ("add", "checkout", "commit", "merge", "rebase", "restore"):
        assert not fake.ran(forbidden), f"a bounce must not run git {forbidden}"


def test_missed_couplings_attributes_only_the_lanes_that_touched_the_paths() -> None:
    landed = [("early", ("docs/x.md",)), ("culprit", ("src/shared.py", "src/other.py"))]
    assert merge.missed_couplings(("src/shared.py",), landed) == ("culprit",)
    assert merge.missed_couplings((), landed) == ()
    assert merge.missed_couplings(("src/none.py",), landed) == ()


def test_missed_couplings_ignores_a_tracker_collision() -> None:
    landed = [
        ("a", (".basicly/ledger/events-0001.jsonl",)),
        ("b", (".basicly/ledger/events-0001.jsonl", "src/x.py")),
    ]
    assert merge.missed_couplings((".basicly/ledger/events-0001.jsonl",), landed) == ()
    assert merge.missed_couplings((".basicly/ledger/events-0001.jsonl", "src/x.py"), landed) == (
        "b",
    )


def test_coupled_lanes_reads_the_declared_scope_not_the_landed_diff() -> None:
    scopes = {"wide": ("src/**",), "narrow": ("src/shared.py",), "elsewhere": ("docs/**",)}
    assert merge.coupled_lanes(("src/shared.py",), scopes, bounced="me") == ("narrow", "wide")
    assert merge.coupled_lanes(("src/other.py",), scopes, bounced="me") == ("wide",)
    assert merge.coupled_lanes(("README.md",), scopes, bounced="me") == ()
    assert merge.coupled_lanes((), scopes, bounced="me") == ()


def test_coupled_lanes_is_free_of_dict_order_and_never_self_blames() -> None:
    scopes = {"z": ("src/shared.py",), "a": ("src/shared.py",), "self": ("src/shared.py",)}
    forward = merge.coupled_lanes(("src/shared.py",), scopes, bounced="self")
    backward = merge.coupled_lanes(
        ("src/shared.py",), dict(reversed(list(scopes.items()))), bounced="self"
    )
    assert forward == ("a", "z") and backward == forward


def test_coupled_lanes_ignores_a_tracker_collision() -> None:
    scopes = {"a": (".beads/**",), "b": ("src/x.py",)}
    assert merge.coupled_lanes((".basicly/ledger/events-0001.jsonl",), scopes, bounced="me") == ()
    assert merge.coupled_lanes(
        (".basicly/ledger/events-0001.jsonl", "src/x.py"), scopes, bounced="me"
    ) == ("b",)


def test_out_of_scope_paths_reports_only_what_no_declared_glob_covers() -> None:
    scope = ("src/basicly/merge.py", "tests/test_merge.py")
    changed = ("src/basicly/merge.py", "tests/test_merge.py", "src/basicly/loop.py", "README.md")
    assert merge.out_of_scope_paths(changed, scope) == ("README.md", "src/basicly/loop.py")
    assert merge.out_of_scope_paths(("src/basicly/merge.py",), scope) == ()
    assert merge.out_of_scope_paths((), scope) == ()


def test_out_of_scope_paths_is_sorted_and_deduplicated() -> None:
    changed = ("z.py", "a.py", "z.py", "  m.py  ", "")
    assert merge.out_of_scope_paths(changed, ("src/**",)) == ("a.py", "m.py", "z.py")


def test_out_of_scope_paths_says_nothing_when_nothing_was_declared() -> None:
    assert merge.out_of_scope_paths(("anything.py", "else.py"), ()) == ()


def test_out_of_scope_paths_never_faults_a_lane_for_the_tracker() -> None:
    changed = (".basicly/ledger/events-0001.jsonl", ".basicly/ledger/events-0002.jsonl", "src/x.py")
    assert merge.out_of_scope_paths(changed, ("docs/**",)) == ("src/x.py",)


def test_branch_changed_paths_diffs_against_the_merge_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGit({"diff": _Proc(0, "b.py\na.py\n\n")})
    _patch_git(monkeypatch, fake)
    assert merge.branch_changed_paths(tmp_path, "main", "harness/feat") == ("a.py", "b.py")
    assert fake.calls == [["diff", "--name-only", "main...harness/feat"]]


def test_branch_changed_paths_is_empty_when_git_cannot_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _FakeGit({"diff": _Proc(128, "fatal: bad revision\n")}))
    assert merge.branch_changed_paths(tmp_path, "main", "harness/gone") == ()


def test_attribute_couplings_considers_every_landing_of_the_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scopes = {"early": ("docs/**",), "late": ("src/shared.py",)}
    monkeypatch.setattr(
        merge.decompose, "bead_class_and_scope", lambda _r, bead: ("task", scopes[bead])
    )
    collisions = [("bounced", ("src/shared.py",))]

    forward = merge.attribute_couplings(tmp_path, collisions, ["early", "late"])
    backward = merge.attribute_couplings(tmp_path, collisions, ["late", "early"])

    assert forward == {"bounced": ("late",)} and backward == forward
    assert merge.attribute_couplings(tmp_path, collisions, []) == {}


def test_record_coupling_writes_the_pair_in_a_canonical_direction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    fake_tracker.install(monkeypatch, lambda _r, args: calls.append(args) or _Proc(0))

    merge.record_coupling(tmp_path, "epic.2", "epic.1")
    merge.record_coupling(tmp_path, "epic.1", "epic.2")

    assert calls == [["dep", "add", "epic.1", "epic.2", "-t", "related"]] * 2


def test_landing_order_lands_a_dependency_before_its_dependent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deps = {"b2": frozenset({"b1"}), "b1": frozenset(), "b3": frozenset({"b2"})}
    monkeypatch.setattr(merge, "blocking_dependencies", lambda _r, bead: deps[bead])

    ordered = merge.landing_order(tmp_path, [("c", "b3"), ("b", "b2"), ("a", "b1")])

    assert [bead for _, bead in ordered] == ["b1", "b2", "b3"]


def test_landing_order_is_stable_for_independent_lanes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(merge, "blocking_dependencies", lambda _r, _bead: frozenset())
    items = [("c", "b3"), ("a", "b1"), ("b", "b2")]
    assert merge.landing_order(tmp_path, items) == items


def test_landing_order_keeps_an_unresolvable_cycle_queued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deps = {"b1": frozenset({"b2"}), "b2": frozenset({"b1"})}
    monkeypatch.setattr(merge, "blocking_dependencies", lambda _r, bead: deps[bead])
    items = [("a", "b1"), ("b", "b2")]
    assert merge.landing_order(tmp_path, items) == items


def test_landing_order_ignores_dependencies_outside_the_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(merge, "blocking_dependencies", lambda _r, _bead: frozenset({"elsewhere"}))
    items = [("a", "b1"), ("b", "b2")]
    assert merge.landing_order(tmp_path, items) == items


def test_blocking_dependencies_reads_the_br_show_payload_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    payload = (
        '[{"id":"b2","dependencies":['
        '{"id":"b1","title":"lane","status":"open","dependency_type":"blocks"},'
        '{"id":"epic","title":"e","status":"open","dependency_type":"parent-child"}]}]'
    )
    fake_tracker.install(monkeypatch, lambda _r, _args: _Proc(0, payload))
    assert merge.blocking_dependencies(tmp_path, "b2") == frozenset({"b1"})


def test_blocking_dependencies_also_reads_the_echo_payload_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = (
        '{"id":"b2","dependencies":['
        '{"issue_id":"b2","depends_on_id":"b1","type":"blocks"},'
        '{"issue_id":"b2","depends_on_id":"epic","type":"parent-child"}]}'
    )
    fake_tracker.install(monkeypatch, lambda _r, _args: _Proc(0, payload))
    assert merge.blocking_dependencies(tmp_path, "b2") == frozenset({"b1"})


def test_blocking_dependencies_degrades_when_br_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_tracker.install(monkeypatch, lambda _r, _args: None)
    assert merge.blocking_dependencies(tmp_path, "b2") == frozenset()
    fake_tracker.install(monkeypatch, lambda _r, _args: _Proc(0, "not json"))
    assert merge.blocking_dependencies(tmp_path, "b2") == frozenset()


def test_merge_queue_all_merged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda _r, name, **_kwargs: merge.MergeResult(name, "merged", "ok"),
    )
    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])
    assert [q.result.name for q in results] == ["a", "b"]
    assert all(q.result.merged for q in results)


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_rejects_an_unknown_bead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / ".basicly" / "ledger"
    ledger.mkdir(parents=True)
    (ledger / "events-0001.jsonl").write_text('{"record":"proj-abc"}\n', encoding="utf-8")
    fake = _FakeGit({"status": _Proc(0, "")})
    _patch_git(monkeypatch, fake)

    with pytest.raises(SystemExit, match="unknown bead id"):
        merge.merge_worktree(tmp_path, "feat", bead="proj-nope")
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_aborts_when_the_merge_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeGit({
        **_HAS_WORK,
        "status": _Proc(0, ""),
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(1),
    })
    _patch_git(monkeypatch, fake)
    monkeypatch.setattr(
        merge.verify,
        "run_verify",
        lambda _p, _m: verify.VerifyReport(mode="full", results=()),
    )

    result = merge.merge_worktree(tmp_path, "feat", bead="proj-abc")

    assert result.status == "merge-failed"
    assert ["merge", "--abort"] in [c[:2] for c in fake.calls]


_FAILED = verify.VerifyReport("full", (verify.CheckResult("pytest", "fail", 1),))
_GREEN = verify.VerifyReport("full", (verify.CheckResult("pytest", "pass", 0),))


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_reports_unreliable_when_verify_does_not_reproduce(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: _GREEN)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == merge.VERIFY_UNRELIABLE
    assert result.unreliable is True
    assert "pytest" in result.detail and "passed unchanged on re-run" in result.detail


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_still_reports_verify_failed_when_it_reproduces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: _FAILED)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "verify-failed"
    assert result.unreliable is False


@pytest.mark.usefixtures("base_ready")
def test_a_reproduced_failure_carries_the_remedy_the_gate_already_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    printed = (
        "release-notes: basicly-fi1i7z: closed with a `## Scope` naming a shipped path "
        "and no release note\n"
        "release-notes:   write `changelog.d/basicly-fi1i7z.<category>.md`, or declare it "
        "invisible to a consumer\n"
    )
    report = verify.VerifyReport(
        "full", (verify.CheckResult("release-notes", "fail", 1, output=printed),)
    )
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: report)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: report)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "verify-failed"
    assert "changelog.d/basicly-fi1i7z.<category>.md" in result.detail


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_forgives_a_reproduced_failure_that_is_a_dependency_defect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    reproduced = verify.VerifyReport(
        "full",
        (
            verify.CheckResult(
                "pytest",
                "fail",
                1,
                output=(
                    "E           basicly_tracker_kit_events.LockUnavailableError: another "
                    "writer holds /repo/.basicly/ledger/.events.lock after 5.0s\n"
                ),
            ),
        ),
    )
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: reproduced)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == merge.VERIFY_UNRELIABLE
    assert result.unreliable is True
    assert "known dependency defect" in result.detail
    assert "one lock" in result.detail


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_does_not_forgive_reproduced_output_it_does_not_recognise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    reproduced = verify.VerifyReport(
        "full",
        (verify.CheckResult("pytest", "fail", 1, output="E   AssertionError: assert 3 == 4\n"),),
    )
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: reproduced)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "verify-failed"
    assert result.unreliable is False


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_reruns_only_after_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _landing_git())
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    reruns: list = []
    monkeypatch.setattr(
        verify,
        "rerun_failures",
        lambda *a, **_k: reruns.append(a) or verify.VerifyReport("full", ()),
    )

    merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert reruns == []


def test_merge_queue_spends_no_rework_on_an_unreliable_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult("a", merge.VERIFY_UNRELIABLE, "failed on pytest, passed on re-run"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    charged: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: charged.append(a) or 1)
    flakes: list = []
    monkeypatch.setattr(policy, "record_unreliable_gate", lambda *a, **_k: flakes.append(a) or 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert charged == []
    assert results[0].deferred and results[0].attempts == 0 and results[0].escalate is False
    assert [q.result.name for q in results] == ["a", "b"]
    assert results[1].result.merged is True
    assert [(a[1], a[2]) for a in flakes] == [("b1", merge.MERGE_GATE)]


_TRACKER_WIDE = (
    "E       AssertionError: assert ['basicly-tcm...east 128,000'] == []\n"
    "E         Left contains one more item: 'basicly-tcmy.5 completed at an estimate "
    "of 128,000, above working_set_max 72,000; raise it to at least 128,000'\n"
)


def _tracker(tmp_path: Path, *bead_ids: str) -> None:
    ledger = tmp_path / ".basicly" / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    ledger.joinpath("events-0001.jsonl").write_text(
        "".join(json.dumps({"record": one}) + "\n" for one in bead_ids), encoding="utf-8"
    )


def _reproduced(output: str) -> verify.VerifyReport:
    return verify.VerifyReport("full", (verify.CheckResult("pytest", "fail", 1, output=output),))


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_faults_the_lane_whose_record_failed_a_tracker_wide_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _tracker(tmp_path, "basicly-tcmy.5", "basicly-tcmy.6")
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: _reproduced(_TRACKER_WIDE))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-tcmy.6")

    assert result.status == merge.VERIFY_FOREIGN
    assert result.foreign is True and result.unreliable is False
    assert result.culprits == ("basicly-tcmy.5",)
    assert "basicly-tcmy.5" in result.detail and "not by this lane's diff" in result.detail


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_still_faults_the_lane_the_gate_names_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _tracker(tmp_path, "basicly-tcmy.5")
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: _reproduced(_TRACKER_WIDE))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-tcmy.5")

    assert result.status == "verify-failed"
    assert result.foreign is False


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_will_not_forgive_a_lane_a_truncated_id_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _tracker(tmp_path, "basicly-tcmy.5", "basicly-tcmy.6")
    elided = (
        "E       AssertionError: assert ['basicly-tcm...completed at an estimate of "
        "128,000, above working_set_max 72,000'] == []\n"
    )
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: _reproduced(elided))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-tcmy.6")

    assert result.status == "verify-failed"


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_will_not_forgive_a_run_that_also_failed_on_its_own_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _tracker(tmp_path, "basicly-tcmy.5", "basicly-tcmy.6")
    mixed = verify.VerifyReport(
        "full",
        (
            verify.CheckResult("pytest", "fail", 1, output=_TRACKER_WIDE),
            verify.CheckResult("ruff", "fail", 1, output="E   F401 unused import\n"),
        ),
    )
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: mixed)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-tcmy.6")

    assert result.status == "verify-failed"


def test_merge_queue_spends_no_rework_on_another_lanes_declaration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcomes = {
        "a": merge.MergeResult(
            "a",
            merge.VERIFY_FOREIGN,
            "invalidated in the shared tracker by basicly-tcmy.5",
            culprits=("basicly-tcmy.5",),
        ),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    charged: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: charged.append(a) or 1)
    attributed: list = []
    monkeypatch.setattr(
        policy, "record_shared_gate_failure", lambda *a, **_k: attributed.append(a) or 1
    )

    results = merge.merge_queue(tmp_path, [("a", "basicly-tcmy.6"), ("b", "basicly-tcmy.22")])

    assert charged == []
    assert results[0].deferred and results[0].attempts == 0 and results[0].escalate is False
    assert [q.result.name for q in results] == ["a"]
    assert [(one[1], one[2], one[3]) for one in attributed] == [
        ("basicly-tcmy.6", merge.MERGE_GATE, ("basicly-tcmy.5",))
    ]


def test_foreign_dirt_names_only_the_paths_outside_beads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeGit({
        "status": _Proc(0, " M src/app.py\n M .basicly/ledger/events-0001.jsonl\n?? .gitignore\n")
    })
    _patch_git(monkeypatch, fake)

    assert merge.foreign_dirt(tmp_path) == ("src/app.py", ".gitignore")


def test_foreign_dirt_is_empty_for_a_tracker_only_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(
        monkeypatch, _FakeGit({"status": _Proc(0, " M .basicly/ledger/events-0001.jsonl\n")})
    )
    assert merge.foreign_dirt(tmp_path) == ()


def test_the_warning_names_the_blocking_paths_and_the_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _FakeGit({"status": _Proc(0, " M .gitignore\n")}))

    warning = merge.skipped_tracker_commit_warning(tmp_path)

    assert "tracker state NOT committed" in warning
    assert ".gitignore" in warning
    assert "stash or commit" in warning and "re-run the advance" in warning


def test_the_warning_is_empty_when_nothing_foreign_is_dirty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _FakeGit({"status": _Proc(0, "")}))
    assert merge.skipped_tracker_commit_warning(tmp_path) == ""


_REBUILD_SCRIPT = """\
import json
import pathlib

sources = sorted(p.name for p in pathlib.Path("sources").glob("*.txt"))
pathlib.Path("manifest.json").write_text(json.dumps(sources, indent=2) + "\\n", encoding="utf-8")
"""

_REBUILD_PLAN = """\
import pathlib

names = sorted(p.stem for p in pathlib.Path("sources").glob("*.txt"))
plan = pathlib.Path("plan.md")
head, _, rest = plan.read_text(encoding="utf-8").partition("<!-- begin -->\\n")
_, _, tail = rest.partition("<!-- end -->\\n")
body = "sources: " + ", ".join(names) + "\\n"
plan.write_text(head + "<!-- begin -->\\n" + body + "<!-- end -->\\n" + tail, encoding="utf-8")
"""

_PLAN_SEED = """\
# Plan

hand-authored line

filler the lanes never touch, so git sees two hunks
and not one spanning the marker
and a third line of it

<!-- begin -->
<!-- end -->

trailing prose
"""


def _git_here(cwd: Path, *args: str) -> str:
    proc = subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _add_source(tree: Path, name: str) -> None:
    (tree / "sources" / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    for script in ("rebuild.py", "rebuild_plan.py"):
        subprocess.run(  # nosec B603
            [sys.executable, script], cwd=tree, check=True, capture_output=True
        )
    _git_here(tree, "add", "-A")
    _git_here(tree, "commit", "-m", f"add {name}")


def _edit_prose(tree: Path, word: str) -> None:
    plan = tree / "plan.md"
    plan.write_text(plan.read_text(encoding="utf-8").replace("hand-", f"{word}-"), encoding="utf-8")
    _git_here(tree, "commit", "-am", f"{word} edits the prose")


def _seed_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "sources").mkdir(parents=True)
    _git_here(tmp_path, "init", "-q", "-b", "main", str(repo))
    _git_here(repo, "config", "user.email", "tester@example.invalid")
    _git_here(repo, "config", "user.name", "tester")
    (repo / "rebuild.py").write_text(_REBUILD_SCRIPT, encoding="utf-8")
    (repo / "rebuild_plan.py").write_text(_REBUILD_PLAN, encoding="utf-8")
    (repo / "plan.md").write_text(_PLAN_SEED, encoding="utf-8")
    (repo / "basicly.toml").write_text(
        "[worktree.regenerate_commands]\n"
        f'"manifest.json" = [{json.dumps(sys.executable)}, "rebuild.py"]\n'
        f'"plan.md" = [{json.dumps(sys.executable)}, "rebuild_plan.py"]\n',
        encoding="utf-8",
    )
    _add_source(repo, "a")
    return repo


def _lane_worktree(tmp_path: Path, repo: Path) -> Session:
    lane = tmp_path / "feat"
    base_head = _git_here(repo, "rev-parse", "main")
    _git_here(repo, "worktree", "add", "-q", "-b", "harness/feat", str(lane), "main")
    return Session(
        name="feat",
        branch="harness/feat",
        base="main",
        base_head=base_head,
        worktree_path=str(lane),
        created_at="2026-08-06T00:00:00Z",
    )


def _fail_verify(monkeypatch: pytest.MonkeyPatch, output: str) -> None:

    failed = verify.VerifyReport("full", (verify.CheckResult("docs-claims", "fail", 1),))
    reran = verify.VerifyReport(
        "full", (verify.CheckResult("docs-claims", "fail", 1, output=output),)
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: failed)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: reran)


@pytest.fixture
def diverged_lane(tmp_path: Path) -> tuple[Path, Session]:
    repo = _seed_repo(tmp_path)
    session = _lane_worktree(tmp_path, repo)
    _add_source(Path(session.path), "c")
    _add_source(repo, "b")
    return repo, session


@pytest.fixture
def adding_lane(tmp_path: Path) -> tuple[Path, Session]:

    repo = _seed_repo(tmp_path)
    session = _lane_worktree(tmp_path, repo)
    lane = Path(session.path)
    (lane / "sources" / "d.txt").write_text("d\n", encoding="utf-8")
    _git_here(lane, "add", "-A")
    _git_here(lane, "commit", "-m", "add d")
    return repo, session


def test_two_lanes_that_rebuild_one_manifest_land_without_bouncing(
    monkeypatch: pytest.MonkeyPatch, diverged_lane: tuple[Path, Session]
) -> None:
    repo, session = diverged_lane
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: session)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(repo, "feat", bead="basicly-lyro")

    assert result.status == "merged", result.detail
    assert json.loads((repo / "manifest.json").read_text(encoding="utf-8")) == [
        "a.txt",
        "b.txt",
        "c.txt",
    ]
    assert "manifest.json" in result.detail


def test_a_partly_generated_doc_is_rebuilt_while_the_lane_prose_edit_survives(
    monkeypatch: pytest.MonkeyPatch, diverged_lane: tuple[Path, Session]
) -> None:
    repo, session = diverged_lane
    _edit_prose(Path(session.path), "lane")
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: session)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(
        merge.policy, "record_rework", lambda *_a: pytest.fail("the landing spent rework")
    )

    result = merge.merge_worktree(repo, "feat", bead="basicly-3w51")

    assert result.status == "merged", result.detail
    landed = (repo / "plan.md").read_text(encoding="utf-8")
    assert "sources: a, b, c" in landed
    assert "lane-authored line" in landed
    assert "plan.md" in result.detail


def test_a_conflict_in_the_hand_authored_half_still_bounces_to_the_lane(
    monkeypatch: pytest.MonkeyPatch, diverged_lane: tuple[Path, Session]
) -> None:
    repo, session = diverged_lane
    _edit_prose(Path(session.path), "lane")
    _edit_prose(repo, "base")
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: session)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(repo, "feat", bead="basicly-3w51")

    assert result.status == "rebase-conflicts"
    assert "plan.md" in result.conflicts
    assert "base-authored line" in (repo / "plan.md").read_text(encoding="utf-8")


def test_the_same_pass_still_bounces_when_a_source_really_conflicts(
    monkeypatch: pytest.MonkeyPatch, diverged_lane: tuple[Path, Session]
) -> None:
    repo, session = diverged_lane
    lane = Path(session.path)
    for tree, text in ((lane, "lane\n"), (repo, "base\n")):
        (tree / "sources" / "shared.txt").write_text(text, encoding="utf-8")
        _git_here(tree, "add", "-A")
        _git_here(tree, "commit", "-m", "touch the shared source")
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: session)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(repo, "feat", bead="basicly-lyro")

    assert result.status == "rebase-conflicts"
    assert "sources/shared.txt" in result.conflicts
    assert _git_here(repo, "status", "--porcelain") == ""
    assert _git_here(lane, "status", "--porcelain") == ""


def test_a_lane_that_only_adds_a_file_lands_with_its_stale_artifacts_rebuilt(
    monkeypatch: pytest.MonkeyPatch, adding_lane: tuple[Path, Session]
) -> None:

    repo, session = adding_lane
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: session)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(
        merge.policy, "record_rework", lambda *_a: pytest.fail("the landing spent rework")
    )

    result = merge.merge_worktree(repo, "feat", bead="basicly-e2mz.35")

    assert result.status == "merged", result.detail
    assert json.loads((repo / "manifest.json").read_text(encoding="utf-8")) == ["a.txt", "d.txt"]
    assert "sources: a, d" in (repo / "plan.md").read_text(encoding="utf-8")
    assert "manifest.json" in result.detail and "plan.md" in result.detail


def test_a_generated_path_the_rebuild_did_not_fix_is_reported_with_its_command(
    monkeypatch: pytest.MonkeyPatch, adding_lane: tuple[Path, Session]
) -> None:
    repo, session = adding_lane
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: session)
    _fail_verify(monkeypatch, "plan.md [plan-current-state]: generated block is stale\n")

    result = merge.merge_worktree(repo, "feat", bead="basicly-e2mz.35")

    assert result.status == "verify-failed"
    assert "`plan.md` <- " in result.detail and "rebuild_plan.py" in result.detail
    assert "manifest.json" not in result.detail


def test_a_verify_failure_naming_no_generated_path_is_reported_unchanged(
    monkeypatch: pytest.MonkeyPatch, adding_lane: tuple[Path, Session]
) -> None:
    repo, session = adding_lane
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: session)
    _fail_verify(monkeypatch, "tests/test_thing.py::test_one FAILED\n")

    result = merge.merge_worktree(repo, "feat", bead="basicly-e2mz.35")

    assert result.detail == "verify full failed: docs-claims"

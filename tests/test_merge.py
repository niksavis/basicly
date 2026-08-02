"""Tests for the merge orchestrator (onb.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import merge, policy, run_record, verify
from basicly.config import PolicyConfig
from basicly.worktree import Session


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class _FakeGit:
    """Routes git(...) calls to canned results, recording them.

    Keys are matched most-specific first: the whole argument list joined by spaces,
    then the subcommand alone. Two calls to one subcommand asking different
    questions are therefore separable, which the landing needs — ``rev-list --count
    main..harness/feat`` ("is the branch ahead of base?") and ``rev-list --count
    abc123..harness/feat`` ("did the branch ever receive a commit?") have different
    answers, and a stub that gives both the same one cannot tell a lane that landed
    from a lane that did nothing (basicly-tcmy.29).
    """

    def __init__(self, responses: dict[str, _Proc]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        for key in (" ".join(args), args[0]):
            if key in self.responses:
                return self.responses[key]
        return _Proc(0)

    def ran(self, subcommand: str) -> bool:
        return any(call[0] == subcommand for call in self.calls)


def _session() -> Session:
    return Session(
        name="feat",
        branch="harness/feat",
        base="main",
        base_head="abc123",
        worktree_path="/tmp/repo.worktrees/feat",
        created_at="2026-07-14T00:00:00Z",
    )


# The landing's "did this branch ever receive a commit?" probe, keyed on the session
# above: commits on harness/feat since the commit it was cut from.
_OWN_COMMITS = "rev-list --count abc123..harness/feat"


@pytest.fixture
def base_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make load_session/current_branch resolve a clean base checkout on 'main'."""
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")
    monkeypatch.setattr(merge, "reconcile_beads", lambda _r: None)


def test_probe_merge_safe_and_conflicts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A zero merge-tree exit is SAFE; non-zero surfaces the conflicting paths."""
    monkeypatch.setattr(merge, "git", _FakeGit({"merge-tree": _Proc(0)}))
    assert merge.probe_merge(tmp_path, "main", "harness/feat").safe is True

    monkeypatch.setattr(
        merge, "git", _FakeGit({"merge-tree": _Proc(1, "treeoid\nsrc/a.py\nsrc/b.py")})
    )
    probe = merge.probe_merge(tmp_path, "main", "harness/feat")
    assert probe.safe is False
    assert probe.conflicts == ("src/a.py", "src/b.py")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clean rebase + green verify + safe probe performs the --no-ff merge."""
    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(0),
        "rev-parse": _Proc(0, "def456"),
    })
    monkeypatch.setattr(merge, "git", fake)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.merged is True
    merge_calls = [c for c in fake.calls if c[0] == "merge"]
    assert merge_calls and merge_calls[0][:3] == ["merge", "--no-ff", "harness/feat"]


# --- merge-commit attribution (basicly-140a) --------------------------------


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
    """A record with an agent + model is stamped as Harness-Runner / Harness-Model trailers."""
    msg = merge._merge_message(
        "feat", "harness/feat", "main", "basicly-x", _record("claude", "opus")
    )
    assert "Harness-Runner: claude" in msg
    assert "Harness-Model: opus" in msg
    assert "basicly-x" in msg


def test_merge_message_runner_without_a_model_omits_the_model_trailer() -> None:
    """A record with no pinned model stamps only Harness-Runner."""
    msg = merge._merge_message("feat", "harness/feat", "main", "basicly-x", _record("manual"))
    assert "Harness-Runner: manual" in msg
    assert "Harness-Model" not in msg


def test_merge_message_unchanged_without_a_record() -> None:
    """No run-record: the message ends at the bead id, no trailers added."""
    msg = merge._merge_message("feat", "harness/feat", "main", "basicly-x")
    assert "Harness-Runner" not in msg
    assert msg.rstrip().endswith("basicly-x")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_stamps_attribution_from_the_run_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """merge_worktree reads the latest run-record and stamps its agent/model trailers."""
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
        "status": _Proc(0, ""),
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(0),
        "rev-parse": _Proc(0, "def456"),
    })
    monkeypatch.setattr(merge, "git", fake)
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
    """Tracker-only dirt is rolled into one chore commit referencing the bead."""
    fake = _FakeGit({"status": _Proc(0, " M .beads/issues.jsonl\n?? .beads/metadata.json\n")})
    monkeypatch.setattr(merge, "git", fake)
    flushed = {}
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, args: flushed.setdefault("args", args))

    assert merge.commit_tracker_state(tmp_path, "basicly-x") is True
    assert flushed["args"] == ["sync", "--flush-only"]
    assert ["add", ".beads"] in fake.calls
    commit = next(call for call in fake.calls if call[0] == "commit")
    assert "(basicly-x)" in commit[-1] and commit[-1].startswith("chore(beads):")


def test_commit_tracker_state_refuses_mixed_dirt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-beads dirt is someone's work — nothing is committed."""
    fake = _FakeGit({"status": _Proc(0, " M src/app.py\n M .beads/issues.jsonl\n")})
    monkeypatch.setattr(merge, "git", fake)

    assert merge.commit_tracker_state(tmp_path, "basicly-x") is False
    assert not fake.ran("commit")

    fake_clean = _FakeGit({"status": _Proc(0, "")})
    monkeypatch.setattr(merge, "git", fake_clean)
    assert merge.commit_tracker_state(tmp_path, "basicly-x") is False


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_rolls_up_tracker_dirt_before_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Loop tracker state dirtying the base no longer blocks the landing."""
    status_results = iter([
        _Proc(0, ""),  # _worktree_land_readiness: worktree tree is clean (work committed)
        _Proc(0, " M .beads/issues.jsonl\n"),  # commit_tracker_state sees the base dirt
        _Proc(0, ""),  # after the rollup commit, _assert_base_ready sees clean
    ])
    responses = {
        "rev-list": _Proc(0, "1"),  # branch has committed work ahead of base
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(0),
        "rev-parse": _Proc(0, "def456"),
    }
    calls: list[list[str]] = []

    def fake_git(args, **_kwargs):
        calls.append(args)
        if args[0] == "status":
            return next(status_results)
        return responses.get(args[0], _Proc(0))

    monkeypatch.setattr(merge, "git", fake_git)
    monkeypatch.setattr(merge.br, "try_run_br", lambda *_a, **_k: None)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.merged is True
    assert any(call[0] == "commit" for call in calls)  # the rollup chore commit
    assert any(call[0] == "merge" for call in calls)


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_aborts_on_rebase_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rebase conflict aborts cleanly and never reaches the merge."""
    fake = _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(1, "CONFLICT")})
    monkeypatch.setattr(merge, "git", fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "rebase-conflicts"
    assert ["rebase", "--abort"] in fake.calls
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_reads_conflict_paths_before_aborting_the_rebase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The collided paths are read while the rebase is stopped — the queue needs them (D5)."""
    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rebase": _Proc(1, "CONFLICT"),
        "diff": _Proc(0, "src/shared.py\n"),
    })
    monkeypatch.setattr(merge, "git", fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.conflicts == ("src/shared.py",) and result.conflicted
    assert "src/shared.py" in result.detail
    unmerged = next(i for i, call in enumerate(fake.calls) if call[0] == "diff")
    aborted = next(i for i, call in enumerate(fake.calls) if call[:2] == ["rebase", "--abort"])
    assert unmerged < aborted  # read first, or the rebase state is already gone


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_carries_probe_conflict_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A probe conflict carries its paths as data, not only inside the message."""
    monkeypatch.setattr(
        merge,
        "git",
        _FakeGit({
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
    """A failing re-verify blocks the merge."""
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda *_a, **_k: verify.VerifyReport("full", (verify.CheckResult("ruff", "fail", 1),)),
    )
    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "verify-failed"


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_blocks_on_probe_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A conflicting probe blocks the merge even after a clean rebase + verify."""
    monkeypatch.setattr(
        merge,
        "git",
        _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(0), "merge-tree": _Proc(1, "oid\nx.py")}),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "merge-conflicts"


def test_merge_worktree_requires_bead(tmp_path: Path) -> None:
    """A merge without a bead id is rejected (the commit-msg hook needs one)."""
    with pytest.raises(SystemExit, match="bead id"):
        merge.merge_worktree(tmp_path, "feat", bead="")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_not_ready_when_work_uncommitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dirty worktree is 'not-ready' and never touches base (basicly-4psl).

    Regression: an uncommitted worktree made ``git rebase`` abort with "unstaged
    changes", which was misreported as a rebase conflict and burned rework. The
    landing now bails before rebasing or committing any base tracker state.
    """
    fake = _FakeGit({"status": _Proc(0, " M src/app.py\n")})
    monkeypatch.setattr(merge, "git", fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "not-ready"
    assert "commit the work" in result.detail
    assert not fake.ran("rebase")  # base is never rebased
    assert not fake.ran("commit")  # no redundant tracker commit
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_not_ready_when_branch_has_no_commits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean branch with nothing ahead of base is 'not-ready', not a conflict.

    The branch is deliberately *not* an ancestor of base (``merge-base`` exits
    non-zero): nothing ahead and not merged is the genuinely-empty branch, which is a
    different situation from the half-landed one below (basicly-jr0l.50).
    """
    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rev-list": _Proc(0, "0"),
        "merge-base": _Proc(1),
    })
    monkeypatch.setattr(merge, "git", fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "not-ready"
    assert "no committed work" in result.detail
    assert not fake.ran("rebase")


# --- forward recovery of a half-landed lane (basicly-jr0l.50) ----------------


@pytest.mark.usefixtures("base_ready")
def test_a_branch_already_merged_with_no_gate_is_recognised_not_called_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect: a landing that succeeded looked like a branch with no work.

    A crash between the merge and the verify-gate record leaves the branch an
    ancestor of base with nothing ahead of it. Reading only ``rev-list`` reported "no
    committed work to land" — wrong, and under the supervisor it charged the lane a
    rework attempt for a landing that had worked.
    """
    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rev-list": _Proc(0, "0"),
        "merge-base": _Proc(0),  # the branch *is* an ancestor of base: it merged
        _OWN_COMMITS: _Proc(0, "3"),  # and it did work: three commits since it was cut
    })
    monkeypatch.setattr(merge, "git", fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == merge.ALREADY_LANDED
    assert "already an ancestor" in result.detail
    # Nothing is re-attempted: there is no second merge to make and no rebase to run.
    assert not fake.ran("rebase")
    assert not fake.ran("merge")
    # And it is not a failure shape, so no caller can score it as one.
    assert result.conflicted is False


@pytest.mark.usefixtures("base_ready")
def test_a_half_landed_branch_is_not_refused_as_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Recovery must outrank the staleness guard, or the two fixes cancel out.

    A branch that already merged has necessarily moved relative to whatever head the
    queue recorded, so checking staleness first would refuse the very state this
    recovers — re-stranding it (basicly-jr0l.46 + basicly-jr0l.50 interaction).
    """
    monkeypatch.setattr(
        merge,
        "git",
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
    """The defect: a lane that did no work shipped, because empty branches are ancestors.

    A worktree nobody committed to still points at the commit it was cut from, so it
    is an ancestor of base exactly like a lane that merged and then crashed before
    its gate was recorded. Reading ancestry alone let the landing report ``[merged]``,
    record a passing verify gate against a tree identical to base, approve ship and
    close the bead with an empty diff (basicly-tcmy.29). The commit count since the
    branch was cut is what separates the two.
    """
    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rev-list": _Proc(0, "0"),
        "merge-base": _Proc(0),  # an ancestor of base — but only because it never moved
        _OWN_COMMITS: _Proc(0, "0"),
    })
    monkeypatch.setattr(merge, "git", fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "not-ready"
    assert "no committed work" in result.detail
    # Nothing is claimed and nothing is touched: no gate to record, no base to mutate.
    assert result.merged is False
    assert not fake.ran("rebase")
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_an_unreadable_creation_commit_blocks_rather_than_claiming_a_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unprovable is not the same as landed, and the asymmetry is deliberate.

    A spurious block costs one command; an empty landing is unrecoverable once the
    bead has closed, because gate records and checkpoints are append-only and there
    is no un-ship. So a creation commit git cannot resolve fails closed.
    """
    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rev-list": _Proc(0, "0"),
        "merge-base": _Proc(0),
        _OWN_COMMITS: _Proc(128, ""),  # unknown revision: the branch point is gone
    })
    monkeypatch.setattr(merge, "git", fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "not-ready"
    assert "abc123" in result.detail
    assert not fake.ran("rebase")


def test_the_queue_finishes_a_half_landed_lane_without_charging_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane whose work is already in base costs no rework and stops nothing."""
    outcomes = {
        "a": merge.MergeResult("a", merge.ALREADY_LANDED, "harness/a is already an ancestor"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    recorded: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: recorded.append(a) or 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert [q.result.name for q in results] == ["a", "b"]  # the pass continues
    assert results[0].attempts == 0 and results[0].escalate is False
    assert results[1].result.merged is True
    assert recorded == []


# --- the merge must prove itself (basicly-jr0l.46) --------------------------


def _landing_git(**overrides: _Proc) -> _FakeGit:
    """A fake git where a landing runs cleanly to the merge, before the proof."""
    responses = {
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
    """The defect: `git merge` exited 0, so the old code called the lane landed.

    This repo twice closed a bead with its code stranded on a harness branch. A
    ``merged`` status must be unreachable while the lane's head is not reachable
    from the base ref, no matter what the merge's own exit code claimed.
    """
    monkeypatch.setattr(merge, "git", _landing_git(**{"merge-base": _Proc(1)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.merged is False
    assert result.status == merge.MERGE_UNPROVEN
    assert "not reachable" in result.detail
    # Not a conflict, so it is never bounced back as a missed coupling.
    assert result.conflicted is False


@pytest.mark.usefixtures("base_ready")
def test_a_merge_whose_branch_ref_will_not_resolve_is_not_proved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unresolvable head is 'unknown', and unknown must never read as landed."""
    monkeypatch.setattr(merge, "git", _landing_git(**{"rev-parse": _Proc(128, "")}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == merge.MERGE_UNPROVEN
    assert "unresolvable" in result.detail


@pytest.mark.usefixtures("base_ready")
def test_a_lane_whose_branch_moved_after_queueing_is_refused_before_base_is_touched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane verified in a state that no longer exists must not land.

    The refusal has to come before base is mutated, so nothing is rebased, merged,
    or committed on the strength of a branch the queue never examined.
    """
    fake = _landing_git()
    monkeypatch.setattr(merge, "git", fake)
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
    """The staleness guard must not refuse the ordinary case it wraps."""
    monkeypatch.setattr(merge, "git", _landing_git())
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", expected_head="def456")

    assert result.merged is True


def test_is_ancestor_reads_any_git_failure_as_not_proved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`may I claim this landed` must answer no when it cannot answer at all.

    merge-base exits 1 for "not an ancestor" but other codes for a bad ref or a
    broken repo; every one of them has to read as unproved, never as proved.
    """
    for code in (1, 128, 2):
        monkeypatch.setattr(merge, "git", _FakeGit({"merge-base": _Proc(code)}))
        assert merge.is_ancestor(tmp_path, "harness/feat", "main") is False
    monkeypatch.setattr(merge, "git", _FakeGit({"merge-base": _Proc(0)}))
    assert merge.is_ancestor(tmp_path, "harness/feat", "main") is True


def test_branch_head_is_none_for_a_ref_that_does_not_resolve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """None means unknown, and is what keeps a missing branch from reading as moved."""
    monkeypatch.setattr(merge, "git", _FakeGit({"rev-parse": _Proc(128, "")}))
    assert merge.branch_head(tmp_path, "harness/gone") is None
    monkeypatch.setattr(merge, "git", _FakeGit({"rev-parse": _Proc(0, "abc123\n")}))
    assert merge.branch_head(tmp_path, "harness/feat") == "abc123"


def test_the_queue_snapshots_each_branch_head_when_the_queue_is_formed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read up front, not at each lane's turn — reading late defeats the check.

    The whole point is to notice a branch that moved *while an earlier lane was
    landing*; a head read at the lane's own turn already includes that movement.
    """
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
    """A branch that moved is a state, not a merit failure: charge nothing, keep going."""
    outcomes = {
        "a": merge.MergeResult("a", merge.STALE_BRANCH, "harness/a moved since it was queued"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    recorded: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: recorded.append(a) or 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert [q.result.name for q in results] == ["a", "b"]  # the pass continues
    assert results[0].deferred and results[0].attempts == 0 and results[0].escalate is False
    assert results[1].result.merged is True
    assert recorded == []


def test_the_queue_stops_on_an_unproved_merge_without_charging_the_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Base is in a contradictory state: stop, but do not blame the lane for it."""
    outcomes = {
        "a": merge.MergeResult("a", merge.MERGE_UNPROVEN, "not reachable from main"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    recorded: list = []
    monkeypatch.setattr(policy, "record_rework", lambda *a, **_k: recorded.append(a) or 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert [q.result.name for q in results] == ["a"]  # stopped before "b"
    assert results[0].attempts == 0 and results[0].escalate is False
    assert recorded == []  # no evidence against the lane's work


def test_merge_queue_defers_a_not_ready_lane_and_keeps_going(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Consume-as-ready: an uncommitted lane stays queued while the others land (D5)."""
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
    assert recorded == []  # no rework spent on an operator-fixable state


def test_merge_queue_bounces_a_conflict_and_lands_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A conflicting lane bounces back at the rework cap; independent lanes still land."""
    outcomes = {
        "a": merge.MergeResult("a", "merged", "ok"),
        "b": merge.MergeResult("b", "merge-conflicts", "conflicts in: x.py", conflicts=("x.py",)),
        "c": merge.MergeResult("c", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda _r, _bead, _gate: 2)

    config = PolicyConfig(required_gates=("verify",), max_rework=2)
    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2"), ("c", "b3")], config=config)

    assert [q.result.name for q in results] == ["a", "b", "c"]  # "c" is not held hostage
    assert results[1].bounced is True
    assert results[1].attempts == 2 and results[1].escalate is True
    assert results[2].result.merged is True


def test_merge_queue_stops_on_a_failed_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A red suite is a signal about the base: the pass stops instead of stacking on it."""
    outcomes = {
        "a": merge.MergeResult("a", "verify-failed", "verify full failed: pytest"),
        "b": merge.MergeResult("b", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda _r, _bead, _gate: 1)

    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])

    assert [q.result.name for q in results] == ["a"]  # stopped before "b"
    assert results[0].bounced is False and results[0].attempts == 1


def test_merge_queue_records_the_missed_coupling_as_a_dependency_edge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The lane whose declared scope covers the conflicting path gets the edge (D5)."""
    outcomes = {
        "a": merge.MergeResult("a", "merged", "ok"),
        "b": merge.MergeResult("b", "merged", "ok"),
        "c": merge.MergeResult(
            "c", "rebase-conflicts", "conflicts in: src/shared.py", conflicts=("src/shared.py",)
        ),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    # "ba" declared docs, "bb" declared the path that later collided.
    scopes = {"ba": ("docs/**",), "bb": ("src/shared.py",), "bc": ("src/shared.py",)}
    monkeypatch.setattr(
        merge.decompose, "bead_class_and_scope", lambda _r, bead: ("task", scopes[bead])
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb"), ("c", "bc")])

    assert results[2].bounced and results[2].couplings == ("bb",)
    # `related`, never `blocks`: the edge teaches the next decomposition, and a
    # gating edge would hold the bounced lane behind the lane it collided with
    # until that lane *ships* (basicly-grrb). The ids are sorted, so the edge does
    # not encode which of the pair happened to bounce (basicly-kjc5.32).
    assert ["dep", "add", "bb", "bc", "-t", "related"] in calls
    assert not any(call[:2] == ["dep", "add"] and call[-1] == "blocks" for call in calls)


def test_merge_queue_attributes_a_bounce_against_a_later_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attribution runs over the whole pass, not the prefix before the bounce (D9)."""
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
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb")])

    # "bb" landed *after* "ba" bounced, and is still named — an incremental
    # attribution had nothing to blame at the bounce and recorded no edge.
    assert results[0].bounced and results[0].couplings == ("bb",)
    assert ["dep", "add", "ba", "bb", "-t", "related"] in calls


def test_merge_queue_records_no_coupling_outside_the_conflicting_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A landing whose declared scope cannot match the conflicting path is not blamed."""
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
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb")])

    assert results[1].bounced and results[1].couplings == ()
    assert not any(call[:2] == ["dep", "add"] for call in calls)


def test_merge_queue_records_no_coupling_when_the_scope_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bead with no declared ``## Scope`` cannot be shown to own the path.

    Attribution costs the graph an edge rather than inventing one — a wrong edge
    would teach the next decomposition a coupling that does not exist.
    """
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
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb")])

    assert results[1].bounced and results[1].couplings == ()
    assert not any(call[:2] == ["dep", "add"] for call in calls)


def test_merge_queue_bounce_records_no_coupling_without_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no conflicting paths to attribute, no edge is invented (a wrong one teaches a lie)."""
    outcomes = {
        "a": merge.MergeResult("a", "merged", "ok"),
        "b": merge.MergeResult("b", "merge-conflicts", "conflicts in: "),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    calls: list[list[str]] = []
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, args: calls.append(args) or _Proc(0))

    results = merge.merge_queue(tmp_path, [("a", "ba"), ("b", "bb")])

    assert results[1].bounced and results[1].couplings == ()
    assert not any(call[:2] == ["dep", "add"] for call in calls)


def test_merge_queue_bounce_never_resolves_the_conflict_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No merge-time resolution (D5): a bounce touches no tree and commits nothing."""
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda _r, name, **_kwargs: merge.MergeResult(
            name, "merge-conflicts", "conflicts in: x.py", conflicts=("x.py",)
        ),
    )
    monkeypatch.setattr(policy, "record_rework", lambda *_a: 1)
    fake = _FakeGit({"rev-parse": _Proc(0, "sha")})
    monkeypatch.setattr(merge, "git", fake)
    monkeypatch.setattr(merge.br, "try_run_br", lambda *_a, **_k: _Proc(0))

    merge.merge_queue(tmp_path, [("a", "ba")])

    for forbidden in ("add", "checkout", "commit", "merge", "rebase", "restore"):
        assert not fake.ran(forbidden), f"a bounce must not run git {forbidden}"


def test_missed_couplings_attributes_only_the_lanes_that_touched_the_paths() -> None:
    """Attribution is by collided path, so an unrelated landing is never blamed."""
    landed = [("early", ("docs/x.md",)), ("culprit", ("src/shared.py", "src/other.py"))]
    assert merge.missed_couplings(("src/shared.py",), landed) == ("culprit",)
    assert merge.missed_couplings((), landed) == ()
    assert merge.missed_couplings(("src/none.py",), landed) == ()


def test_missed_couplings_ignores_a_tracker_collision() -> None:
    """Every landing rewrites .beads, so a tracker clash is not a scope coupling."""
    landed = [("a", (".beads/issues.jsonl",)), ("b", (".beads/issues.jsonl", "src/x.py"))]
    assert merge.missed_couplings((".beads/issues.jsonl",), landed) == ()
    # A real path in the same conflict still attributes, and only to whoever landed it.
    assert merge.missed_couplings((".beads/issues.jsonl", "src/x.py"), landed) == ("b",)


def test_coupled_lanes_reads_the_declared_scope_not_the_landed_diff() -> None:
    """A glob that can match the conflicting path names its lane (kjc5.32)."""
    scopes = {"wide": ("src/**",), "narrow": ("src/shared.py",), "elsewhere": ("docs/**",)}
    assert merge.coupled_lanes(("src/shared.py",), scopes, bounced="me") == ("narrow", "wide")
    assert merge.coupled_lanes(("src/other.py",), scopes, bounced="me") == ("wide",)
    assert merge.coupled_lanes(("README.md",), scopes, bounced="me") == ()
    assert merge.coupled_lanes((), scopes, bounced="me") == ()


def test_coupled_lanes_is_free_of_dict_order_and_never_self_blames() -> None:
    """The result is a function of the inputs alone, so no insertion order leaks in."""
    scopes = {"z": ("src/shared.py",), "a": ("src/shared.py",), "self": ("src/shared.py",)}
    forward = merge.coupled_lanes(("src/shared.py",), scopes, bounced="self")
    backward = merge.coupled_lanes(
        ("src/shared.py",), dict(reversed(list(scopes.items()))), bounced="self"
    )
    assert forward == ("a", "z") and backward == forward


def test_coupled_lanes_ignores_a_tracker_collision() -> None:
    """The engine rewrites .beads on every landing, so it evidences no coupling."""
    scopes = {"a": (".beads/**",), "b": ("src/x.py",)}
    assert merge.coupled_lanes((".beads/issues.jsonl",), scopes, bounced="me") == ()
    assert merge.coupled_lanes((".beads/issues.jsonl", "src/x.py"), scopes, bounced="me") == ("b",)


def test_out_of_scope_paths_reports_only_what_no_declared_glob_covers() -> None:
    """The declared scope is finally held against the diff it was a plan for (jr0l.44)."""
    scope = ("src/basicly/merge.py", "tests/test_merge.py")
    changed = ("src/basicly/merge.py", "tests/test_merge.py", "src/basicly/loop.py", "README.md")
    assert merge.out_of_scope_paths(changed, scope) == ("README.md", "src/basicly/loop.py")
    assert merge.out_of_scope_paths(("src/basicly/merge.py",), scope) == ()
    assert merge.out_of_scope_paths((), scope) == ()


def test_out_of_scope_paths_is_sorted_and_deduplicated() -> None:
    """A finding recorded on a bead must be a function of the inputs alone (D9)."""
    changed = ("z.py", "a.py", "z.py", "  m.py  ", "")
    assert merge.out_of_scope_paths(changed, ("src/**",)) == ("a.py", "m.py", "z.py")


def test_out_of_scope_paths_says_nothing_when_nothing_was_declared() -> None:
    """A bead with no ``## Scope`` — a hand-filed leaf — contradicts no plan."""
    assert merge.out_of_scope_paths(("anything.py", "else.py"), ()) == ()


def test_out_of_scope_paths_never_faults_a_lane_for_the_tracker() -> None:
    """The harness rewrites .beads on every landing, so no plan declares it."""
    changed = (".beads/issues.jsonl", ".beads/metadata.json", "src/x.py")
    assert merge.out_of_scope_paths(changed, ("docs/**",)) == ("src/x.py",)


def test_branch_changed_paths_diffs_against_the_merge_base(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Three-dot: a base that moved on after the fork is not the lane's work."""
    fake = _FakeGit({"diff": _Proc(0, "b.py\na.py\n\n")})
    monkeypatch.setattr(merge, "git", fake)
    assert merge.branch_changed_paths(tmp_path, "main", "harness/feat") == ("a.py", "b.py")
    assert fake.calls == [["diff", "--name-only", "main...harness/feat"]]


def test_branch_changed_paths_is_empty_when_git_cannot_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Best-effort like every read on this path: it costs a finding, never the pass."""
    monkeypatch.setattr(merge, "git", _FakeGit({"diff": _Proc(128, "fatal: bad revision\n")}))
    assert merge.branch_changed_paths(tmp_path, "main", "harness/gone") == ()


def test_attribute_couplings_considers_every_landing_of_the_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Order-free by construction: the whole pass's landings are the candidate set."""
    scopes = {"early": ("docs/**",), "late": ("src/shared.py",)}
    monkeypatch.setattr(
        merge.decompose, "bead_class_and_scope", lambda _r, bead: ("task", scopes[bead])
    )
    collisions = [("bounced", ("src/shared.py",))]

    forward = merge.attribute_couplings(tmp_path, collisions, ["early", "late"])
    backward = merge.attribute_couplings(tmp_path, collisions, ["late", "early"])

    assert forward == {"bounced": ("late",)} and backward == forward
    # Nothing landed, so there is nothing the pass can attribute against.
    assert merge.attribute_couplings(tmp_path, collisions, []) == {}


def test_record_coupling_writes_the_pair_in_a_canonical_direction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The edge must not encode which lane bounced (D9, basicly-kjc5.32)."""
    calls: list[list[str]] = []
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, args: calls.append(args) or _Proc(0))

    merge.record_coupling(tmp_path, "epic.2", "epic.1")
    merge.record_coupling(tmp_path, "epic.1", "epic.2")

    assert calls == [["dep", "add", "epic.1", "epic.2", "-t", "related"]] * 2


def test_landing_order_lands_a_dependency_before_its_dependent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dependency order comes from br, not from the caller's ordering (D5)."""
    deps = {"b2": frozenset({"b1"}), "b1": frozenset(), "b3": frozenset({"b2"})}
    monkeypatch.setattr(merge, "blocking_dependencies", lambda _r, bead: deps[bead])

    ordered = merge.landing_order(tmp_path, [("c", "b3"), ("b", "b2"), ("a", "b1")])

    assert [bead for _, bead in ordered] == ["b1", "b2", "b3"]


def test_landing_order_is_stable_for_independent_lanes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Independent lanes keep the caller's (scheduler-rank) order."""
    monkeypatch.setattr(merge, "blocking_dependencies", lambda _r, _bead: frozenset())
    items = [("c", "b3"), ("a", "b1"), ("b", "b2")]
    assert merge.landing_order(tmp_path, items) == items


def test_landing_order_keeps_an_unresolvable_cycle_queued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dependency cycle degrades to the caller's order instead of dropping a lane."""
    deps = {"b1": frozenset({"b2"}), "b2": frozenset({"b1"})}
    monkeypatch.setattr(merge, "blocking_dependencies", lambda _r, bead: deps[bead])
    items = [("a", "b1"), ("b", "b2")]
    assert merge.landing_order(tmp_path, items) == items


def test_landing_order_ignores_dependencies_outside_the_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dep that is not queued is already landed or not this pass's business."""
    monkeypatch.setattr(merge, "blocking_dependencies", lambda _r, _bead: frozenset({"elsewhere"}))
    items = [("a", "b1"), ("b", "b2")]
    assert merge.landing_order(tmp_path, items) == items


def test_blocking_dependencies_reads_the_br_show_payload_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``br show --json`` spells a dependency id/dependency_type — the shape actually parsed.

    Regression: reading only the ``depends_on_id``/``type`` spelling (what the
    create/dep-add echo returns) matched nothing in a real ``br show``, so every
    landing order silently degraded to the caller's.
    """
    payload = (
        '[{"id":"b2","dependencies":['
        '{"id":"b1","title":"lane","status":"open","dependency_type":"blocks"},'
        '{"id":"epic","title":"e","status":"open","dependency_type":"parent-child"}]}]'
    )
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, _args: _Proc(0, payload))
    assert merge.blocking_dependencies(tmp_path, "b2") == frozenset({"b1"})


def test_blocking_dependencies_also_reads_the_echo_payload_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The create/dep-add echo spelling (depends_on_id/type) parses too."""
    payload = (
        '{"id":"b2","dependencies":['
        '{"issue_id":"b2","depends_on_id":"b1","type":"blocks"},'
        '{"issue_id":"b2","depends_on_id":"epic","type":"parent-child"}]}'
    )
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, _args: _Proc(0, payload))
    assert merge.blocking_dependencies(tmp_path, "b2") == frozenset({"b1"})


def test_blocking_dependencies_degrades_when_br_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No br on PATH (or junk output) means no ordering, never a crash."""
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, _args: None)
    assert merge.blocking_dependencies(tmp_path, "b2") == frozenset()
    monkeypatch.setattr(merge.br, "try_run_br", lambda _r, _args: _Proc(0, "not json"))
    assert merge.blocking_dependencies(tmp_path, "b2") == frozenset()


def test_merge_queue_all_merged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When every node lands, the whole queue is processed with no escalation."""
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
    """A bead id missing from the tracker fails before any git merge starts."""
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "issues.jsonl").write_text('{"id":"proj-abc"}\n', encoding="utf-8")
    fake = _FakeGit({"status": _Proc(0, "")})
    monkeypatch.setattr(merge, "git", fake)

    with pytest.raises(SystemExit, match="unknown bead id"):
        merge.merge_worktree(tmp_path, "feat", bead="proj-nope")
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_aborts_when_the_merge_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook-rejected merge commit is aborted instead of stranding MERGE_HEAD."""
    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(1),
    })
    monkeypatch.setattr(merge, "git", fake)
    monkeypatch.setattr(
        merge.verify,
        "run_verify",
        lambda _p, _m: verify.VerifyReport(mode="full", results=()),
    )

    result = merge.merge_worktree(tmp_path, "feat", bead="proj-abc")

    assert result.status == "merge-failed"
    assert ["merge", "--abort"] in [c[:2] for c in fake.calls]


# --- An unreliable gate spends no rework budget (basicly-55yh) ----------------


_FAILED = verify.VerifyReport("full", (verify.CheckResult("pytest", "fail", 1),))
_GREEN = verify.VerifyReport("full", (verify.CheckResult("pytest", "pass", 0),))


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_reports_unreliable_when_verify_does_not_reproduce(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failure that passes unchanged on re-run is a distinct status, not verify-failed."""
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(0)}))
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
    """A real red suite must not be excused: the re-run fails too."""
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: _FAILED)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "verify-failed"
    assert result.unreliable is False


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_forgives_a_reproduced_failure_that_is_a_dependency_defect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A backwards clock step persists, so the re-run test alone cannot see it.

    Measured on basicly-m4zv.9: the landing re-ran, reproduced, and spent a rework
    attempt on a `br` defect the work could not have caused (basicly-kjc5.56).
    """
    reproduced = verify.VerifyReport(
        "full",
        (
            verify.CheckResult(
                "pytest",
                "fail",
                1,
                output=(
                    "E           RuntimeError: br update fx-d01 -t task failed: "
                    "Error: Validation failed: updated_at: cannot be before created_at\n"
                ),
            ),
        ),
    )
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: reproduced)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == merge.VERIFY_UNRELIABLE
    assert result.unreliable is True
    assert "known dependency defect" in result.detail
    # The reason travels with the verdict, so a reader is never left guessing which
    # dependency was forgiven or why forgiving it is safe.
    assert "clock steps backwards" in result.detail


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_does_not_forgive_reproduced_output_it_does_not_recognise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The register is a whitelist; anything else is the work's fault.

    This is the direction that matters. A signature list becomes a way to launder
    real failures the moment it matches something this repo can cause.
    """
    reproduced = verify.VerifyReport(
        "full",
        (verify.CheckResult("pytest", "fail", 1, output="E   AssertionError: assert 3 == 4\n"),),
    )
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: reproduced)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == "verify-failed"
    assert result.unreliable is False


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_reruns_only_after_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A green landing pays nothing for the mechanism: no re-run is attempted."""
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(0)}))
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
    """The reported defect: the lane was charged and escalated for an upstream flake."""
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

    assert charged == []  # the whole point
    assert results[0].deferred and results[0].attempts == 0 and results[0].escalate is False
    # `continue`, not `break`: a failure that does not reproduce says nothing
    # about the base, so the lanes behind it still land.
    assert [q.result.name for q in results] == ["a", "b"]
    assert results[1].result.merged is True
    # Forgiven, but not silently: the flake is recorded so a chronic one is visible.
    assert [(a[1], a[2]) for a in flakes] == [("b1", merge.MERGE_GATE)]


# --- Naming what blocked a tracker-state commit (basicly-f7li) ----------------


def test_foreign_dirt_names_only_the_paths_outside_beads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The loop's own tracker state is not foreign; anything else is."""
    fake = _FakeGit({"status": _Proc(0, " M src/app.py\n M .beads/issues.jsonl\n?? .gitignore\n")})
    monkeypatch.setattr(merge, "git", fake)

    assert merge.foreign_dirt(tmp_path) == ("src/app.py", ".gitignore")


def test_foreign_dirt_is_empty_for_a_tracker_only_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A commit that would have succeeded has nothing foreign to report."""
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, " M .beads/issues.jsonl\n")}))
    assert merge.foreign_dirt(tmp_path) == ()


def test_the_warning_names_the_blocking_paths_and_the_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An operator must not have to rediscover what stopped the commit."""
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, " M .gitignore\n")}))

    warning = merge.skipped_tracker_commit_warning(tmp_path)

    assert "tracker state NOT committed" in warning
    assert ".gitignore" in warning
    assert "stash or commit" in warning and "re-run the advance" in warning


def test_the_warning_is_empty_when_nothing_foreign_is_dirty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A declined commit on a clean tree is 'nothing pending', which needs no words."""
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, "")}))
    assert merge.skipped_tracker_commit_warning(tmp_path) == ""

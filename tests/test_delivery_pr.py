from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import ci_landing, delivery_pr, tracker
from basicly import cli as basicly_cli
from tests.delivery_pr_fixture import CLOCK, RECORD, Gh, _git, _lane, _lane_on, _repo

COMMIT_MSG = (
    Path(__file__).resolve().parent.parent / ".basicly" / "core" / "hooks" / "commit-msg.py"
)


def _artifact(repo: Path) -> dict:
    found = tracker.read_artifacts(repo, RECORD).get("landing-verification")
    return found if isinstance(found, dict) else {}


def test_a_green_pull_request_is_merged_by_github_and_leaves_landing_evidence(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    candidate = _lane(repo)
    gh = Gh(repo)

    landed = delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    assert _git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == landed.commit
    assert _git(repo, "rev-parse", "main") == landed.commit
    assert _git(repo, "rev-parse", f"{landed.commit}^2") == candidate.head
    merge = next(call for call in gh.calls if call[:2] == ["pr", "merge"])
    assert merge[merge.index("--match-head-commit") + 1] == candidate.head
    assert _artifact(repo) == {
        "status": "ci-verified",
        "head": landed.commit,
        "base": "main",
        "run": "https://example.invalid/runs/7",
        "tree": _git(repo, "rev-parse", f"{candidate.head}^{{tree}}"),
    }
    assert not _git(repo, "ls-remote", "origin", f"refs/heads/pr/lane-a/{candidate.head[:12]}")


def test_an_open_pull_request_on_the_same_head_is_reused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    candidate = _lane(repo)
    gh = Gh(repo)
    gh.prs.append({
        "number": 3,
        "url": "https://example.invalid/pull/3",
        "headRefOid": candidate.head,
        "branch": delivery_pr.branch_name("lane-a", candidate.head),
        "state": "OPEN",
    })

    landed = delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    assert landed.pull_request == "https://example.invalid/pull/3"
    assert not [call for call in gh.calls if call[:2] == ["pr", "create"]]


def test_a_failed_check_names_its_run_and_merges_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    candidate = _lane(repo)
    pushed = _git(repo, "ls-remote", "origin", "refs/heads/main")

    with pytest.raises(delivery_pr.PrDeliveryError, match="runs/7 ended failure in Quality gates"):
        delivery_pr.deliver(repo, candidate, "main", Gh(repo, "failure"), CLOCK)

    assert _git(repo, "ls-remote", "origin", "refs/heads/main") == pushed
    assert _artifact(repo) == {}


def test_a_lane_behind_its_base_is_refused_before_any_push(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    candidate = _lane(repo)
    (repo / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "chore: base moved")
    _git(repo, "push", "-q", "origin", "main")
    gh = Gh(repo)

    with pytest.raises(delivery_pr.PrDeliveryError, match="does not contain origin/main"):
        delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    assert not _git(repo, "ls-remote", "origin", "refs/heads/pr/*")
    assert not [call for call in gh.calls if call[0] == "pr"]


def test_a_landing_workflow_without_a_pull_request_trigger_is_refused_by_name(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path, triggers="  workflow_dispatch:\n")

    with pytest.raises(
        delivery_pr.PrDeliveryError, match=r"quality-gates\.yml has no pull_request"
    ):
        delivery_pr.deliver(repo, _lane(repo), "main", Gh(repo), CLOCK)


def test_the_merge_subject_and_body_pass_the_commit_msg_hook(tmp_path: Path) -> None:
    subject, body = delivery_pr.merge_message("basicly-ybun19f", "12", "pr/lane/abc", "main")
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(f"{subject}\n\n{body}\n", encoding="utf-8")

    done = subprocess.run(
        [sys.executable, str(COMMIT_MSG), str(message)], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stdout + done.stderr


def test_a_base_that_moves_during_the_wait_is_refused_before_github_merges(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    candidate = _lane(repo)
    gh = Gh(repo)
    viewed = gh.run

    def move_base(_argv: list[str]) -> str:
        if _git(repo, "rev-parse", "main") == candidate.base:
            (repo / "src" / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
            _git(repo, "commit", "-q", "-am", "chore: base moved")
            _git(repo, "push", "-q", "origin", "main")
        return json.dumps(viewed)

    gh.view = move_base

    with pytest.raises(delivery_pr.PrDeliveryError, match="stays open"):
        delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    assert not [call for call in gh.calls if call[:2] == ["pr", "merge"]]
    assert _artifact(repo) == {}


def _superseded(repo: Path, gh: Gh, head: str) -> str:
    branch = delivery_pr.branch_name("lane-a", head)
    _git(repo, "push", "-q", "origin", f"{head}:refs/heads/{branch}")
    url = "https://example.invalid/pull/2"
    gh.prs.append({"number": 2, "url": url, "headRefOid": head, "branch": branch, "state": "OPEN"})
    return branch


def test_a_superseded_pull_request_is_closed_and_its_landed_branch_deleted(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "harness/lane-a")
    (repo / "src" / "old.py").write_text("OLD = 1\n", encoding="utf-8")
    _git(repo, "add", "src/old.py")
    _git(repo, "commit", "-q", "-m", "chore: first attempt")
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    candidate = _lane_on(repo, "harness/lane-a")
    gh = Gh(repo)
    branch = _superseded(repo, gh, old)

    landed = delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    first = gh.prs[0]
    assert first["state"] == "CLOSED"
    assert first["comment"] == f"Superseded by {landed.pull_request}."
    assert not _git(repo, "ls-remote", "origin", f"refs/heads/{branch}")


def test_a_superseded_branch_whose_work_is_not_on_the_base_is_kept_and_named(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "src" / "lost.py").write_text("LOST = 1\n", encoding="utf-8")
    _git(repo, "add", "src/lost.py")
    _git(repo, "commit", "-q", "-m", "chore: work that never landed")
    side = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    candidate = _lane(repo)
    gh = Gh(repo)
    branch = _superseded(repo, gh, side)

    landed = delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    assert gh.prs[0]["state"] == "CLOSED"
    assert _git(repo, "ls-remote", "origin", f"refs/heads/{branch}")
    assert any(f"kept origin/{branch}" in note for note in landed.notes)


def test_cleanup_of_a_removed_lane_sweeps_its_pr_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(basicly_cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(basicly_cli.worktree, "load_session", lambda *_a: None)
    monkeypatch.setattr(basicly_cli.checkout, "current_branch", lambda _root: "main")
    monkeypatch.setattr(delivery_pr, "branches", lambda _root, _lane: ("pr/gone/abc",))
    monkeypatch.setattr(
        basicly_cli.worktree, "cleanup", lambda _name, **kw: calls.append(("cleanup", kw))
    )
    monkeypatch.setattr(
        delivery_pr, "sweep", lambda _root, lane, base: calls.append(("sweep", (lane, base))) or []
    )
    args = argparse.Namespace(name="gone", force=False)

    assert basicly_cli._cmd_worktree_cleanup(args) == 0
    assert calls == [
        ("cleanup", {"force": False, "repo_root": tmp_path, "missing_ok": True}),
        ("sweep", ("gone", "main")),
    ]


FAILED_RUN = {
    "databaseId": 3,
    "url": "https://example.invalid/runs/3",
    "status": "completed",
    "conclusion": "failure",
    "attempt": 1,
}


def _same_tree(repo: Path, candidate: ci_landing.Candidate, gh: Gh, failures: int) -> str:
    tree = _git(repo, "rev-parse", f"{candidate.head}^{{tree}}")
    earlier = _git(repo, "commit-tree", tree, "-p", candidate.base, "-m", "chore: earlier try")
    branch = delivery_pr.branch_name("lane-a", earlier)
    gh.prs.append({
        "number": 2,
        "url": "https://example.invalid/pull/2",
        "headRefOid": earlier,
        "branch": branch,
        "state": "CLOSED",
    })
    gh.history[earlier] = [FAILED_RUN] * failures
    return earlier


def test_a_head_that_already_failed_is_refused_before_any_push_with_the_retry_commands(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    candidate = _lane(repo)
    gh = Gh(repo)
    gh.history[candidate.head] = [FAILED_RUN]

    with pytest.raises(delivery_pr.PrDeliveryError, match="git commit --allow-empty") as refused:
        delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    assert "runs/3 already ended failure on attempt 1" in str(refused.value)
    assert not _git(repo, "ls-remote", "origin", "refs/heads/pr/*")
    assert not [call for call in gh.calls if call[:2] == ["pr", "create"]]


def test_a_tree_that_failed_twice_on_earlier_heads_is_refused_before_any_push(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    candidate = _lane(repo)
    gh = Gh(repo)
    _same_tree(repo, candidate, gh, failures=2)

    with pytest.raises(delivery_pr.PrDeliveryError, match="already failed 2 times on this tree"):
        delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    assert not _git(repo, "ls-remote", "origin", "refs/heads/pr/*")


def test_a_tree_that_failed_once_gets_one_more_run_on_a_new_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    candidate = _lane(repo)
    gh = Gh(repo)
    _same_tree(repo, candidate, gh, failures=1)

    landed = delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    assert _git(repo, "ls-remote", "origin", "refs/heads/main").split()[0] == landed.commit


def test_a_pass_only_on_a_rerun_of_the_pull_request_head_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    candidate = _lane(repo)
    gh = Gh(repo)
    gh.run = {**gh.run, "attempt": 2}

    with pytest.raises(delivery_pr.PrDeliveryError, match="succeeded only on attempt 2"):
        delivery_pr.deliver(repo, candidate, "main", gh, CLOCK)

    assert not [call for call in gh.calls if call[:2] == ["pr", "merge"]]

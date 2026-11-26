from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from basicly import ci_landing, delivery_pr, tracker
from basicly import cli as basicly_cli
from tests import flipped_tracker

COMMIT_MSG = (
    Path(__file__).resolve().parent.parent / ".basicly" / "core" / "hooks" / "commit-msg.py"
)
CLOCK = ci_landing.Clock(sleep=lambda _s: None, monotonic=lambda: 0.0)
RECORD = "acme-1"
TRIGGERS = "  pull_request:\n    branches: [main]\n  workflow_dispatch:\n"


def _git(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True, env=env
    ).stdout.strip()


def _commit(repo: Path, path: str, text: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"chore: {path}")
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, triggers: str = TRIGGERS) -> Path:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = flipped_tracker.flipped_repo(tmp_path / "repo")
    with (repo / ".gitignore").open("a", encoding="utf-8") as ignore:
        ignore.write(".basicly/usage/\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "remote", "add", "origin", str(origin))
    flow = repo / ".github" / "workflows" / "quality-gates.yml"
    flow.parent.mkdir(parents=True, exist_ok=True)
    flow.write_text(f"name: q\non:\n  push:\n    branches: [main]\n{triggers}", encoding="utf-8")
    _commit(repo, "src/app.py", "VALUE = 1\n")
    _git(repo, "push", "-q", "origin", "main")
    flipped_tracker.seed(repo, RECORD, title=RECORD)
    return repo


def _lane(repo: Path) -> ci_landing.Candidate:
    _git(repo, "checkout", "-q", "-b", "harness/lane-a")
    _git(repo, "checkout", "-q", "main")
    return _lane_on(repo, "harness/lane-a")


def _lane_on(repo: Path, branch: str) -> ci_landing.Candidate:
    base = _git(repo, "rev-parse", "main")
    _git(repo, "checkout", "-q", branch)
    (repo / "src" / "lane.py").write_text("LANE = 1\n", encoding="utf-8")
    _git(repo, "add", "src/lane.py")
    _git(repo, "commit", "-q", "-m", "chore: lane")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return ci_landing.Candidate("lane-a", RECORD, base, head)


class Gh:
    def __init__(self, repo: Path, conclusion: str = "success") -> None:
        self.repo = repo
        self.calls: list[list[str]] = []
        self.prs: list[dict] = []
        self.merged = ""
        self.history: dict[str, list[dict]] = {}
        self.view: Callable[[list[str]], str] = lambda _argv: json.dumps(self.run)
        self.run = {
            "databaseId": 7,
            "url": "https://example.invalid/runs/7",
            "status": "completed",
            "conclusion": conclusion,
            "attempt": 1,
            "jobs": [{"name": "Quality gates", "conclusion": conclusion}],
        }

    def _value(self, argv: list[str], flag: str) -> str:
        return argv[argv.index(flag) + 1]

    def __call__(self, argv: list[str]) -> str | None:
        self.calls.append(argv)
        handlers = {
            ("pr", "list"): self._list,
            ("pr", "close"): self._close,
            ("pr", "create"): self._create,
            ("run", "list"): self._runs,
            ("run", "view"): self.view,
            ("pr", "merge"): self._merge,
            ("pr", "view"): lambda _argv: json.dumps({
                "state": "MERGED",
                "mergeCommit": {"oid": self.merged},
            }),
        }
        handler = handlers.get((argv[0], argv[1]))
        return handler(argv) if handler else None

    def _list(self, argv: list[str]) -> str:
        if "--head" not in argv:
            return json.dumps([
                {"headRefName": pr["branch"], "headRefOid": pr["headRefOid"]} for pr in self.prs
            ])
        branch = self._value(argv, "--head")
        return json.dumps([
            pr for pr in self.prs if pr["branch"] == branch and pr["state"] == "OPEN"
        ])

    def _runs(self, argv: list[str]) -> str:
        commit = self._value(argv, "--commit")
        if commit in self.history:
            return json.dumps(self.history[commit])
        opened = any(pr["headRefOid"] == commit for pr in self.prs)
        return json.dumps([self.run] if "--event" in argv and opened else [])

    def _close(self, argv: list[str]) -> str:
        found = next(pr for pr in self.prs if str(pr["number"]) == argv[2])
        found["state"] = "CLOSED"
        found["comment"] = self._value(argv, "--comment")
        return ""

    def _create(self, argv: list[str]) -> str:
        branch = self._value(argv, "--head")
        head = _git(self.repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0]
        url = f"https://example.invalid/pull/{len(self.prs) + 5}"
        self.prs.append({
            "number": len(self.prs) + 5,
            "url": url,
            "headRefOid": head,
            "branch": branch,
            "state": "OPEN",
        })
        return f"{url}\n"

    def _merge(self, argv: list[str]) -> str | None:
        head = self._value(argv, "--match-head-commit")
        if head != self.prs[-1]["headRefOid"]:
            return None
        message = f"{self._value(argv, '--subject')}\n\n{self._value(argv, '--body')}"
        _git(self.repo, "fetch", "-q", "origin", "main")
        tree = _git(self.repo, "rev-parse", f"{head}^{{tree}}")
        self.merged = _git(
            self.repo, "commit-tree", tree, "-p", "origin/main", "-p", head, "-m", message
        )
        _git(self.repo, "push", "-q", "origin", f"{self.merged}:refs/heads/main")
        self.prs[-1]["state"] = "MERGED"
        return ""


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

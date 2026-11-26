from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import ci_landing, delivery_pr, tracker
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
    base = _git(repo, "rev-parse", "main")
    _git(repo, "checkout", "-q", "-b", "harness/lane-a")
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
            ("pr", "list"): lambda _argv: json.dumps(self.prs),
            ("pr", "create"): self._create,
            ("run", "list"): lambda argv: json.dumps([self.run] if "--event" in argv else []),
            ("run", "view"): lambda _argv: json.dumps(self.run),
            ("pr", "merge"): self._merge,
            ("pr", "view"): lambda _argv: json.dumps({
                "state": "MERGED",
                "mergeCommit": {"oid": self.merged},
            }),
        }
        handler = handlers.get((argv[0], argv[1]))
        return handler(argv) if handler else None

    def _create(self, argv: list[str]) -> str:
        branch = self._value(argv, "--head")
        head = _git(self.repo, "ls-remote", "origin", f"refs/heads/{branch}").split()[0]
        url = f"https://example.invalid/pull/{len(self.prs) + 5}"
        self.prs.append({"number": len(self.prs) + 5, "url": url, "headRefOid": head})
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

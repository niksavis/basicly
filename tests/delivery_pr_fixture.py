from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from basicly import ci_landing
from tests import flipped_tracker

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
        self.view: Callable[[list[str]], str] = self._view
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

    def _view(self, argv: list[str]) -> str:
        known = [run for runs in self.history.values() for run in runs]
        found = next((run for run in known if str(run.get("databaseId")) == argv[2]), self.run)
        return json.dumps(found)

    def _runs(self, argv: list[str]) -> str:
        commit = self._value(argv, "--commit")
        if commit in self.history:
            return json.dumps(self.history[commit])
        opened = commit == self.merged or any(pr["headRefOid"] == commit for pr in self.prs)
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

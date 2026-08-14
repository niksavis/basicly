"""The suite may not reach the repository the ambient git environment names.

Regression for basicly-e2mz.15, a P0: a `pytest` run wrote `core.bare = true`, a test
identity and `basicly.identityAllowEmail` into the developer's own `.git/config`, created
four `harness/*` branches there and registered four `/tmp` worktrees against it. For about
a minute `git status` answered "this operation must be run in a work tree" and every gate
in the repository was answering about the wrong tree.

The cause is not in the call sites. `tests/test_hooks.py` and `tests/test_landing_anchors.py`
correctly pass `cwd=<fixture repo>` to every `git init`, `git config` and `git worktree add`
they make. Git resolves the repository from `GIT_DIR` and its relatives *first* and from
`cwd` only afterwards, so one inherited variable retargets all of them at once. `git push`
is what made it recur: `.pre-commit-config.yaml` runs this suite from a pre-push hook.

So the fix is `tests/conftest.py`'s import-time scrub, and this file is the test that binds
it. It runs the two sites the incident named, in a subprocess, with the environment
deliberately poisoned, against a repository that exists only for this test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The two sites the leaked worktree paths named. Both create a sibling worktree and write
# config, which is the shape that turns one leaked variable into a corrupted repository.
_LANDING_TEST = "test_three_lanes_each_adding_a_check_and_a_ratchet_entry_all_land"
INCIDENT_SITES = (
    "tests/test_hooks.py::test_check_ignores_a_hook_edit_in_a_sibling_worktree",
    f"tests/test_landing_anchors.py::{_LANDING_TEST}",
)


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return proc.stdout


def _seed_repo(path: Path) -> Path:
    """A repository with one commit of its own, so a leak into it is unmistakable."""
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "seed@example.invalid")
    _git(path, "config", "user.name", "Seed")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return path


def _snapshot(repo: Path) -> tuple[str, str, str]:
    """The three things the incident changed: config bytes, refs, worktree registry."""
    return (
        (repo / ".git" / "config").read_text(encoding="utf-8"),
        _git(repo, "branch", "-a", "--format=%(refname) %(objectname)"),
        _git(repo, "worktree", "list", "--porcelain"),
    )


def _poisoned_env(repo: Path) -> dict[str, str]:
    """This process's environment with `GIT_DIR` aimed at *repo*, as the incident had it.

    `PYTEST_*` goes because an xdist worker exports `PYTEST_XDIST_WORKER`, which the child
    run would read as an instruction to behave like a worker.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    env["GIT_DIR"] = str(repo / ".git")
    return env


def test_a_leaked_git_dir_really_does_reach_the_repository_it_names(tmp_path: Path) -> None:
    """Positive control: without it, the guard below cannot tell a fix from a no-op.

    A `git config` run from an unrelated directory has to land in the seeded repository,
    or `GIT_DIR` is not the mechanism and the guard is asserting nothing.
    """
    victim = _seed_repo(tmp_path / "victim")
    before = _snapshot(victim)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    _git(elsewhere, "config", "user.email", "leaked@example.invalid", env=_poisoned_env(victim))

    assert _snapshot(victim) != before
    assert "leaked@example.invalid" in (victim / ".git" / "config").read_text(encoding="utf-8")


def test_the_incident_sites_leave_a_poisoned_git_dir_target_untouched(tmp_path: Path) -> None:
    """The guard: run the two sites with `GIT_DIR` poisoned; all three readings must hold.

    A subprocess rather than an in-process monkeypatch, because the defence under test is
    `tests/conftest.py`'s import-time scrub and only a fresh interpreter imports it again.
    """
    victim = _seed_repo(tmp_path / "victim")
    before = _snapshot(victim)

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *INCIDENT_SITES],
        cwd=REPO_ROOT,
        env=_poisoned_env(victim),
        check=False,
        capture_output=True,
        text=True,
    )

    # Before the exit code, because a leak fails both and only this one names the damage.
    config, branches, worktrees = _snapshot(victim)
    assert config == before[0]
    assert branches == before[1]
    assert worktrees == before[2]
    assert proc.returncode == 0, proc.stdout + proc.stderr

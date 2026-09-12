from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

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
    return (
        (repo / ".git" / "config").read_text(encoding="utf-8"),
        _git(repo, "branch", "-a", "--format=%(refname) %(objectname)"),
        _git(repo, "worktree", "list", "--porcelain"),
    )


def _poisoned_env(repo: Path) -> dict[str, str]:

    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTEST_")}
    env["GIT_DIR"] = str(repo / ".git")
    return env


def _hook_recording_git_dir(git_dir: Path, record: Path) -> None:
    hook = git_dir / "hooks" / "pre-commit"
    hook.write_text(
        f'#!/bin/sh\necho "${{GIT_DIR-unset}}" >> "{record.as_posix()}"\n', encoding="utf-8"
    )
    hook.chmod(0o755)


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"chore: add {name}")


def test_git_hands_a_hook_in_a_linked_worktree_the_variable_that_poisons_the_suite(
    tmp_path: Path,
) -> None:

    repo = _seed_repo(tmp_path / "repo")
    record = tmp_path / "hook-git-dir.txt"
    _hook_recording_git_dir(repo / ".git", record)
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-q", str(lane), "-b", "harness/lane")

    _commit(repo, "from-main-checkout.txt")
    _commit(lane, "from-linked-worktree.txt")

    from_main, from_lane = record.read_text(encoding="utf-8").split()
    assert from_main == "unset"
    assert Path(from_lane).resolve() == (repo / ".git" / "worktrees" / "lane").resolve()


def test_a_leaked_git_dir_really_does_reach_the_repository_it_names(tmp_path: Path) -> None:

    victim = _seed_repo(tmp_path / "victim")
    before = _snapshot(victim)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    _git(elsewhere, "config", "user.email", "leaked@example.invalid", env=_poisoned_env(victim))

    assert _snapshot(victim) != before
    assert "leaked@example.invalid" in (victim / ".git" / "config").read_text(encoding="utf-8")


def test_a_fixture_root_under_tmp_path_is_not_what_protects_the_repository(
    tmp_path: Path,
) -> None:

    victim = _seed_repo(tmp_path / "victim")
    before = _snapshot(victim)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "lane.txt").write_text("lane\n", encoding="utf-8")

    assert fixture.is_relative_to(tmp_path)

    env = _poisoned_env(victim)
    _git(fixture, "add", "-A", env=env)
    _git(fixture, "commit", "-q", "-m", "chore: seed the fixture repo", env=env)

    assert _snapshot(victim) != before
    assert _git(victim, "log", "-1", "--format=%s").strip() == "chore: seed the fixture repo"


UNSCRUBBED_GIT_SPAWNS = {
    "hooks.py: git rev-parse --git-path hooks",
    "hooks.py: git rev-parse --git-common-dir --show-toplevel",
    "supervise.py: git -C ... rev-parse HEAD",
    "supervise.py: git -C ... status --porcelain",
    "verify.py: git diff --cached --name-only --diff-filter=ACM",
}
_SPAWN_ATTRS = frozenset({"run", "Popen", "check_output", "call"})


def _git_argv(node: ast.AST) -> str | None:

    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr in _SPAWN_ATTRS
        and isinstance(func.value, ast.Name)
        and func.value.id == "subprocess"
    ):
        return None
    for arg in node.args:
        if not (isinstance(arg, ast.List) and arg.elts):
            continue
        first = arg.elts[0]
        if isinstance(first, ast.Constant) and first.value == "git":
            return " ".join(
                str(el.value) if isinstance(el, ast.Constant) else "..." for el in arg.elts
            )
    return None


def test_every_direct_git_spawn_in_the_engine_is_a_known_read_only_query() -> None:

    found = set()
    for path in sorted((REPO_ROOT / "src" / "basicly").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            argv = _git_argv(node)
            if argv is not None:
                found.add(f"{path.name}: {argv}")

    assert found == UNSCRUBBED_GIT_SPAWNS


def test_the_incident_sites_leave_a_poisoned_git_dir_target_untouched(tmp_path: Path) -> None:

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

    config, branches, worktrees = _snapshot(victim)
    assert config == before[0]
    assert branches == before[1]
    assert worktrees == before[2]
    assert proc.returncode == 0, proc.stdout + proc.stderr

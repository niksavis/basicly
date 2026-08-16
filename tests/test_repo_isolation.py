"""The suite may not reach the repository the ambient git environment names.

Regression for basicly-e2mz.15, a P0: a `pytest` run wrote `core.bare = true`, a test
identity and `basicly.identityAllowEmail` into the developer's own `.git/config`, created
four `harness/*` branches there and registered four `/tmp` worktrees against it. For about
a minute `git status` answered "this operation must be run in a work tree" and every gate
in the repository was answering about the wrong tree.

The cause is not in the call sites. `tests/test_hooks.py` and `tests/test_landing_anchors.py`
correctly pass `cwd=<fixture repo>` to every `git init`, `git config` and `git worktree add`
they make. Git resolves the repository from `GIT_DIR` and its relatives *first* and from
`cwd` only afterwards, so one inherited variable retargets all of them at once. What set
it is git, in the lane: a hook run from a linked worktree is handed `GIT_DIR`, and
`.pre-commit-config.yaml` runs this suite from `pre-push` (basicly-e2mz.16, first test
below). The push is the occasion, not the mechanism — the same hook fires at commit.

So the fix is `tests/conftest.py`'s import-time scrub, and this file is the test that binds
it. It runs the two sites the incident named, in a subprocess, with the environment
deliberately poisoned, against a repository that exists only for this test.
"""

from __future__ import annotations

import ast
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


def _hook_recording_git_dir(git_dir: Path, record: Path) -> None:
    """Install a `pre-commit` hook appending `<GIT_DIR or "unset">` for each commit."""
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
    """The producer basicly-e2mz.15 could not name, and why its probes missed it.

    That lane dumped a hook's environment in a *clean clone* and saw `GIT_EDITOR`,
    `GIT_EXEC_PATH`, `GIT_PREFIX` and nothing else — the first reading below, exactly.
    A clean clone is a main checkout, and the discriminator is the checkout: git exports
    `GIT_DIR` to a hook whenever the git dir is not a plain `<worktree>/.git`. Every lane
    is a linked worktree, `.pre-commit-config.yaml` runs this suite from `pre-push`, and
    a hook's descendants inherit it — so the suite ran aimed at the shared repository.

    Pinned as a test because the sanitising in `basicly.checkout` and the scrub in
    `tests/conftest.py` are both answers to this one fact about git's own behaviour.
    """
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


def test_a_fixture_root_under_tmp_path_is_not_what_protects_the_repository(
    tmp_path: Path,
) -> None:
    """The remedy basicly-e2mz.21 proposed, shown passing while the damage happens.

    That bead read the nine fixture commits on `harness/basicly-rn0o` as call sites that
    "ran with the live worktree as their repo root", and asked for an assertion that the
    fixture root is under `tmp_path`. The root already was. Git took `GIT_DIR` first and
    committed the fixture's files somewhere else entirely, which is why the assertion
    below holds and the victim still moves.

    Kept as a test rather than as a comment on a closed bead because the remedy is the
    obvious one to re-propose the next time this signature appears.
    """
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


# Every direct `subprocess` git call in the engine, i.e. the ones that do *not* go through
# `checkout.run`'s scrub. All five are read-only queries, so a leaked `GIT_DIR` makes them
# answer about the wrong repository but cannot corrupt it; the writing paths are the
# `checkout.run` chokepoint and the `runner.run` dispatch, both scrubbed (basicly-e2mz.16).
# Keyed by argv rather than by line so an edit elsewhere in the module cannot fail this.
UNSCRUBBED_GIT_SPAWNS = {
    "hooks.py: git rev-parse --git-path hooks",
    "hooks.py: git rev-parse --git-common-dir --show-toplevel",
    "supervise.py: git -C ... rev-parse HEAD",
    "supervise.py: git -C ... status --porcelain",
    "verify.py: git diff --cached --name-only --diff-filter=ACM",
}
_SPAWN_ATTRS = frozenset({"run", "Popen", "check_output", "call"})


def _git_argv(node: ast.AST) -> str | None:
    """The rendered argv of a `subprocess.<spawn>` starting with a literal `git`, else None.

    Non-literal elements — a `str(cwd)` — render as `...`, since the point is which call
    it is and not what it was pointed at.
    """
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
    """Answers the bead's third criterion for code the scrub does not cover.

    A new git call that bypasses `checkout.run` is how this incident comes back through a
    door the guard is not behind, so the inventory is pinned rather than described. Adding
    one fails here: route it through `checkout.git`, or add it above with its argv.
    """
    found = set()
    for path in sorted((REPO_ROOT / "src" / "basicly").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            argv = _git_argv(node)
            if argv is not None:
                found.add(f"{path.name}: {argv}")

    assert found == UNSCRUBBED_GIT_SPAWNS


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

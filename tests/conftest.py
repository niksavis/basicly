"""Shared pytest fixtures.

Every fixture here exists for one reason: a test's input must be a property of the
test, not of the machine running it (basicly-kjc5.55, basicly-tcmy.22).

A developer box has ``claude``/``codex``/``copilot`` on PATH and CI has none, so
``select_runner("auto")`` resolved to a headless adapter in one place and to the
manual handoff in the other — and nothing said so. That asymmetry hid
basicly-kjc5.53, a telemetry crash on the handoff path reachable from a real
landing: the suite was green locally, red on CI, and the difference was only found
by someone re-running it with a hand-stripped PATH. An incantation one machine
ever ran is not a check.

So the absent case is pinned as the default, because it is the stricter of the two
and the one CI exercises: every machine now agrees with CI without anybody
remembering a PATH prefix. Coverage of the CLI-present branch does not depend on
ambient PATH either way — ``test_runner.py`` drives both resolutions through
``is_available``/``select_runner``'s injected ``which``, which is where a unit test
should express it.

``work_repo`` is the same rule applied to the filesystem, the ambient ``GIT_*``
scrub is the same rule applied to the environment, and the process-global
registries are the same rule applied to whatever test happened to run first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from basicly import runner, session

REPO_ROOT = Path(__file__).resolve().parent.parent

# The headless adapters `auto` detects. Kept here rather than imported from
# basicly.runner on purpose: if a rename ever desynchronizes the two, the suite
# should start seeing an ambient CLI and fail loudly, not silently stop pinning.
AGENT_BINARIES = frozenset({"claude", "codex", "copilot"})

# The `GIT_*` variables a test run may keep. Everything else is dropped, because git
# resolves *which repository it is talking to* from the environment first and from `cwd`
# only afterwards: an inherited `GIT_DIR` makes every fixture's `git init`, `git config`
# and `git worktree add` operate on the repository the environment names, whatever tmp
# path the fixture passed. Measured 2026-08-14 in a throwaway clone (basicly-e2mz.15):
# `GIT_DIR=<clone>/.git pytest -q -n 4` wrote `user.name`/`user.email` and
# `basicly.identityAllowEmail` into the clone's config, created four `harness/*`
# branches, registered four worktrees under `/tmp/pytest-of-*`, and moved `main` — the
# signature of the incident that filed the bead. The same run with this scrub in place
# leaves config, refs and the worktree registry byte-identical.
#
# The set is pre-commit's own `no_git_env` allowlist (`pre_commit/git.py`, which
# documents the same class of leak) minus `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*`/
# `GIT_CONFIG_VALUE_*`: pre-commit keeps those to *forward* config into hooks, and
# injecting config into every git call a test makes is the same defect one layer down.
GIT_ENV_KEPT = frozenset({
    "GIT_ALLOW_PROTOCOL",
    "GIT_ASKPASS",
    "GIT_EXEC_PATH",
    "GIT_HTTP_PROXY_AUTHMETHOD",
    "GIT_SSH",
    "GIT_SSH_COMMAND",
    "GIT_SSL_CAINFO",
    "GIT_SSL_NO_VERIFY",
})


def _drop_ambient_git_env() -> None:
    """Remove every inherited ``GIT_*`` outside :data:`GIT_ENV_KEPT` from this process.

    At import rather than in a fixture, and that is not a style choice: collection and
    the session-scoped ``_tracked_repo_files`` both shell out to git before the first
    function-scoped fixture runs, and a subprocess inherits whatever ``os.environ`` holds
    at the moment it is spawned. Under xdist every worker imports this file, so each one
    scrubs its own environment.
    """
    for name in [
        name for name in os.environ if name.startswith("GIT_") and name not in GIT_ENV_KEPT
    ]:
        del os.environ[name]


_drop_ambient_git_env()


def _shared_git_config() -> Path | None:
    """The config file every checkout of this repo shares, or ``None`` outside git.

    Read from the git *common* dir, not ``REPO_ROOT/.git``: a linked worktree has no
    config of its own, so the incident's writes landed in the parent checkout's file and
    a suite running from a lane worktree has to watch that one.
    """
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = REPO_ROOT / common
    return common / "config"


@pytest.fixture(scope="session", autouse=True)
def _shared_git_config_untouched() -> Iterator[None]:
    """Fail the session when something in it rewrote the config of the repo under test.

    The acceptance criterion of basicly-e2mz.15 read literally. What made the incident a
    P0 was three keys in the shared config — ``core.bare = true`` detached the working
    tree, and an identity plus ``basicly.identityAllowEmail`` retargeted every gate — so
    the file's bytes are the thing to hold still.

    Refs and the worktree registry are deliberately *not* asserted here. A supervised
    pass runs this suite inside one lane while other lanes commit and provision worktrees
    in the same repository, so both would move for reasons that have nothing to do with
    the suite. ``tests/test_repo_isolation.py`` asserts all three against a repository
    that only it can touch.
    """
    config = _shared_git_config()
    before = config.read_text(encoding="utf-8") if config is not None else None
    yield
    if config is None:
        return
    assert config.read_text(encoding="utf-8") == before, (
        f"the suite rewrote {config}. A test reached the repository under test instead of"
        " its own fixture — see tests/test_repo_isolation.py (basicly-e2mz.15)."
    )


def _leaked_git_env() -> list[str]:
    return sorted(
        name for name in os.environ if name.startswith("GIT_") and name not in GIT_ENV_KEPT
    )


@pytest.fixture(autouse=True)
def _ambient_git_env_stays_scrubbed() -> Iterator[None]:
    """Hold :func:`_drop_ambient_git_env`'s result across every test in the session.

    ``tests/test_repo_isolation.py`` binds the scrub against the two sites the incident
    named, in a subprocess. This binds it against the *whole* population for the cost of
    one dict scan, which matters because the population is what the incident hit: four
    modules commit a fixture repository and only one of them is on that list.

    Checked after the test as well as before, so a test that sets ``GIT_DIR`` itself is
    caught where it happened rather than in whatever ran next (basicly-e2mz.21).
    """
    assert not _leaked_git_env(), (
        f"a forbidden GIT_* survived into this test: {_leaked_git_env()}. The import-time"
        " scrub is what stops it, so this means the scrub stopped running."
    )
    yield
    assert not _leaked_git_env(), (
        f"this test left {_leaked_git_env()} in os.environ. Git resolves which repository"
        " it is talking to from the environment before cwd, so it retargets every git call"
        " the next test makes, whatever tmp path that test passes (basicly-e2mz.15)."
    )


@pytest.fixture(autouse=True)
def _hide_ambient_agent_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any agent CLI the host has on PATH unresolvable for the whole suite.

    Patches ``shutil.which`` itself, since both runner call sites default to it.
    Every other binary — git, uv, pre-commit, br — still resolves normally, so
    this changes exactly three answers and nothing else.
    """
    real_which = shutil.which

    def which(cmd: str, mode: int = os.F_OK | os.X_OK, path: str | None = None) -> str | None:
        if Path(cmd).name.removesuffix(".exe").lower() in AGENT_BINARIES:
            return None
        return real_which(cmd, mode, path)

    monkeypatch.setattr(shutil, "which", which)


@pytest.fixture(autouse=True)
def _reset_process_globals():
    """Reset the two process-global registries around every test (basicly-tcmy.22).

    ``runner._BUDGET`` and ``session._OVERRIDES`` both document that tests reset
    them, and both resets were file-local — so the guarantee held only inside the
    files that remembered. ``configure_process_budget`` is first-caller-wins, so
    whichever test reached it first pinned the ceiling for the rest of the pytest
    process and every later assertion about capacity was reading someone else's
    numbers; a leaked override reconfigures the *next* test's view of committed
    config, which is the quiet direction on a permission control.

    Both ways round, because a test that leaks is as much a problem for the suite
    as a test that inherits, and only the teardown half catches the leaker.
    """
    runner.reset_process_budget()
    session.clear_overrides()
    yield
    runner.reset_process_budget()
    session.clear_overrides()


@pytest.fixture(scope="session")
def _tracked_repo_files() -> tuple[Path, ...]:
    """Every git-tracked path in this repo, resolved once for the whole suite."""
    listing = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    return tuple(Path(name) for name in listing.stdout.split("\0") if name)


@pytest.fixture
def work_repo(tmp_path: Path, _tracked_repo_files: tuple[Path, ...]) -> Path:
    """An isolated copy of this repo's *tracked* files, so tests never mutate it.

    Tracked-only rather than a filtered ``copytree`` (basicly-tcmy.22). The old
    fixture excluded ``.git`` and ``.venv`` and copied everything else — measured
    361 MB across 9925 files per test against this repo's own checkout, versus
    7.4 MB across 378 tracked. The bulk was ``node_modules`` and the live SQLite
    tracker database with its WAL, but the part that actually changes answers was
    the gitignored ``basicly.local.toml`` and any untracked ``.basicly-local/``
    content: a developer with a real local overlay was handing these tests
    different input than CI, which is exactly the asymmetry the rest of this file
    exists to close.

    A tracked file that is missing from the working tree raises here rather than
    being skipped: that is a deleted-but-unstaged edit, and it is a difference from
    CI too.
    """
    work = tmp_path / "repo"
    for relative in _tracked_repo_files:
        destination = work / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    return work

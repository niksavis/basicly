from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from basicly import runner, session
from basicly.checkout import COLOUR_ENV_FORCING, GIT_ENV_KEPT
from tests import ledger_guard

REPO_ROOT = Path(__file__).resolve().parent.parent

LIVE_LEDGER = REPO_ROOT / ".basicly" / "ledger"

AGENT_BINARIES = frozenset({"claude", "codex", "copilot"})


def _drop_ambient_git_env() -> None:

    for name in [
        name for name in os.environ if name.startswith("GIT_") and name not in GIT_ENV_KEPT
    ]:
        del os.environ[name]


_drop_ambient_git_env()


def _drop_ambient_colour_env() -> None:

    for name in COLOUR_ENV_FORCING:
        os.environ.pop(name, None)


_drop_ambient_colour_env()


def _shared_git_config() -> Path | None:

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

    real_which = shutil.which

    def which(cmd: str, mode: int = os.F_OK | os.X_OK, path: str | None = None) -> str | None:
        if Path(cmd).name.removesuffix(".exe").lower() in AGENT_BINARIES:
            return None
        return real_which(cmd, mode, path)

    monkeypatch.setattr(shutil, "which", which)


@pytest.fixture(autouse=True)
def _reset_process_globals():

    runner.reset_process_budget()
    session.clear_overrides()
    yield
    runner.reset_process_budget()
    session.clear_overrides()


@pytest.fixture(autouse=True)
def _the_live_ledger_is_never_written(request: pytest.FixtureRequest) -> Iterator[None]:

    with ledger_guard.watching(LIVE_LEDGER, request.node.nodeid) as watch:
        yield
    ledger_guard.report(watch)


@pytest.fixture(scope="session")
def _tracked_repo_files() -> tuple[Path, ...]:

    listing = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = tuple(Path(name) for name in listing.stdout.split("\0") if name)
    absent = [name for name in tracked if not (REPO_ROOT / name).exists()]
    if absent:
        shown = "\n  ".join(str(name) for name in absent[:10])
        more = f"\n  ... and {len(absent) - 10} more" if len(absent) > 10 else ""
        raise RuntimeError(
            f"{len(absent)} tracked file(s) deleted but not staged, so the work_repo "
            f"fixture cannot build a tree that matches CI.\n  "
            f"{shown}{more}\n"
            "Stage the deletions with `git add -A`, or restore them with `git restore`."
        )
    return tracked


@pytest.fixture
def work_repo(tmp_path: Path, _tracked_repo_files: tuple[Path, ...]) -> Path:

    work = tmp_path / "repo"
    for relative in _tracked_repo_files:
        destination = work / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    return work

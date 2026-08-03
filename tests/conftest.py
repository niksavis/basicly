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

``work_repo`` is the same rule applied to the filesystem, and the process-global
registries are the same rule applied to whatever test happened to run first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import runner, session

REPO_ROOT = Path(__file__).resolve().parent.parent

# The headless adapters `auto` detects. Kept here rather than imported from
# basicly.runner on purpose: if a rename ever desynchronizes the two, the suite
# should start seeing an ambient CLI and fail loudly, not silently stop pinning.
AGENT_BINARIES = frozenset({"claude", "codex", "copilot"})


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

"""Tests for the pre-push hook's contention refusal (basicly-u3b65o).

The defect this pins is a **message**, not a crash. `pre-commit` stashes the unstaged tree
for the pre-push stage; a landing writing the ledger inside that window changes the tree
under the stash, the restore conflicts, and the push dies reporting `Stashed changes
conflicted with hook auto-fixes`. The commits are intact, so an operator reads a local
mistake rather than two engine operations racing - the third surface of one class, after
`basicly-kjc5.63` on the base checkout.

Every case here is a real lock file on disk and an injected liveness answer, never a raced
process: the three answers `events.default_pid_liveness` can give - alive, gone, and the
Windows *cannot tell* - are test data, so the verdict is a property of the fixture rather
than of whichever machine ran it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from basicly import hooks

REPO_ROOT = Path(__file__).parent.parent
HOOK_SOURCE = REPO_ROOT / ".basicly" / "core" / "hooks" / "pre-push.py"
KIT_EVENTS = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker" / "events.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone hook by path, the way git runs it."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hook = _load(HOOK_SOURCE, "pre_push_hook")
events = _load(KIT_EVENTS, "basicly_tracker_kit_events")


def _lock(repo_root: Path, pid: int) -> Path:
    """A ledger lock file naming *pid*, in the shape `LedgerLock._try_create` writes."""
    ledger = repo_root / ".basicly" / "ledger"
    ledger.mkdir(parents=True, exist_ok=True)
    path = ledger / events.LOCK_NAME
    path.write_text(json.dumps({"pid": pid, "monotonic": 1.0}), encoding="utf-8")
    return path


def _ignored(relative: str) -> bool:
    """Whether this repo's rules ignore *relative*, asked of git rather than of the file."""
    return (
        subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", "--", relative],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def test_contention_is_named_when_a_live_writer_holds_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance criterion: the holder, not the stash."""
    _lock(tmp_path, 4321)
    monkeypatch.setattr(events, "default_pid_liveness", lambda _pid: True)
    monkeypatch.setattr(hook, "_kit_events", lambda: events)
    assert hook.ledger_write_holder(tmp_path) == 4321


def test_a_quiet_tree_reports_no_holder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No lock file is the ordinary case, and it must cost the push nothing."""
    monkeypatch.setattr(hook, "_kit_events", lambda: events)
    assert hook.ledger_write_holder(tmp_path) is None


def test_a_quiet_tree_is_reported_when_the_holder_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crashed writer's lock file must not wedge every push, which is the steal rule."""
    _lock(tmp_path, 4321)
    monkeypatch.setattr(events, "default_pid_liveness", lambda _pid: False)
    monkeypatch.setattr(hook, "_kit_events", lambda: events)
    assert hook.ledger_write_holder(tmp_path) is None


def test_a_quiet_tree_is_reported_when_the_platform_cannot_judge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows answers *cannot tell*, and the hook's own uncertainty is not contention.

    `events.default_pid_liveness` returns None there rather than calling
    ``TerminateProcess`` on the process it was asking about, so refusing here would refuse
    every push on that platform for as long as any stale lock file sat on disk.
    """
    _lock(tmp_path, 4321)
    monkeypatch.setattr(events, "default_pid_liveness", lambda _pid: None)
    monkeypatch.setattr(hook, "_kit_events", lambda: events)
    assert hook.ledger_write_holder(tmp_path) is None


@pytest.mark.parametrize("body", ["", "not json", "[]", '{"pid": "4321"}'])
def test_a_lock_file_it_cannot_read_is_quiet_rather_than_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """An unreadable lock is not evidence of a writer, and must not block a push."""
    ledger = tmp_path / ".basicly" / "ledger"
    ledger.mkdir(parents=True)
    (ledger / events.LOCK_NAME).write_text(body, encoding="utf-8")
    monkeypatch.setattr(events, "default_pid_liveness", lambda _pid: True)
    monkeypatch.setattr(hook, "_kit_events", lambda: events)
    assert hook.ledger_write_holder(tmp_path) is None


def test_a_repo_without_the_kit_is_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A consumer that never adopted the store has no ledger to race."""
    _lock(tmp_path, 4321)
    monkeypatch.setattr(hook, "_kit_events", lambda: None)
    assert hook.ledger_write_holder(tmp_path) is None


def test_the_refusal_names_the_stash_message_an_operator_will_have_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The message has to connect the two, or the operator diagnoses it wrongly again.

    It names the pid, quotes the git text they already saw, and says the commits are safe -
    the three facts the stash message withheld. It also must not run the checks: they would
    read the tree the landing is writing.
    """
    monkeypatch.setattr(hook, "project_root", lambda: tmp_path)
    monkeypatch.setattr(hook, "ledger_write_holder", lambda _root: 4321)
    monkeypatch.setattr(hook, "run_checks", lambda *_a, **_k: pytest.fail("ran the checks"))

    assert hook.main() == 1

    reported = capsys.readouterr().err
    assert "pid 4321" in reported
    assert "Stashed changes conflicted with hook auto-fixes" in reported
    assert "commits are unaffected" in reported


def test_a_quiet_tree_runs_the_checks_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second acceptance criterion: no lock, no change in behaviour."""
    ran: list[tuple[Path, str]] = []
    monkeypatch.setattr(hook, "project_root", lambda: tmp_path)
    monkeypatch.setattr(hook, "ledger_write_holder", lambda _root: None)
    monkeypatch.setattr(hook, "run_checks", lambda root, mode: ran.append((root, mode)) or 0)

    assert hook.main() == 0
    assert ran == [(tmp_path, "full")]


# --- the stash window itself (basicly-6ajmrc) -------------------------------------------
#
# The refusal above narrows the window; it does not close it, because `pre-commit` enters
# `staged_files_only` *before* any hook of the stage runs. These cases drive the real git
# hook file end to end: a repo dirtied only in `.basicly/ledger/`, the hook killed while it
# runs, and the ledger read back. The unguarded control is not decoration - without it a
# green guarded case is equally consistent with a kill that never landed inside the window.

LEDGER_FILE = Path(".basicly") / "ledger" / "events-0001.jsonl"
LIVE_APPEND = b'{"seq": 2, "body": "appended while the push ran"}\n'

posix_hooks = pytest.mark.skipif(
    sys.platform == "win32",
    reason="kills the hook by process group, which is POSIX-only; the guard itself is shell",
)


def _git(repo: Path, *args: str) -> None:
    """Run git in *repo*, failing the test on a non-zero exit."""
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


def _pushable_repo(tmp_path: Path) -> Path:
    """A repo whose installed pre-push hook sleeps, with only the ledger left unstaged.

    Two commits, because a push carrying the *root* commit takes pre-commit's `all_files`
    branch and never stashes - a one-commit fixture would pass without a guard.
    """
    repo = tmp_path / "work"
    (repo / LEDGER_FILE.parent).mkdir(parents=True)
    _git(repo.parent, "init", "-q", "-b", "main", repo.name)
    for key, value in (
        ("user.email", "t@example.invalid"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(repo, "config", key, value)
    (repo / LEDGER_FILE).write_bytes(b'{"seq": 1}\n')
    (repo / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: local\n  hooks:\n  - id: slow\n    name: slow\n"
        f"    entry: {shlex.quote(sys.executable)} {(tmp_path / 'slow.py').as_posix()}\n"
        "    language: system\n    stages: [pre-push]\n"
        "    always_run: true\n    pass_filenames: false\n",
        encoding="utf-8",
    )
    (tmp_path / "slow.py").write_text(
        f"import pathlib, time\npathlib.Path({str(tmp_path / 'started')!r}).write_text('go')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    (repo / "other.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "two")
    subprocess.run(
        (sys.executable, "-m", "pre_commit", "install", "--hook-type", "pre-push"),
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, "PRE_COMMIT_HOME": str(tmp_path / "pc-home")},
    )
    (repo / LEDGER_FILE).write_bytes(b'{"seq": 1}\n' + LIVE_APPEND)
    return repo


def _kill_the_hook_mid_run(repo: Path, tmp_path: Path) -> str:
    """Run the installed pre-push hook, SIGKILL it once it is running, return its output.

    The kill waits on the hook's own sentinel rather than a duration, so the process dies
    inside the window on a loaded machine too.
    """
    log = tmp_path / "hook.log"
    refs = subprocess.run(
        ("git", "rev-list", "--max-count=2", "HEAD"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    with log.open("wb") as sink:
        process = subprocess.Popen(
            [str(repo / ".git" / "hooks" / "pre-push"), "origin", str(tmp_path / "remote")],
            cwd=repo,
            stdin=subprocess.PIPE,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PRE_COMMIT_HOME": str(tmp_path / "pc-home")},
        )
    assert process.stdin is not None
    process.stdin.write(f"refs/heads/main {refs[0]} refs/heads/main {refs[1]}\n".encode())
    process.stdin.close()
    deadline = time.monotonic() + 60
    while not (tmp_path / "started").exists() and time.monotonic() < deadline:
        assert process.poll() is None, f"the hook exited before it ran: {log.read_text()}"
        time.sleep(0.05)
    assert (tmp_path / "started").exists(), "the pre-push hook never reached its slow hook"
    # The callers carry ``posix_hooks``; this narrows the same fact for the Windows type check.
    assert sys.platform != "win32"
    os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=30)
    return log.read_text(encoding="utf-8", errors="ignore")


@posix_hooks
def test_a_kill_mid_push_loses_the_ledger_without_the_guard(tmp_path: Path) -> None:
    """The control, and the defect as observed 2026-08-23: the live append is gone."""
    repo = _pushable_repo(tmp_path)

    output = _kill_the_hook_mid_run(repo, tmp_path)

    assert "Unstaged files detected" in output
    assert LIVE_APPEND not in (repo / LEDGER_FILE).read_bytes()


@posix_hooks
def test_a_kill_mid_push_leaves_a_ledger_only_tree_byte_identical(tmp_path: Path) -> None:
    """The acceptance criterion: ledger-only dirt is never stashed, so a kill costs nothing."""
    repo = _pushable_repo(tmp_path)
    before = (repo / LEDGER_FILE).read_bytes()
    assert hooks.apply_pre_push_guard(repo)

    output = _kill_the_hook_mid_run(repo, tmp_path)

    assert "Unstaged files detected" not in output
    assert (repo / LEDGER_FILE).read_bytes() == before


@posix_hooks
def test_dirt_outside_the_ledger_still_takes_the_stash(tmp_path: Path) -> None:
    """The other criterion: the guard is for ledger-only trees, and changes nothing else."""
    repo = _pushable_repo(tmp_path)
    (repo / "other.txt").write_text("unstaged\n", encoding="utf-8")
    assert hooks.apply_pre_push_guard(repo)

    output = _kill_the_hook_mid_run(repo, tmp_path)

    assert "Unstaged files detected" in output


def test_the_guard_refuses_a_hook_script_it_does_not_recognise(tmp_path: Path) -> None:
    """A mangled pre-push hook is a worse failure than the window it would close."""
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    foreign = tmp_path / ".git" / "hooks" / "pre-push"
    foreign.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    assert hooks.apply_pre_push_guard(tmp_path) is False
    assert foreign.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"


def test_a_pre_push_hook_without_the_guard_reads_as_not_installed(tmp_path: Path) -> None:
    """`pre-commit install` rewrites the file unconditionally; the drift has to surface."""
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    hook_file = tmp_path / ".git" / "hooks" / "pre-push"
    hook_file.write_text("# pre-commit\n", encoding="utf-8")

    assert hooks.missing_hook_installations(tmp_path, ["pre-push"]) == ["pre-push"]

    hook_file.write_text(f"# pre-commit\n# {hooks.PRE_PUSH_GUARD_MARKER}\n", encoding="utf-8")
    assert hooks.missing_hook_installations(tmp_path, ["pre-push"]) == []


def test_this_repo_ignores_the_ledger_lock_without_hiding_a_real_log() -> None:
    """The lock is transient, so only an ignore rule keeps it out of a cleanliness check.

    It is held for the width of one append and then unlinked, and no rule matched it: a
    `git status` sampled mid-write reported the base checkout dirty and refused the landing
    for a file that had already ceased to exist. The sweep was the worse half —
    `merge._commit_tracker_state` runs `git add .basicly/ledger`, and before the rule
    `git add -n` on that directory printed `add '.basicly/ledger/.events.lock'`, so a
    transient lock went into a `chore(beads)` commit (basicly-6mfhjp).

    Asked of git for `kit_deployment.py`'s reason: matching the glob by hand would
    reimplement git's precedence and be wrong where it matters. The second assertion is the
    control — a rule wide enough to catch the lock would also hide an event log, which is
    the truth the folds are derived from.
    """
    lock = f".basicly/ledger/{events.LOCK_NAME}"
    assert _ignored(lock), f"{lock} is unignored; a status sampled mid-append reads dirty"
    assert not _ignored(".basicly/ledger/events-0002.jsonl"), "the rule widened over the log"

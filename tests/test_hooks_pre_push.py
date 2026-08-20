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
import sys
from pathlib import Path
from types import ModuleType

import pytest

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

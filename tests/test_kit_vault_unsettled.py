from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from tests.conftest import VAULT_TEST_HOLDER as HOLDER
from tests.test_kit_vault_claim import _ask, vault_claim
from tests.test_kit_vault_share import _machine, _share, bare

__all__ = ["bare"]
KIT = Path(__file__).resolve().parents[1] / ".basicly/core/kit/tracker"
ID = "acme-a1b2"
queue = vault_claim.vault_queue
clone_api = vault_claim.vault_clone


def _pending(clone: Any) -> list[dict]:
    return queue.pending(clone.folder / queue.QUEUE_NAME)


def _claim(
    clone: Any, state: Path, record: str = ID, *, session: str = "s1", at: float | None = None
) -> dict:
    clock = time.time if at is None else lambda: at
    queue.write(
        clone,
        [{"kind": "claim", "record": record, "payload": {"name": HOLDER}}],
        state=state,
        origin=queue.Origin(session, False, clock),
    )
    return _pending(clone)[-1]


def _cli(
    root: Path, state: Path, bare: Path, *args: str, session: str = "s1"
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, BASICLY_TRACKER_SESSION=session, BASICLY_HOLDER=HOLDER)
    for name in ("CI", "BASICLY_TRACKER_EPHEMERAL"):
        env.pop(name, None)
    return subprocess.run(
        [sys.executable, str(KIT / "cli.py"), *args, "--vault", str(bare), "--state", str(state)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("landed", [False, True])
def test_cancel_removes_only_its_attempt_and_releases_any_settled_ownership(
    tmp_path: Path, bare: Path, landed: bool
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    attempt = _claim(clone, state)
    if landed:
        _share(clone, state)
        assert vault_claim.holder(clone, ID) == (queue.writer_of(state), "s1")
        queue.requeue(clone, [attempt])
    other = _claim(clone, state, "acme-other", session="s2")

    result = _cli(tmp_path, state, bare, "claim", ID, "--cancel")

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["record"] == ID
    clone_api.fetch(clone, clock=time.time)
    assert vault_claim.holder(clone, ID) is None
    assert attempt["id"] not in {line["id"] for line in _pending(clone)}
    assert other["id"] in clone_api.valid_ids(clone) | {line["id"] for line in _pending(clone)}
    if not landed:
        assert attempt["id"] not in clone_api.valid_ids(clone)


def test_wait_settles_the_existing_attempt_without_minting_another_claim(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    attempt = _claim(clone, state)

    result = _cli(tmp_path, state, bare, "claim", ID, "--wait")

    assert result.returncode == 0, result.stdout + result.stderr
    clone_api.fetch(clone, clock=time.time)
    assert vault_claim.holder(clone, ID) == (queue.writer_of(state), "s1")
    assert [
        line["id"]
        for line in clone_api.valid_events(clone)
        if line.get("kind") == "claim" and line.get("record") == ID
    ] == [attempt["id"]]
    assert not _pending(clone)


def test_wait_reports_the_competing_holder_and_retires_the_losing_attempt(
    tmp_path: Path, bare: Path
) -> None:
    winner, winner_state = _machine(tmp_path, bare, "winner")
    assert vault_claim.claim(_ask(winner, winner_state, "s1"))["exit"] == 0
    loser, state = _machine(tmp_path, bare, "loser")
    clone_api.fetch(loser, clock=time.time)
    attempt = _claim(loser, state, session="s2")

    result = _cli(tmp_path, state, bare, "claim", ID, "--wait", session="s2")

    assert result.returncode == 3, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["holder"]["writer"] == queue.writer_of(winner_state)
    assert attempt["id"] not in {line["id"] for line in _pending(loser)}
    assert vault_claim.holder(loser, ID) == (queue.writer_of(winner_state), "s1")


def test_cancel_cannot_report_success_or_change_the_queue_after_an_unanswered_fetch(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    _claim(clone, state)
    pending = clone.folder / queue.QUEUE_NAME
    original = pending.read_bytes()
    bare.rename(tmp_path / "unreachable.git")
    bare.mkdir()

    result = _cli(tmp_path, state, bare, "claim", ID, "--cancel")

    assert result.returncode == 5, result.stdout + result.stderr
    assert "fetch" in result.stdout
    assert pending.read_bytes() == original


def test_t_expiry_drops_an_old_unpublished_claim_and_unassigns_an_old_landed_one(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=lambda: 1000.0)
    landed = _claim(clone, state, "acme-landed", session="s2", at=1000.0)
    _share(clone, state, plan=vault_claim.vault_share.Plan(lambda: 1000.0))
    assert vault_claim.holder(clone, "acme-landed") == (queue.writer_of(state), "s2")
    queue.requeue(clone, [landed])
    unpublished = _claim(clone, state, at=1000.0)
    recent = _claim(clone, state, "acme-recent", session="s3", at=1600.0)

    clone_api.fetch(clone, clock=lambda: 1600.0)

    assert vault_claim.holder(clone, "acme-landed") is None
    assert vault_claim.holder(clone, ID) is None
    held = {line["id"] for line in _pending(clone)}
    assert landed["id"] not in held and unpublished["id"] not in held
    assert unpublished["id"] not in clone_api.valid_ids(clone)
    assert recent["id"] in clone_api.valid_ids(clone) | held


@pytest.mark.parametrize("command", ["prompt", "status", "commit-msg"])
def test_next_hook_or_command_retries_claim_before_the_bulk_slot(
    tmp_path: Path, bare: Path, command: str
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    attempt = _claim(clone, state)
    (clone.folder / vault_claim.vault_share.SHARED_AT).write_text(str(time.time()))

    args = ("hook", "prompt") if command == "prompt" else ("status",)
    if command == "commit-msg":
        message = tmp_path / "COMMIT_EDITMSG"
        message.write_text(f"fix: retry the claim ({ID})\n", encoding="utf-8")
        args = ("commit-msg", str(message))
    result = _cli(tmp_path, state, bare, *args)

    assert result.returncode == 0, result.stdout + result.stderr
    clone_api.fetch(clone, clock=time.time)
    assert attempt["id"] in clone_api.valid_ids(clone)
    assert vault_claim.holder(clone, ID) == (queue.writer_of(state), "s1")


def test_reading_the_pending_view_retries_claim_before_the_bulk_slot(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    attempt = _claim(clone, state)
    (clone.folder / vault_claim.vault_share.SHARED_AT).write_text(str(time.time()))
    pending = vault_claim._load("vault_pending.py", "basicly_tracker_kit_vault_pending")

    pending.present(clone, pending.vault_view.Want("acme"), clock=time.time)

    assert attempt["id"] in clone_api.valid_ids(clone)
    assert vault_claim.holder(clone, ID) == (queue.writer_of(state), "s1")


@pytest.mark.parametrize("age,expired", [(599.99, False), (600.0, True)])
def test_expiry_boundary_preserves_younger_attempts(
    tmp_path: Path, bare: Path, age: float, expired: bool
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=lambda: 1000.0)
    attempt = _claim(clone, state, at=1000.0)

    shown = clone_api.fetch(clone, clock=lambda: 1000.0 + age)

    assert (attempt["id"] in shown.get("expired", [])) is expired
    assert (attempt["id"] in {line["id"] for line in _pending(clone)}) is not expired
    assert attempt["id"] not in clone_api.valid_ids(clone)


def test_expiring_a_losing_attempt_preserves_the_foreign_holder(tmp_path: Path, bare: Path) -> None:
    winner, winner_state = _machine(tmp_path, bare, "winner")
    assert vault_claim.claim(_ask(winner, winner_state, "s1"))["exit"] == 0
    loser, state = _machine(tmp_path, bare, "loser")
    clone_api.fetch(loser, clock=lambda: 1000.0)
    attempt = _claim(loser, state, session="s2", at=1000.0)

    shown = clone_api.fetch(loser, clock=lambda: 1600.0)

    assert attempt["id"] in shown["expired"]
    assert vault_claim.holder(loser, ID) == (queue.writer_of(winner_state), "s1")
    assert attempt["id"] not in {line["id"] for line in _pending(loser)}


@pytest.mark.parametrize(
    "bad", ["{broken", '{"kind":"claim","at":NaN}', '{"kind":"claim","at":true}']
)
def test_cancel_preserves_a_malformed_queue_and_names_the_repair(
    tmp_path: Path, bare: Path, bad: str
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    _claim(clone, state)
    path = clone.folder / queue.QUEUE_NAME
    original = path.read_bytes() + (bad + "\n").encode()
    path.write_bytes(original)

    result = _cli(tmp_path, state, bare, "claim", ID, "--cancel")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "fsck --repair" in result.stdout
    assert path.read_bytes() == original


def test_cancel_honors_its_deadline_while_the_queue_lock_is_held(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    _claim(clone, state)
    path = clone.folder / queue.QUEUE_NAME
    original = path.read_bytes()

    with vault_claim.events.LedgerLock(clone.folder):
        started = time.monotonic()
        result = _cli(tmp_path, state, bare, "claim", ID, "--cancel", "--deadline", "0.1")
        elapsed = time.monotonic() - started

    assert result.returncode == 4, result.stdout + result.stderr
    assert elapsed < 2.0
    assert path.read_bytes() == original


def test_cancellation_cannot_be_undone_by_a_previously_built_delayed_share(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    attempt = _claim(clone, state)
    built, resume = Event(), Event()
    first = True

    def delayed(args: list[str], *values: Any, **options: Any) -> str:
        nonlocal first
        if "push" in args and first:
            first = False
            built.set()
            assert resume.wait(10.0)
        return vault_claim.git_runner.run(args, *values, **options)

    with ThreadPoolExecutor(max_workers=1) as pool:
        publishing = pool.submit(
            vault_claim.vault_share.share,
            clone,
            state,
            plan=vault_claim.vault_share.Plan(time.time, urgent=True, unlocked=True),
            runner=delayed,
        )
        try:
            assert built.wait(10.0)
            cancelled = _cli(tmp_path, state, bare, "claim", ID, "--cancel")
            assert cancelled.returncode == 0, cancelled.stdout + cancelled.stderr
        finally:
            resume.set()
        publishing.result(timeout=15.0)

    clone_api.fetch(clone, clock=time.time)
    assert vault_claim.holder(clone, ID) is None
    assert attempt["id"] not in clone_api.valid_ids(clone)
    assert not _pending(clone)


def test_repeating_a_normal_claim_reuses_its_existing_unsettled_attempt(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    attempt = _claim(clone, state)

    result = _cli(tmp_path, state, bare, "claim", ID)

    assert result.returncode == 0, result.stdout + result.stderr
    clone_api.fetch(clone, clock=time.time)
    assert [
        line["id"]
        for line in clone_api.valid_events(clone)
        if line.get("kind") == "claim" and line.get("record") == ID
    ] == [attempt["id"]]


def test_wait_retries_unanswered_fetches_until_the_attempt_really_settles(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    attempt = _claim(clone, state)
    failures = 0

    def temporary_outage(args: list[str], *values: Any, **options: Any) -> str:
        nonlocal failures
        if args[0] == "fetch" and failures < 2:
            failures += 1
            raise vault_claim.git_runner.GitTimeoutError("fixture fetch unanswered")
        return vault_claim.git_runner.run(args, *values, **options)

    settlement = vault_claim._load("vault_unsettled.py", "basicly_tracker_kit_vault_unsettled")
    shown = settlement.wait(_ask(clone, state, "s1", runner=temporary_outage))

    assert failures == 2
    assert shown["exit"] == 0
    assert vault_claim.holder(clone, ID) == (queue.writer_of(state), "s1")
    assert attempt["id"] in clone_api.valid_ids(clone)


def test_an_unfinished_cancellation_is_retried_by_the_next_status_command(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    assert vault_claim.claim(_ask(clone, state, "s1"))["exit"] == 0

    def refused(args: list[str], *values: Any, **options: Any) -> str:
        if "push" in args:
            raise vault_claim.git_runner.GitCommandError("push", 1, "fixture refusal")
        return vault_claim.git_runner.run(args, *values, **options)

    settlement = vault_claim._load("vault_unsettled.py", "basicly_tracker_kit_vault_unsettled")
    shown = settlement.cancel(_ask(clone, state, "s1", runner=refused), deadline=0.1)
    assert shown["exit"] == 4
    assert vault_claim.holder(clone, ID) == (queue.writer_of(state), "s1")
    assert any(line.get("kind") == "unassign" for line in _pending(clone))

    result = _cli(tmp_path, state, bare, "status")

    assert result.returncode == 0, result.stdout + result.stderr
    clone_api.fetch(clone, clock=time.time)
    assert vault_claim.holder(clone, ID) is None
    assert not _pending(clone)


def test_cancel_preserves_a_malformed_release_intent_instead_of_publishing_it(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    _claim(clone, state)
    queue.write(
        clone,
        [{"kind": "unassign", "record": ID, "payload": {"cancelled_claim": []}}],
        state=state,
        origin=queue.Origin("s1", False, time.time),
    )
    path = clone.folder / queue.QUEUE_NAME
    original = path.read_bytes()
    before = clone_api.valid_ids(clone)

    result = _cli(tmp_path, state, bare, "claim", ID, "--cancel")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "fsck --repair" in result.stdout
    assert path.read_bytes() == original
    assert clone_api.valid_ids(clone) == before

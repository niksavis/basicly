"""``basicly tracker write`` — a hand-authored write, through the engine seam.

Driven through :func:`basicly.cli.main` over a real ledger, because the whole reason this
command exists is that a human editing the log directly appends events nothing validated,
against a store with no undo. What is asserted is that the edit *lands the way an engine
edit does*: the same translation, the same refusals, the same read-only guard.

A spawn fails the test.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from basicly import cli, policy, tracker
from tests import flipped_tracker

ROOT = "tw-1"


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any process start is the test's failure rather than a fallback."""

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"a tracker write spawned a process: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout holding one open root, with a declared id prefix for a root mint."""
    root = flipped_tracker.flipped_repo(tmp_path)
    (root / "basicly.toml").write_text(
        '[tracker]\nmode = "owned"\nprefix = "tw"\n', encoding="utf-8"
    )
    flipped_tracker.seed(root, ROOT, title="the root")
    monkeypatch.chdir(root)
    return root


def test_a_field_write_lands_and_is_readable_back(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ordinary case, read back through the seam rather than off the log."""
    assert cli.main(["tracker", "write", "--", "update", ROOT, "-p", "1"]) == 0

    assert "recorded" in capsys.readouterr().out
    assert (tracker.read_record(repo, ROOT) or {})["priority"] == 1


@pytest.mark.usefixtures("repo")
def test_a_create_prints_json_only_when_the_caller_asked_for_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Printing prose to a caller that passed ``--json`` minted a duplicate record.

    The id was piped through ``jq``, vanished, and the create was re-run
    (basicly-vkh0.42.10) — so the two output shapes are pinned together here.
    """
    argv = ["tracker", "write", "--", "create", "a child", "-t", "task", "--parent", ROOT]

    assert cli.main([*argv, "--json"]) == 0
    minted = json.loads(capsys.readouterr().out)["id"]
    assert minted == f"{ROOT}.1"

    assert cli.main(argv) == 0
    prose = capsys.readouterr().out
    assert prose.startswith("created: ")
    assert not prose.lstrip().startswith("{")


def test_a_close_prints_what_the_record_asked_for_before_claiming_it(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not a refusal: a human may close for reasons the criteria never covered.

    It is the closer seeing the list — `basicly-agzx.4` was closed with three of four
    criteria met and the fourth needing a file another lane held, and nothing said so.
    """
    cli.main([
        "tracker",
        "write",
        "--",
        "update",
        ROOT,
        "--acceptance-criteria",
        "Given a backlog when it is read then it is ordered",
    ])
    capsys.readouterr()

    assert cli.main(["tracker", "write", "--", "close", ROOT, "--reason", "shipped"]) == 0

    out = capsys.readouterr().out
    assert "which asked for" in out
    assert "then it is ordered" in out
    assert (tracker.read_record(repo, ROOT) or {})["status"] == "closed"


def test_a_label_write_accumulates_and_the_lane_query_finds_it(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The write `loop supervise --label` had no way to be set up without (basicly-wpc8)."""
    assert cli.main(["tracker", "write", "--", "update", ROOT, "--add-label", "cut-a"]) == 0
    assert cli.main(["tracker", "write", "--", "update", ROOT, "--add-label", "cut-b"]) == 0
    capsys.readouterr()

    assert (tracker.read_record(repo, ROOT) or {})["labels"] == ["cut-a", "cut-b"]

    assert cli.main(["tracker", "write", "--", "update", ROOT, "--remove-label", "cut-a"]) == 0

    assert (tracker.read_record(repo, ROOT) or {})["labels"] == ["cut-b"]


@pytest.mark.usefixtures("repo")
def test_naming_no_subcommand_refuses_and_says_what_one_looks_like(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An empty argv is an operator error, not an empty write."""
    assert cli.main(["tracker", "write", "--"]) == 2

    assert "name a subcommand" in capsys.readouterr().out


@pytest.mark.usefixtures("repo")
def test_a_write_with_no_translation_stops_rather_than_landing_half(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A surface nobody translated is a dependency somebody took without deciding to."""
    assert cli.main(["tracker", "write", "--", "reopen", ROOT]) != 0

    # The refused verb and the accepted set both, so the message tells a caller what to
    # run instead rather than only that it stopped.
    refusal = capsys.readouterr().err
    assert "'reopen'" in refusal
    assert "comments add" in refusal


def test_the_read_only_guard_binds_this_surface_too(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A gate that promised to write nothing must be refused here as well.

    The seam is the same one every engine write passes, which is the whole point of
    routing a human's edit through it rather than letting them append by hand.
    """
    with policy.preflight_gate(policy.DOR_GATE):
        assert cli.main(["tracker", "write", "--", "close", ROOT, "--reason", "no"]) != 0

    err = capsys.readouterr().err
    assert policy.DOR_GATE in err
    assert "close writes" in err
    assert (tracker.read_record(repo, ROOT) or {})["status"] == "open"


def test_a_field_driven_a_then_b_then_a_records_the_third_write(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A value returning to one it held is a fact about now, not a replay (basicly-bj8kks).

    An event id is a digest over the fact, so driving one field to A, to B and back to A
    re-minted the first A's id and the append skipped it: three commands reported
    ``recorded:`` and the field never moved (basicly-kn4rip), and after that seam learned to
    say so, the label simply stayed off. The record moved between the two A's, so the second
    one is new however familiar its content.
    """
    assert cli.main(["tracker", "write", "--", "update", ROOT, "--add-label", "live-demo"]) == 0
    assert cli.main(["tracker", "write", "--", "update", ROOT, "--remove-label", "live-demo"]) == 0
    capsys.readouterr()

    assert cli.main(["tracker", "write", "--", "update", ROOT, "--add-label", "live-demo"]) == 0

    assert "recorded:" in capsys.readouterr().out
    # The claim and the ledger agree: the seam says it landed, and the record reads it.
    assert (tracker.read_record(repo, ROOT) or {})["labels"] == ["live-demo"]


def test_a_status_the_record_left_is_written_again_and_reads_back(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reported case, on a record minted the way the store mints one (basicly-bj8kks).

    ``--status open`` after ``deferred`` re-mints the id of the ``open`` event every create
    writes, so the write was swallowed and ``tracker show`` kept ``deferred``. The record is
    created here rather than seeded because only the create path stamps the same provenance
    the later write does — a seeded status event carries none, and the two would not collide.
    """
    argv = ["tracker", "write", "--", "create", "a child", "-t", "task", "--parent", ROOT]
    assert cli.main([*argv, "--json"]) == 0
    minted = json.loads(capsys.readouterr().out)["id"]

    assert cli.main(["tracker", "write", "--", "update", minted, "--status", "deferred"]) == 0
    assert (tracker.read_record(repo, minted) or {})["status"] == "deferred"
    capsys.readouterr()

    assert cli.main(["tracker", "write", "--", "update", minted, "--status", "open"]) == 0

    assert "recorded:" in capsys.readouterr().out
    assert (tracker.read_record(repo, minted) or {})["status"] == "open"


def test_replaying_a_verb_that_writes_two_events_is_still_one_replay(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A repeat is judged over the whole write, not over its last event (basicly-bj8kks).

    ``close --reason`` states two facts, the reason field and the status. Comparing only the
    newest event would read the reason as new the second time — the status sits above it —
    and append a duplicate beside a status the ledger rightly skipped.
    """
    argv = ["tracker", "write", "--", "close", ROOT, "--reason", "done"]
    assert cli.main(argv) == 0
    capsys.readouterr()
    before = len(flipped_tracker.ledger_events(repo))

    assert cli.main(argv) == 1

    assert "already recorded" in capsys.readouterr().out
    assert len(flipped_tracker.ledger_events(repo)) == before


def test_a_write_stating_two_facts_the_record_already_reads_appends_nothing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing has happened since those facts landed, whatever order they landed in.

    The ledger holds them in the order they *first* landed, so a pairwise comparison against
    the tail read this pair as new and appended both again (basicly-bj8kks).
    """
    assert cli.main(["tracker", "write", "--", "update", ROOT, "--status", "open"]) == 0
    assert cli.main(["tracker", "write", "--", "update", ROOT, "-p", "2"]) == 0
    capsys.readouterr()
    before = len(flipped_tracker.ledger_events(repo))

    argv = ["tracker", "write", "--", "update", ROOT, "-p", "2", "--status", "open"]
    assert cli.main(argv) == 1

    assert "already recorded" in capsys.readouterr().out
    assert len(flipped_tracker.ledger_events(repo)) == before


def test_replaying_one_identical_write_is_reported_and_exits_non_zero(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: a true replay still appends once and still changes nothing.

    Idempotent replay is the property the content digest exists for, so the second command
    must not append a second event. It exits non-zero rather than 0 because a script reads
    the status and not the prose, and a skipped write it read as success is the failure
    `basicly-kn4rip` recorded from the other side.
    """
    assert cli.main(["tracker", "write", "--", "update", ROOT, "-p", "2"]) == 0
    assert "recorded:" in capsys.readouterr().out

    assert cli.main(["tracker", "write", "--", "update", ROOT, "-p", "2"]) == 1

    assert "already recorded" in capsys.readouterr().out
    assert (tracker.read_record(repo, ROOT) or {})["priority"] == 2


def _hold_the_ledger_lock(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave the ledger locked by a live holder, and stop the seam waiting five seconds.

    Our own pid, because the lock frees one whose holder is known dead. The timeout is
    injected through the lock the seam constructs rather than through the module constant,
    which is bound as a default at definition time and cannot be patched afterwards.
    """
    kit = tracker.kit(repo)
    real = kit.events.LedgerLock

    def _no_wait(directory: Path, **_kwargs: object) -> object:
        return real(directory, timeout_s=0.0)

    monkeypatch.setattr(kit.events, "LedgerLock", _no_wait)
    (tracker.ledger_dir(repo) / kit.events.LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid(), "monotonic": time.monotonic()}), encoding="utf-8"
    )


def test_a_landed_write_names_the_fact_it_appended_rather_than_the_argv(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`recorded: <argv>` echoed the caller's own words back, which is not evidence.

    The flag in the message is the one the ledger's field maps to, so it says what is now
    stored rather than what was typed — here `-p 1` read back as `--priority=1`.
    """
    assert cli.main(["tracker", "write", "--", "update", ROOT, "-p", "1"]) == 0

    out = capsys.readouterr().out
    assert f"recorded: {ROOT} --priority=1" in out
    assert (tracker.read_record(repo, ROOT) or {})["priority"] == 1


def test_a_write_the_store_does_not_keep_is_reported_as_not_recorded(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The measured incident: the append returns, and the log holds none of it.

    Twenty consecutive writes printed success over a store that kept nothing, because a
    concurrent process was restoring the log's bytes (basicly-vkh0.51). That restore is
    what is reproduced here — the seam answers exactly as it did then, and the command
    must not call it recorded.
    """
    kit = tracker.kit(repo)
    log = kit.events.append_target(tracker.ledger_dir(repo))
    appended = kit.events.append

    def _append_then_lose_it(*args: object, **kwargs: object) -> object:
        held = log.read_bytes()
        landed = appended(*args, **kwargs)
        log.write_bytes(held)
        return landed

    monkeypatch.setattr(kit.events, "append", _append_then_lose_it)

    assert cli.main(["tracker", "write", "--", "update", ROOT, "--notes", "probe"]) != 0

    err = capsys.readouterr().err
    assert "not recorded" in err
    assert f"{ROOT} --notes=probe" in err
    assert (tracker.read_record(repo, ROOT) or {}).get("notes") is None


def test_a_write_that_cannot_take_the_lock_says_so_and_that_it_may_be_retried(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lane holding the lock is contention, not a failed write: appended or refused.

    The retry line is bound to the ledger's own ``retryable`` contract rather than to the
    message text, so a failure that retrying cannot fix does not get it.
    """
    _hold_the_ledger_lock(repo, monkeypatch)

    assert cli.main(["tracker", "write", "--", "update", ROOT, "--notes", "probe"]) == 1

    err = capsys.readouterr().err
    assert "not recorded" in err
    assert "running it again is safe" in err
    assert (tracker.read_record(repo, ROOT) or {}).get("notes") is None


def test_a_write_landing_beside_a_fact_the_ledger_held_reports_both(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A partial landing is neither `recorded: <argv>` nor `nothing was appended`.

    One flag of the two was a replay, so echoing the argv would claim the title moved and
    the `already recorded` line would deny the notes ever landed.
    """
    assert cli.main(["tracker", "write", "--", "update", ROOT, "--title", "one"]) == 0
    capsys.readouterr()

    argv = ["tracker", "write", "--", "update", ROOT, "--title", "one", "--notes", "n"]
    assert cli.main(argv) == 0

    out = capsys.readouterr().out
    assert f"recorded: {ROOT} --notes=n" in out
    assert "and 1 fact(s) the ledger already held" in out
    assert (tracker.read_record(repo, ROOT) or {})["notes"] == "n"

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

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"a tracker write spawned a process: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    assert cli.main(["tracker", "write", "--", "update", ROOT, "-p", "1"]) == 0

    assert "recorded" in capsys.readouterr().out
    assert (tracker.read_record(repo, ROOT) or {})["priority"] == 1


@pytest.mark.usefixtures("repo")
def test_a_create_prints_json_only_when_the_caller_asked_for_it(
    capsys: pytest.CaptureFixture[str],
) -> None:

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
    assert cli.main(["tracker", "write", "--"]) == 2

    assert "name a subcommand" in capsys.readouterr().out


@pytest.mark.usefixtures("repo")
def test_a_write_with_no_translation_stops_rather_than_landing_half(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["tracker", "write", "--", "reopen", ROOT]) != 0

    refusal = capsys.readouterr().err
    assert "'reopen'" in refusal
    assert "comments add" in refusal


def test_the_read_only_guard_binds_this_surface_too(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    with policy.preflight_gate(policy.DOR_GATE):
        assert cli.main(["tracker", "write", "--", "close", ROOT, "--reason", "no"]) != 0

    err = capsys.readouterr().err
    assert policy.DOR_GATE in err
    assert "close writes" in err
    assert (tracker.read_record(repo, ROOT) or {})["status"] == "open"


def test_a_field_driven_a_then_b_then_a_records_the_third_write(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    assert cli.main(["tracker", "write", "--", "update", ROOT, "--add-label", "live-demo"]) == 0
    assert cli.main(["tracker", "write", "--", "update", ROOT, "--remove-label", "live-demo"]) == 0
    capsys.readouterr()

    assert cli.main(["tracker", "write", "--", "update", ROOT, "--add-label", "live-demo"]) == 0

    assert "recorded:" in capsys.readouterr().out
    assert (tracker.read_record(repo, ROOT) or {})["labels"] == ["live-demo"]


def test_a_status_the_record_left_is_written_again_and_reads_back(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

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

    assert cli.main(["tracker", "write", "--", "update", ROOT, "-p", "2"]) == 0
    assert "recorded:" in capsys.readouterr().out

    assert cli.main(["tracker", "write", "--", "update", ROOT, "-p", "2"]) == 1

    assert "already recorded" in capsys.readouterr().out
    assert (tracker.read_record(repo, ROOT) or {})["priority"] == 2


def _hold_the_ledger_lock(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:

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

    assert cli.main(["tracker", "write", "--", "update", ROOT, "-p", "1"]) == 0

    out = capsys.readouterr().out
    assert f"recorded: {ROOT} --priority=1" in out
    assert (tracker.read_record(repo, ROOT) or {})["priority"] == 1


def test_a_write_the_store_does_not_keep_is_reported_as_not_recorded(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:

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

    _hold_the_ledger_lock(repo, monkeypatch)

    assert cli.main(["tracker", "write", "--", "update", ROOT, "--notes", "probe"]) == 1

    err = capsys.readouterr().err
    assert "not recorded" in err
    assert "running it again is safe" in err
    assert (tracker.read_record(repo, ROOT) or {}).get("notes") is None


def test_a_write_landing_beside_a_fact_the_ledger_held_reports_both(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:

    assert cli.main(["tracker", "write", "--", "update", ROOT, "--title", "one"]) == 0
    capsys.readouterr()

    argv = ["tracker", "write", "--", "update", ROOT, "--title", "one", "--notes", "n"]
    assert cli.main(argv) == 0

    out = capsys.readouterr().out
    assert f"recorded: {ROOT} --notes=n" in out
    assert "and 1 fact(s) the ledger already held" in out
    assert (tracker.read_record(repo, ROOT) or {})["notes"] == "n"

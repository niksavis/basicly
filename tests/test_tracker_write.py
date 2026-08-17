"""``basicly tracker write`` — a hand-authored write, through the engine seam.

Driven through :func:`basicly.cli.main` over a real ledger, because the whole reason this
command exists is that a human editing the log directly appends events nothing validated,
against a store with no undo. What is asserted is that the edit *lands the way an engine
edit does*: the same translation, the same refusals, the same read-only guard.

A spawn fails the test.
"""

from __future__ import annotations

import json
import subprocess
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

    assert "no owned-ledger translation" in capsys.readouterr().err


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

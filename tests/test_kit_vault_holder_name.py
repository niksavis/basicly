from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

from tests.test_kit_vault_claim import KIT, _ask, vault_claim
from tests.test_kit_vault_repair import _queued, _repair, _rewritten
from tests.test_kit_vault_share import _machine, _share, _write, bare
from tests.test_kit_vault_view import _carry, _push, _read, _record, _view

__all__ = ["bare"]
vault_queue = vault_claim.vault_queue
vault_fold = vault_claim.vault_fold
vault_clone = vault_claim.vault_clone
RECORD = "acme-a1b2"


def _claim_argv(bare: Path, state: Path, session: str) -> list[str]:
    return [
        sys.executable,
        str(KIT / "cli.py"),
        "claim",
        RECORD,
        "--vault",
        str(bare),
        "--state",
        str(state),
        "--session",
        session,
    ]


def test_a_vault_claim_carries_the_holder_name_and_the_fold_shows_it_beside_the_writer(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    landed = vault_claim.claim(_ask(clone, state, "s1", RECORD, name="alex kim"))
    folded = vault_fold.fold(vault_clone.valid_events(clone)).records[RECORD]

    assert landed["exit"] == vault_claim.LANDED
    assert vault_fold.view(folded)["holder"] == {
        "writer": vault_queue.writer_of(state),
        "session": "s1",
        "name": "alex kim",
    }


def test_two_sessions_with_one_holder_name_race_and_exactly_one_claim_lands(
    tmp_path: Path, bare: Path
) -> None:
    env = {**os.environ, "BASICLY_HOLDER": "alex kim"}
    procs = [
        subprocess.Popen(
            _claim_argv(bare, tmp_path / "machines" / f"m{n}", f"s{n}"),
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        for n in (1, 2)
    ]
    shown = [json.loads(proc.communicate(timeout=120)[0]) for proc in procs]

    assert Counter(proc.returncode for proc in procs) == Counter({0: 1, 3: 1})
    (lost,) = [
        one for one, proc in zip(shown, procs, strict=True) if proc.returncode == vault_claim.LOST
    ]
    assert lost["holder"]["name"] == "alex kim"


def test_a_claim_without_git_config_basicly_holder_refuses_and_prints_the_command(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("BASICLY_HOLDER")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "alex kim")
    proc = subprocess.run(
        _claim_argv(bare, tmp_path / "machines" / "m", "s1"),
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )

    assert proc.returncode == vault_claim.FIX
    assert "`git config basicly.holder 'your name'`" in json.loads(proc.stdout)["fix"]


def test_a_path_or_secret_shaped_holder_name_is_refused_before_anything_is_queued(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    shown = vault_claim.claim(_ask(clone, state, "s1", RECORD, name="/home/alex/work"))

    assert shown["exit"] == vault_claim.FIX
    assert "basicly.holder" in shown["fix"]
    assert not (clone.folder / vault_queue.QUEUE_NAME).exists()


def test_the_queue_redaction_exempts_exactly_the_holder_name_of_a_claim(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    shaped = "/home/alex/work"
    drafts = [
        {"kind": "claim", "record": RECORD, "payload": {"name": shaped, "note": shaped}},
        {"kind": "comment", "record": RECORD, "payload": {"name": shaped}},
    ]
    origin = vault_queue.Origin("s1", False, time.time)

    vault_queue.write(clone, drafts, state=state, origin=origin)
    claimed, commented = vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME)

    assert claimed["payload"]["name"] == shaped
    assert claimed["payload"]["note"] != shaped
    assert commented["payload"]["name"] != shaped


def test_the_view_writes_the_holder_name_as_the_assignee_and_vault_show_keeps_the_writer(
    tmp_path: Path, bare: Path
) -> None:
    held = {"kind": "claim", "record": "acme-a1", "payload": {"name": "alex kim"}}
    writer = _push(tmp_path, bare, "m1", [*_carry(_record("acme-a1")), held])

    _reader, folder = _view(tmp_path, bare)
    assignees = [
        event.payload["value"]
        for event in _read(folder)
        if event.kind == "field" and event.payload.get("name") == "assignee"
    ]
    clone, _state = _machine(tmp_path, bare, "reader")
    shown = vault_fold.view(vault_fold.fold(vault_clone.valid_events(clone)).records["acme-a1"])

    assert assignees == ["alex kim"]
    assert shown["holder"] == {"writer": writer, "session": "s1", "name": "alex kim"}


def test_fsck_repair_names_the_lost_claim_holder_beside_its_writer(
    tmp_path: Path, bare: Path
) -> None:
    case = _rewritten(tmp_path, bare)
    clone, state = case["clone"], case["state"]
    _write(clone, state, 1, "b")
    with pytest.raises(vault_claim.vault_share.ShareError):
        _share(clone, state)

    _repair(clone, state)
    (lost,) = [line for line in _queued(clone) if line["kind"] == "claim-lost"]

    assert lost["payload"]["writer"] == vault_queue.writer_of(case["w_state"])
    assert lost["payload"]["name"] == "alex kim"

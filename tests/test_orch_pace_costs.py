from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import time
from pathlib import Path

import pytest

from tests.test_orch_pace import (
    FIVE_RESET,
    PACKAGE,
    record,
    snapshot,
    tracker,
    window,
    write_snapshot,
)

costs = importlib.import_module("orch_pace.costs")
kit_note = importlib.import_module("orch_state.note")


class FakeList:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records

    def __call__(self, argv: list[str]) -> str:
        assert argv[2:] == ["list", argv[3], "--query", "[pace done]"]
        return json.dumps({"records": self.records})


def done_note(change: str, kind: str, by: str = " by=landing") -> str:
    return f"[pace done] five_hour=50.0 resets_at={FIVE_RESET} change={change} type={kind}{by}"


def test_orch_pace_sums_the_newest_cost_of_each_record_per_type(tmp_path: Path) -> None:
    records = [
        {"record": "a-1", "comments": [done_note("9.0", "task"), done_note("4.0", "task")]},
        {
            "record": "a-2",
            "comments": [{"text": done_note("6.0", "task", " working_sessions=3 by=claim")}],
        },
        {"record": "a-3", "comments": [done_note("unknown_crossed_reset", "bug")]},
        {"record": "a-4", "comments": [done_note("2.5", "bug", " by=manual")]},
        {"record": "a-5", "comments": [done_note("0.0", "task", "")]},
    ]
    shown = costs.lines(tmp_path, tracker(tmp_path, FakeList(records)))
    assert shown == [
        "cost per record type (five_hour percent per delivered record):",
        costs.HEADER,
        f"{'bug':<12} {2:>7} {'2.5':>7} {'2.5':>6} {1:>13}",
        f"{'task':<12} {2:>7} {'5.0':>7} {'6.0':>6} {0:>13}",
    ]
    assert costs.lines(tmp_path, tracker(tmp_path, FakeList([]))) == [
        "cost per record type: no [pace done] note with by= yet"
    ]


FAKE_CLI = """import json, sys
from pathlib import Path
log = Path(sys.argv[0]).with_name("calls.jsonl")
if sys.argv[1] == "fail":
    raise SystemExit(1)
if sys.argv[1] == "comment":
    with log.open("a", encoding="utf-8") as out:
        out.write(json.dumps(sys.argv[1:]) + "\\n")
    print("{}")
else:
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    notes = [{"text": json.loads(line)[-1]} for line in lines]
    print(json.dumps({"comment_log": notes, "fields": {"issue_type": "task"}}))
"""


def vault_repo(tmp_path: Path, used: float, vault: bool = True) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    if vault:
        (repo / ".basicly-tracker.toml").write_text("", encoding="utf-8")
    cli = repo / record.TRACKER_CLI
    cli.parent.mkdir(parents=True)
    cli.write_text(FAKE_CLI, encoding="utf-8")
    now = int(time.time())
    write_snapshot(tmp_path / "snaps" / "statusline.json", window(used, [[now, used]], now + 3600))
    (tmp_path / "store").mkdir()
    env = {
        snapshot.DIR_ENV: str(tmp_path / "snaps"),
        "ORCH_STATE_ROOT": str(tmp_path / "store"),
        "ORCH_SESSION_ID": "s1",
    }
    return repo, env


def comments(repo: Path) -> list[str]:
    log_file = repo / record.TRACKER_CLI.with_name("calls.jsonl")
    if not log_file.exists():
        return []
    return [json.loads(line)[-1] for line in log_file.read_text(encoding="utf-8").splitlines()]


def test_the_claim_and_the_landing_note_the_usage_on_the_record(tmp_path: Path) -> None:
    repo, env = vault_repo(tmp_path, 20.0)
    assert kit_note.pace(repo, env, "acme-1", "start")
    write_snapshot(
        tmp_path / "snaps" / "statusline.json",
        window(27.5, [[int(time.time()), 27.5]], int(time.time()) + 3600),
    )
    assert kit_note.pace(repo, env, "acme-1", "done")
    noted = comments(repo)
    assert noted[0].startswith("[pace start] five_hour=20.0 ")
    assert noted[0].endswith(" working_sessions=0 by=claim")
    assert " change=7.5 type=task " in noted[1]
    assert noted[1].endswith(" by=landing")


@pytest.mark.parametrize("case", ["ledger-mode", "stale", "tracker-fails", "no-store"])
def test_a_pace_note_that_cannot_be_written_is_skipped_without_an_error(
    tmp_path: Path, case: str
) -> None:
    repo, env = vault_repo(tmp_path, 20.0, vault=case != "ledger-mode")
    if case == "stale":
        old = int(time.time()) - 900
        write_snapshot(
            tmp_path / "snaps" / "statusline.json", window(20.0, [[old, 20.0]], old + 3600)
        )
    if case == "tracker-fails":
        (repo / record.TRACKER_CLI).write_text("raise SystemExit(1)\n", encoding="utf-8")
    if case == "no-store":
        del env["ORCH_STATE_ROOT"]
    assert kit_note.pace(repo, env, "acme-1", "start") is False
    assert comments(repo) == []


def test_a_landed_vault_claim_asks_for_the_start_pace_note(monkeypatch: pytest.MonkeyPatch) -> None:
    kit = PACKAGE.parent / "tracker" / "vault_cli.py"
    spec = importlib.util.spec_from_file_location("basicly_tracker_kit_vault_cli_pace", kit)
    assert spec is not None
    assert spec.loader is not None
    vault_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vault_cli)
    seen: list[tuple[str, dict[str, object]]] = []
    landed = {"exit": vault_cli.vault_claim.LANDED}
    monkeypatch.setattr(vault_cli, "_clone", lambda _args: Path("clone"))
    monkeypatch.setattr(vault_cli, "_state", lambda _args: Path("state"))
    monkeypatch.setattr(vault_cli.vault_claim, "claim", lambda *_a, **_k: landed)
    monkeypatch.setattr(vault_cli, "_orch_kit", lambda name, **fields: seen.append((name, fields)))
    args = argparse.Namespace(record="acme-5", session="s1", deadline=30.0, vault="v")
    assert vault_cli._claim_vault(args, time.time) == landed
    assert seen == [
        ("note", {"item": "acme-5", "last": "claimed acme-5"}),
        ("pace", {"record": "acme-5", "moment": "start"}),
    ]
